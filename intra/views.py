from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib.auth.views import PasswordResetConfirmView
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.utils.timezone import localtime
from django.utils.crypto import get_random_string
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth
from django.core.paginator import Paginator, EmptyPage, InvalidPage
from datetime import datetime, timedelta
import json
import html
import logging
import time
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.core.cache import cache

# Configurar logger para debug
logger = logging.getLogger(__name__)

from .forms import LoginForm, EsqueceuAcessoForm, CompletarCadastroForm, EditarPagamentoForm, TrocarSenhaForm
from .models import PerfilSolicitante, RegraUsuario, SolicitacaoReembolso, ItemReembolso, CentroCusto, HistoricoReembolso
from .pdf_reembolso import gerar_pdf
from .docusign_integration import enviar_documento_para_assinatura, baixar_pdf_assinado, consultar_status_envelope

User = get_user_model()


def medir_tempo(view_func):
    """Decorator para medir o tempo de execução de uma view."""
    def wrapper(request, *args, **kwargs):
        inicio = time.time()
        nome_view = view_func.__name__
        path = request.path
        
        try:
            response = view_func(request, *args, **kwargs)
            tempo_total = time.time() - inicio
            print(f"[TEMPO] {nome_view} | Path: {path} | Tempo: {tempo_total:.4f}s")
            return response
        except Exception as e:
            tempo_total = time.time() - inicio
            print(f"[TEMPO] {nome_view} | Path: {path} | Tempo: {tempo_total:.4f}s | ERRO: {str(e)}")
            raise
    return wrapper


def _is_gestor(user):
    """Usuário com regra Gestor Administrativo (Regras de usuários no Admin)."""
    return (
        user.is_authenticated
        and user.regras_usuario.filter(role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO).exists()
    )


def _is_gestor_simples(user):
    """Usuário com regra Gestor (não administrativo)."""
    return (
        user.is_authenticated
        and user.regras_usuario.filter(role=RegraUsuario.ROLE_GESTOR).exists()
    )


def _is_gestor_ou_gestor_admin(user):
    """Usuário com regra Gestor ou Gestor Administrativo."""
    return _is_gestor_simples(user) or _is_gestor(user)


def _get_status_assinatura_docusign(solicitacao, use_cache=True):
    """
    Obtém o status da assinatura no DocuSign e informações sobre quem está aguardando.
    Retorna dict com informações do status ou None se não houver envelope.
    Usa cache para evitar consultas desnecessárias ao DocuSign.
    """
    if not solicitacao.envelope_id_docusign:
        return None
    
    envelope_id = solicitacao.envelope_id_docusign
    
    # Verificar cache primeiro (TTL de 5 minutos)
    cache_key = f"docusign_status_{envelope_id}"
    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            # Retornar do cache, mas ainda verificar se precisa atualizar status no banco
            status_envelope = cached_result.get('status')
            if status_envelope and status_envelope != solicitacao.status_docusign:
                solicitacao.status_docusign = status_envelope
                if status_envelope.lower() in ['completed', 'signed']:
                    # Quando todas as partes assinarem, concluir automaticamente
                    if solicitacao.status != SolicitacaoReembolso.STATUS_CONCLUIDO:
                        solicitacao.status = SolicitacaoReembolso.STATUS_CONCLUIDO
                        solicitacao.concluido = True
                        if not solicitacao.concluido_em:
                            solicitacao.concluido_em = timezone.now()
                solicitacao.save(update_fields=['status_docusign', 'status', 'concluido', 'concluido_em'])
            return cached_result
    
    try:
        # Tentar consultar status atualizado do DocuSign
        resposta = consultar_status_envelope(envelope_id)
        status_envelope = resposta.get('status', solicitacao.status_docusign or 'sent')
        
        # Atualizar status no banco se mudou
        status_mudou = False
        if status_envelope != solicitacao.status_docusign:
            solicitacao.status_docusign = status_envelope
            status_mudou = True
        
        # Se todas as partes assinaram, concluir automaticamente
        if status_envelope.lower() in ['completed', 'signed']:
            if solicitacao.status != SolicitacaoReembolso.STATUS_CONCLUIDO:
                solicitacao.status = SolicitacaoReembolso.STATUS_CONCLUIDO
                solicitacao.concluido = True
                if not solicitacao.concluido_em:
                    solicitacao.concluido_em = timezone.now()
                status_mudou = True
        
        # Salvar se houve mudança
        if status_mudou:
            solicitacao.save(update_fields=['status_docusign', 'status', 'concluido', 'concluido_em'])
        
        # Obter informações dos signatários
        signers_info = []
        recipients = resposta.get('recipients', {})
        signers = recipients.get('signers', [])
        
        aguardando_assinatura = []
        for signer in signers:
            signer_status = signer.get('status', '').lower()
            signer_info = {
                'nome': signer.get('name', ''),
                'email': signer.get('email', ''),
                'status': signer_status,
                'routing_order': signer.get('routingOrder', '')
            }
            signers_info.append(signer_info)
            
            # Se não assinou, está aguardando
            if signer_status in ['sent', 'delivered', 'created']:
                aguardando_assinatura.append(signer_info)
        
        result = {
            'status': status_envelope,
            'status_legivel': _traduzir_status_docusign(status_envelope),
            'signers': signers_info,
            'aguardando_assinatura': aguardando_assinatura
        }
        
        # Salvar no cache por 5 minutos (300 segundos)
        cache.set(cache_key, result, 300)
        
        return result
    except Exception as e:
        logger.warning(f"Erro ao consultar status DocuSign: {e}")
        # Retornar status salvo no banco se houver
        if solicitacao.status_docusign:
            result = {
                'status': solicitacao.status_docusign,
                'status_legivel': _traduzir_status_docusign(solicitacao.status_docusign),
                'signers': [],
                'aguardando_assinatura': []
            }
            # Cachear mesmo em caso de erro (por menos tempo - 1 minuto)
            cache.set(cache_key, result, 60)
            return result
        return None


def _get_status_assinatura_docusign_parallel(solicitacoes):
    """
    Consulta status do DocuSign para múltiplas solicitações em paralelo.
    Retorna um dict mapeando pk da solicitação para o resultado.
    Para solicitações sem envelope_id ou já completadas, retorna None.
    """
    if not solicitacoes:
        return {}
    
    # Filtrar apenas solicitações que precisam ser consultadas
    solicitacoes_para_consultar = []
    resultados = {}
    
    for sol in solicitacoes:
        # Se não tem envelope_id, retornar None
        if not sol.envelope_id_docusign:
            resultados[sol.pk] = None
        # Se já está completed ou signed, não precisa consultar (mas retornar None para manter compatibilidade)
        elif sol.status_docusign and sol.status_docusign.lower() in ['completed', 'signed']:
            resultados[sol.pk] = None
        else:
            # Adicionar à lista para consultar
            solicitacoes_para_consultar.append(sol)
    
    if not solicitacoes_para_consultar:
        return resultados
    
    def consultar_uma(solicitacao):
        """Função auxiliar para consultar uma solicitação."""
        try:
            return solicitacao.pk, _get_status_assinatura_docusign(solicitacao, use_cache=True)
        except Exception as e:
            logger.warning(f"Erro ao consultar DocuSign para solicitação {solicitacao.pk}: {e}")
            return solicitacao.pk, None
    
    # Executar consultas em paralelo (máximo 10 threads simultâneas)
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_sol = {
            executor.submit(consultar_uma, sol): sol 
            for sol in solicitacoes_para_consultar
        }
        
        for future in as_completed(future_to_sol):
            try:
                pk, resultado = future.result()
                resultados[pk] = resultado
            except Exception as e:
                logger.error(f"Erro ao processar resultado DocuSign: {e}")
    
    return resultados


def _traduzir_status_docusign(status):
    """
    Traduz o status do DocuSign para português legível.
    """
    traducoes = {
        'sent': 'Enviado - Aguardando assinatura',
        'delivered': 'Entregue - Aguardando assinatura',
        'signed': 'Assinado',
        'completed': 'Completo - Todas as assinaturas concluídas',
        'declined': 'Recusado',
        'voided': 'Cancelado',
        'created': 'Criado',
    }
    return traducoes.get(status.lower(), status)


def _processar_pagamentos_programados():
    """
    Verifica solicitações com data de pagamento programada que já passou
    e as marca como pagas automaticamente, enviando para DocuSign.
    Usa cache para evitar processamento repetido em requisições consecutivas.
    """
    from datetime import date
    hoje = date.today()
    
    # Verificar cache para evitar processamento repetido (executar no máximo 1x por minuto)
    cache_key = "processar_pagamentos_programados"
    ultima_execucao = cache.get(cache_key)
    if ultima_execucao:
        # Já foi executado recentemente, pular
        return
    
    # Marcar que está processando (TTL de 60 segundos)
    cache.set(cache_key, True, 60)
    
    # Buscar solicitações com data programada que já passou e ainda não foram pagas
    # Incluir tanto STATUS_AGUARDANDO_PAGAMENTO quanto STATUS_PAGAMENTO_AGENDADO
    # Limitar a 10 por vez para não bloquear muito
    solicitacoes_para_processar = SolicitacaoReembolso.objects.filter(
        data_pagamento_programada__lte=hoje,
        pago=False,
        status_gestor_admin=SolicitacaoReembolso.STATUS_APROVADO
    ).filter(
        Q(status=SolicitacaoReembolso.STATUS_AGUARDANDO_PAGAMENTO) | 
        Q(status=SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO)
    )[:10]  # Processar no máximo 10 por vez
    
    for sol in solicitacoes_para_processar:
        try:
            # Marcar como pago e limpar data programada
            sol.pago = True
            sol.pago_em = timezone.now()
            sol.status = SolicitacaoReembolso.STATUS_PAGO_AGUARDANDO_ASSINATURAS
            sol.data_pagamento_programada = None  # Limpar data programada após processar
            sol.save()
            
            # Enviar documento para assinatura no DocuSign
            try:
                # Gerar PDF da solicitação
                pdf_bytes = gerar_pdf(sol)
                
                # Obter dados do solicitante
                try:
                    perfil_solicitante = PerfilSolicitante.objects.get(user=sol.user)
                    nome_solicitante = perfil_solicitante.nome_solicitante or sol.user.get_full_name() or sol.user.email
                    email_solicitante = sol.user.email
                except PerfilSolicitante.DoesNotExist:
                    nome_solicitante = sol.user.get_full_name() or sol.user.email
                    email_solicitante = sol.user.email
                
                # Obter dados do gestor da solicitação
                email_gestor = None
                nome_gestor = sol.nome_gestor or None
                if nome_gestor:
                    # Tentar encontrar o usuário gestor pelo nome ou email
                    try:
                        partes_nome = nome_gestor.split()
                        if len(partes_nome) >= 2:
                            gestor_user = User.objects.filter(
                                first_name__iexact=partes_nome[0],
                                last_name__iexact=" ".join(partes_nome[1:])
                            ).first()
                        else:
                            gestor_user = User.objects.filter(
                                Q(email__iexact=nome_gestor) | 
                                Q(first_name__iexact=nome_gestor) |
                                Q(last_name__iexact=nome_gestor)
                            ).first()
                        
                        if gestor_user:
                            email_gestor = gestor_user.email
                            nome_gestor = gestor_user.get_full_name() or nome_gestor
                    except:
                        pass
                
                # Enviar para DocuSign
                if email_solicitante:
                    resposta_docusign = enviar_documento_para_assinatura(
                        pdf_bytes=pdf_bytes,
                        email_solicitante=email_solicitante,
                        nome_solicitante=nome_solicitante,
                        email_gestor=email_gestor,
                        nome_gestor=nome_gestor
                    )
                    envelope_id = resposta_docusign.get('envelopeId')
                    status_envelope = resposta_docusign.get('status', 'sent')
                    # Salvar envelope_id e status no banco
                    sol.envelope_id_docusign = envelope_id
                    sol.status_docusign = status_envelope
                    sol.save()
                    logger.info(f"Pagamento programado processado automaticamente. Solicitação #{sol.pk} marcada como paga e enviada para DocuSign. Envelope ID: {envelope_id}")
            except Exception as e:
                logger.error(f"Erro ao enviar documento para DocuSign ao processar pagamento programado (Solicitação #{sol.pk}): {e}")
        except Exception as e:
            logger.error(f"Erro ao processar pagamento programado (Solicitação #{sol.pk}): {e}")


def _formatar_acao_historico(acao):
    """Formata o nome da ação do histórico para exibição amigável."""
    acoes_formatadas = {
        "ALTERACAO_DATA_PAGAMENTO": "Alteração de Data de Pagamento",
        "PROGRAMACAO_PAGAMENTO": "Programação de Pagamento",
        "APROVACAO_GESTOR": "Aprovação pelo Gestor",
        "REJEICAO_GESTOR": "Rejeição pelo Gestor",
        "APROVACAO_GESTOR_ADMIN": "Aprovação pelo Gestor Administrativo",
        "REJEICAO_GESTOR_ADMIN": "Rejeição pelo Gestor Administrativo",
        "PAGAMENTO_REALIZADO": "Pagamento Realizado",
        "CONCLUSAO": "Conclusão",
    }
    return acoes_formatadas.get(acao, acao.replace("_", " ").title())


def _get_status_descritivo(solicitacao):
    """
    Retorna o status descritivo da solicitação baseado nos status_gestor e status_gestor_admin.
    Retorna: 'aguardando_gestor', 'aguardando_gestor_admin', 'aguardando_pagamento', 'pagamento_agendado', 
             'pago_aguardando_assinaturas', 'rejeitado', 'concluido'
    """
    # Se foi concluído (verificar status ou campo concluido)
    if solicitacao.status == SolicitacaoReembolso.STATUS_CONCLUIDO or solicitacao.concluido:
        return 'concluido'
    
    # Se foi rejeitado pelo gestor, está rejeitado (mas pode ser editado)
    if solicitacao.status_gestor == SolicitacaoReembolso.STATUS_REJEITADO:
        return 'rejeitado_gestor'
    
    # Se foi rejeitado pelo gestor administrativo, está rejeitado (mas pode ser editado)
    if solicitacao.status_gestor_admin == SolicitacaoReembolso.STATUS_REJEITADO:
        return 'rejeitado_gestor_admin'
    
    # Se foi pago e está aguardando assinaturas
    if solicitacao.pago and solicitacao.envelope_id_docusign:
        # Verificar status do DocuSign
        status_docusign = solicitacao.status_docusign or ''
        if status_docusign.lower() in ['completed', 'signed']:
            # Quando todas as partes assinarem, está concluído automaticamente
            return 'concluido'
        else:
            return 'pago_aguardando_assinaturas'
    
    # Se tem pagamento agendado (status PAGAMENTO_AGENDADO)
    if solicitacao.status == SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO:
        return 'pagamento_agendado'
    
    # Se está aguardando pagamento (aprovado pelo gestor admin mas não pago e sem data programada)
    if solicitacao.status_gestor_admin == SolicitacaoReembolso.STATUS_APROVADO and not solicitacao.pago:
        return 'aguardando_pagamento'
    
    # Se foi aprovado pelo gestor, está aguardando gestor administrativo
    if solicitacao.status_gestor == SolicitacaoReembolso.STATUS_APROVADO:
        return 'aguardando_gestor_admin'
    
    # Se ainda não foi processado pelo gestor, está aguardando gestor
    if solicitacao.status_gestor == SolicitacaoReembolso.STATUS_PENDENTE:
        return 'aguardando_gestor'
    
    # Fallback
    return 'aguardando_gestor'


def _enviar_email_aprovacao_gestor(solicitacao, aprovado=True):
    """Envia e-mail ao solicitante quando o gestor aprova ou rejeita."""
    try:
        solicitante_email = solicitacao.user.email
        if not solicitante_email:
            return
        
        # Buscar nome do solicitante
        try:
            perfil = PerfilSolicitante.objects.get(user=solicitacao.user)
            nome_solicitante = perfil.nome_solicitante or solicitacao.user.get_full_name() or solicitacao.user.email
        except PerfilSolicitante.DoesNotExist:
            nome_solicitante = solicitacao.user.get_full_name() or solicitacao.user.email
        
        # Formatar data
        data_aprovacao = localtime(solicitacao.aprovado_em_gestor).strftime("%d/%m/%Y %H:%M") if solicitacao.aprovado_em_gestor else ""
        
        # Formatar valor
        valor_formatado = f"R$ {solicitacao.valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        if aprovado:
            subject = "Solicitação de Reembolso Aprovada pelo Gestor - Intranet Parceiros"
            message = (
                f"Olá {nome_solicitante},\n\n"
                f"{'='*60}\n"
                f"SUA SOLICITAÇÃO DE REEMBOLSO FOI APROVADA PELO GESTOR\n"
                f"{'='*60}\n\n"
                f"DETALHES DA SOLICITAÇÃO:\n"
                f"{'-'*60}\n"
                f"ID: {solicitacao.pk}\n"
                f"Valor total: {valor_formatado}\n"
                f"Centro de custo: {solicitacao.centro_custo}\n"
                f"Data da solicitação: {localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}\n"
                f"Data da aprovação pelo gestor: {data_aprovacao}\n"
                f"{'-'*60}\n\n"
                f"A solicitação agora aguarda aprovação do gestor administrativo.\n\n"
                f"Atenciosamente,\n"
                f"Equipe Intranet Parceiros"
            )
            nome_solicitante_escaped = html.escape(nome_solicitante)
            centro_custo_escaped = html.escape(solicitacao.centro_custo)
            
            html_message = f"""
            <html>
            <head>
                <meta charset="UTF-8">
            </head>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <p>Olá {nome_solicitante_escaped},</p>
                <div style="border: 2px solid #27ae60; padding: 15px; margin: 20px 0; background-color: #f5f7fa;">
                    <h2 style="color: #27ae60; margin: 0; text-align: center;">
                        SUA SOLICITAÇÃO DE REEMBOLSO FOI APROVADA PELO GESTOR
                    </h2>
                </div>
                <div style="margin: 20px 0;">
                    <h3 style="color: #34495e; border-bottom: 2px solid #e1e8ed; padding-bottom: 10px;">
                        DETALHES DA SOLICITAÇÃO:
                    </h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>ID:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{solicitacao.pk}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Valor total:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{valor_formatado}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Centro de custo:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{centro_custo_escaped}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Data da solicitação:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}</td></tr>
                        <tr><td style="padding: 8px;"><strong>Data da aprovação pelo gestor:</strong></td><td style="padding: 8px;">{data_aprovacao}</td></tr>
                    </table>
                </div>
                <p>A solicitação agora aguarda aprovação do gestor administrativo.</p>
                <p>Atenciosamente,<br>Equipe Intranet Parceiros</p>
            </body>
            </html>
            """
        else:
            subject = "Solicitação de Reembolso Rejeitada pelo Gestor - Intranet Parceiros"
            motivo = solicitacao.motivo_rejeicao_gestor or "Não informado"
            message = (
                f"Olá {nome_solicitante},\n\n"
                f"{'='*60}\n"
                f"SUA SOLICITAÇÃO DE REEMBOLSO FOI REJEITADA PELO GESTOR\n"
                f"{'='*60}\n\n"
                f"DETALHES DA SOLICITAÇÃO:\n"
                f"{'-'*60}\n"
                f"ID: {solicitacao.pk}\n"
                f"Valor total: {valor_formatado}\n"
                f"Centro de custo: {solicitacao.centro_custo}\n"
                f"Data da solicitação: {localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}\n"
                f"Data da rejeição: {data_aprovacao}\n"
                f"Motivo da rejeição: {motivo}\n"
                f"{'-'*60}\n\n"
                f"Se tiver dúvidas sobre a rejeição, entre em contato com o gestor.\n\n"
                f"Atenciosamente,\n"
                f"Equipe Intranet Parceiros"
            )
            motivo_escaped = html.escape(motivo)
            nome_solicitante_escaped = html.escape(nome_solicitante)
            centro_custo_escaped = html.escape(solicitacao.centro_custo)
            
            html_message = f"""
            <html>
            <head>
                <meta charset="UTF-8">
            </head>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <p>Olá {nome_solicitante_escaped},</p>
                <div style="border: 2px solid #e74c3c; padding: 15px; margin: 20px 0; background-color: #f5f7fa;">
                    <h2 style="color: #e74c3c; margin: 0; text-align: center;">
                        SUA SOLICITAÇÃO DE REEMBOLSO FOI REJEITADA PELO GESTOR
                    </h2>
                </div>
                <div style="margin: 20px 0;">
                    <h3 style="color: #34495e; border-bottom: 2px solid #e1e8ed; padding-bottom: 10px;">
                        DETALHES DA SOLICITAÇÃO:
                    </h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>ID:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{solicitacao.pk}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Valor total:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{valor_formatado}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Centro de custo:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{centro_custo_escaped}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Data da solicitação:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Data da rejeição:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{data_aprovacao}</td></tr>
                        <tr><td style="padding: 8px; vertical-align: top;"><strong>Motivo da rejeição:</strong></td><td style="padding: 8px; white-space: pre-wrap; word-wrap: break-word;">{motivo_escaped}</td></tr>
                    </table>
                </div>
                <p>Se tiver dúvidas sobre a rejeição, entre em contato com o gestor.</p>
                <p>Atenciosamente,<br>Equipe Intranet Parceiros</p>
            </body>
            </html>
            """
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[solicitante_email],
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=True)
    except Exception as e:
        logger.error(f"Erro ao enviar e-mail de aprovação/rejeição do gestor: {e}")


def _enviar_email_nova_solicitacao_gestor_admin(solicitacao, request=None):
    """Envia e-mail aos gestores administrativos quando uma solicitação é aprovada pelo gestor."""
    try:
        # Buscar todos os gestores administrativos
        gestores = User.objects.filter(
            regras_usuario__role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO
        ).distinct()
        
        if not gestores.exists():
            return
        
        emails_gestores = []
        for gestor in gestores:
            if gestor.email:
                emails_gestores.append(gestor.email)
        
        if not emails_gestores:
            return
        
        # Buscar nome do solicitante
        try:
            perfil = PerfilSolicitante.objects.get(user=solicitacao.user)
            nome_solicitante = perfil.nome_solicitante or solicitacao.user.get_full_name() or solicitacao.user.email
        except PerfilSolicitante.DoesNotExist:
            nome_solicitante = solicitacao.user.get_full_name() or solicitacao.user.email
        
        nome_solicitante_escaped = html.escape(nome_solicitante)
        email_solicitante_escaped = html.escape(solicitacao.user.email)
        centro_custo_escaped = html.escape(solicitacao.centro_custo)
        
        valor_formatado = f"R$ {solicitacao.valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        if request:
            aprovar_url = request.build_absolute_uri(reverse("intra:aprovar_reembolsos"))
        else:
            aprovar_url = "http://127.0.0.1:8000/aprovar-reembolsos/"
        
        subject = "Nova Solicitação de Reembolso Aprovada pelo Gestor - Aguardando Aprovação Administrativa"
        
        message = (
            f"Olá,\n\n"
            f"{'='*60}\n"
            f"SOLICITAÇÃO DE REEMBOLSO APROVADA PELO GESTOR - AGUARDANDO SUA APROVAÇÃO\n"
            f"{'='*60}\n\n"
            f"DETALHES DA SOLICITAÇÃO:\n"
            f"{'-'*60}\n"
            f"ID: {solicitacao.pk}\n"
            f"Solicitante: {nome_solicitante} ({solicitacao.user.email})\n"
            f"Valor total: {valor_formatado}\n"
            f"Centro de custo: {solicitacao.centro_custo}\n"
            f"Data da solicitação: {localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}\n"
            f"Status: Aprovada pelo Gestor - Aguardando Aprovação Administrativa\n"
            f"{'-'*60}\n\n"
            f"Para visualizar e processar a solicitação, acesse:\n"
            f"{aprovar_url}\n\n"
            f"Atenciosamente,\n"
            f"Equipe Intranet Parceiros"
        )
        
        html_message = f"""
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #2c3e50; margin: 0; padding: 0; background-color: #f5f7fa;">
            <div style="max-width: 600px; margin: 20px auto; background-color: #ffffff; border-radius: 6px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border: 1px solid #e1e8ed;">
                <div style="background: linear-gradient(135deg, #34495e 0%, #2c3e50 50%, #1a252f 100%); padding: 30px 20px; text-align: center;">
                    <h1 style="color: #ecf0f1; margin: 0; font-size: 24px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;">
                        Solicitação Aprovada pelo Gestor
                    </h1>
                    <p style="color: #bdc3c7; margin: 10px 0 0 0; font-size: 16px; font-weight: 500;">
                        Aguardando sua aprovação administrativa
                    </p>
                </div>
                
                <div style="background-color: #fff3cd; border-left: 5px solid #34495e; padding: 15px 20px; margin: 20px;">
                    <p style="margin: 0; color: #856404; font-weight: bold; font-size: 14px;">
                        Ação necessária: Esta solicitação foi aprovada pelo gestor e requer sua aprovação administrativa
                    </p>
                </div>
                
                <div style="padding: 0 20px 20px 20px;">
                    <h2 style="color: #34495e; border-bottom: 3px solid #34495e; padding-bottom: 10px; margin: 20px 0 15px 0; font-size: 18px; font-weight: 600;">
                        Detalhes da Solicitação
                    </h2>
                    <table style="width: 100%; border-collapse: collapse; background-color: #f5f7fa; border-radius: 4px; overflow: hidden; border: 1px solid #e1e8ed;">
                        <tr style="background-color: #ecf0f1;">
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; width: 35%; border-bottom: 1px solid #e1e8ed;">ID da Solicitação:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed; font-weight: 600; font-size: 16px;">#{solicitacao.pk}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Solicitante:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed;">{nome_solicitante_escaped}<br><span style="color: #7f8c8d; font-size: 13px;">{email_solicitante_escaped}</span></td>
                        </tr>
                        <tr style="background-color: #ecf0f1;">
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Valor Total:</td>
                            <td style="padding: 12px 15px; color: #27ae60; border-bottom: 1px solid #e1e8ed; font-weight: 600; font-size: 18px;">{valor_formatado}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Centro de Custo:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed;">{centro_custo_escaped}</td>
                        </tr>
                        <tr style="background-color: #ecf0f1;">
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Data da Solicitação:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed;">{localtime(solicitacao.criado_em).strftime('%d/%m/%Y às %H:%M')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e;">Status:</td>
                            <td style="padding: 12px 15px;">
                                <span style="background-color: #f39c12; color: #ffffff; padding: 5px 12px; border-radius: 4px; font-weight: 600; font-size: 13px; text-transform: uppercase;">
                                    Aprovada pelo Gestor
                                </span>
                            </td>
                        </tr>
                    </table>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{aprovar_url}" style="display: inline-block; background: linear-gradient(135deg, #34495e 0%, #2c3e50 50%, #1a252f 100%); color: #ecf0f1; text-decoration: none; padding: 15px 40px; border-radius: 4px; font-weight: 600; font-size: 16px; box-shadow: 0 2px 6px rgba(52, 73, 94, 0.25);">
                            Visualizar e Processar Solicitação
                        </a>
                    </div>
                </div>
                
                <div style="background-color: #f5f7fa; padding: 20px; text-align: center; border-top: 1px solid #e1e8ed;">
                    <p style="margin: 0; color: #7f8c8d; font-size: 13px;">
                        Este é um e-mail automático. Por favor, não responda.<br>
                        <strong style="color: #34495e;">Equipe Intranet Parceiros</strong>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=emails_gestores,
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=True)
    except Exception as e:
        logger.error(f"Erro ao enviar e-mail de nova solicitação aprovada pelo gestor: {e}")


def _enviar_email_aprovacao_final(solicitacao, aprovado=True):
    """Envia e-mail ao solicitante e ao gestor quando o gestor administrativo aprova ou rejeita."""
    try:
        # Buscar nome do solicitante
        try:
            perfil = PerfilSolicitante.objects.get(user=solicitacao.user)
            nome_solicitante = perfil.nome_solicitante or solicitacao.user.get_full_name() or solicitacao.user.email
        except PerfilSolicitante.DoesNotExist:
            nome_solicitante = solicitacao.user.get_full_name() or solicitacao.user.email
        
        # Formatar data
        data_aprovacao = localtime(solicitacao.aprovado_em_gestor_admin).strftime("%d/%m/%Y %H:%M") if solicitacao.aprovado_em_gestor_admin else ""
        
        # Formatar valor
        valor_formatado = f"R$ {solicitacao.valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        # Lista de destinatários: solicitante e gestor (se houver)
        destinatarios = []
        if solicitacao.user.email:
            destinatarios.append(solicitacao.user.email)
        
        # Buscar email do gestor
        try:
            perfil = PerfilSolicitante.objects.get(user=solicitacao.user)
            if perfil.email_gestor:
                # Verificar se o email do gestor existe como usuário
                try:
                    gestor_user = User.objects.get(email__iexact=perfil.email_gestor)
                    if gestor_user.email and gestor_user.email not in destinatarios:
                        destinatarios.append(gestor_user.email)
                except User.DoesNotExist:
                    pass
        except PerfilSolicitante.DoesNotExist:
            pass
        
        if not destinatarios:
            return
        
        if aprovado:
            subject = "Solicitação de Reembolso Aprovada - Intranet Parceiros"
            message = (
                f"Olá {nome_solicitante},\n\n"
                f"{'='*60}\n"
                f"SUA SOLICITAÇÃO DE REEMBOLSO FOI APROVADA\n"
                f"{'='*60}\n\n"
                f"DETALHES DA SOLICITAÇÃO:\n"
                f"{'-'*60}\n"
                f"ID: {solicitacao.pk}\n"
                f"Valor total: {valor_formatado}\n"
                f"Centro de custo: {solicitacao.centro_custo}\n"
                f"Data da solicitação: {localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}\n"
                f"Data da aprovação final: {data_aprovacao}\n"
                f"{'-'*60}\n\n"
                f"O reembolso será processado conforme os procedimentos internos.\n\n"
                f"Atenciosamente,\n"
                f"Equipe Intranet Parceiros"
            )
            nome_solicitante_escaped = html.escape(nome_solicitante)
            centro_custo_escaped = html.escape(solicitacao.centro_custo)
            
            html_message = f"""
            <html>
            <head>
                <meta charset="UTF-8">
            </head>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <p>Olá {nome_solicitante_escaped},</p>
                <div style="border: 2px solid #27ae60; padding: 15px; margin: 20px 0; background-color: #f5f7fa;">
                    <h2 style="color: #27ae60; margin: 0; text-align: center;">
                        SUA SOLICITAÇÃO DE REEMBOLSO FOI APROVADA
                    </h2>
                </div>
                <div style="margin: 20px 0;">
                    <h3 style="color: #34495e; border-bottom: 2px solid #e1e8ed; padding-bottom: 10px;">
                        DETALHES DA SOLICITAÇÃO:
                    </h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>ID:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{solicitacao.pk}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Valor total:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{valor_formatado}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Centro de custo:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{centro_custo_escaped}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Data da solicitação:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}</td></tr>
                        <tr><td style="padding: 8px;"><strong>Data da aprovação final:</strong></td><td style="padding: 8px;">{data_aprovacao}</td></tr>
                    </table>
                </div>
                <p>O reembolso será processado conforme os procedimentos internos.</p>
                <p>Atenciosamente,<br>Equipe Intranet Parceiros</p>
            </body>
            </html>
            """
        else:
            subject = "Solicitação de Reembolso Rejeitada pelo Gestor Administrativo - Intranet Parceiros"
            motivo = solicitacao.motivo_rejeicao_gestor_admin or "Não informado"
            message = (
                f"Olá {nome_solicitante},\n\n"
                f"{'='*60}\n"
                f"SUA SOLICITAÇÃO DE REEMBOLSO FOI REJEITADA PELO GESTOR ADMINISTRATIVO\n"
                f"{'='*60}\n\n"
                f"DETALHES DA SOLICITAÇÃO:\n"
                f"{'-'*60}\n"
                f"ID: {solicitacao.pk}\n"
                f"Valor total: {valor_formatado}\n"
                f"Centro de custo: {solicitacao.centro_custo}\n"
                f"Data da solicitação: {localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}\n"
                f"Data da rejeição: {data_aprovacao}\n"
                f"Motivo da rejeição: {motivo}\n"
                f"{'-'*60}\n\n"
                f"Se tiver dúvidas sobre a rejeição, entre em contato com o gestor administrativo.\n\n"
                f"Atenciosamente,\n"
                f"Equipe Intranet Parceiros"
            )
            motivo_escaped = html.escape(motivo)
            nome_solicitante_escaped = html.escape(nome_solicitante)
            centro_custo_escaped = html.escape(solicitacao.centro_custo)
            
            html_message = f"""
            <html>
            <head>
                <meta charset="UTF-8">
            </head>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <p>Olá {nome_solicitante_escaped},</p>
                <div style="border: 2px solid #e74c3c; padding: 15px; margin: 20px 0; background-color: #f5f7fa;">
                    <h2 style="color: #e74c3c; margin: 0; text-align: center;">
                        SUA SOLICITAÇÃO DE REEMBOLSO FOI REJEITADA PELO GESTOR ADMINISTRATIVO
                    </h2>
                </div>
                <div style="margin: 20px 0;">
                    <h3 style="color: #34495e; border-bottom: 2px solid #e1e8ed; padding-bottom: 10px;">
                        DETALHES DA SOLICITAÇÃO:
                    </h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>ID:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{solicitacao.pk}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Valor total:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{valor_formatado}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Centro de custo:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{centro_custo_escaped}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Data da solicitação:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Data da rejeição:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{data_aprovacao}</td></tr>
                        <tr><td style="padding: 8px; vertical-align: top;"><strong>Motivo da rejeição:</strong></td><td style="padding: 8px; white-space: pre-wrap; word-wrap: break-word;">{motivo_escaped}</td></tr>
                    </table>
                </div>
                <p>Se tiver dúvidas sobre a rejeição, entre em contato com o gestor administrativo.</p>
                <p>Atenciosamente,<br>Equipe Intranet Parceiros</p>
            </body>
            </html>
            """
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=destinatarios,
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=True)
    except Exception as e:
        logger.error(f"Erro ao enviar e-mail de aprovação/rejeição final: {e}")


def _enviar_email_aprovacao_rejeicao(solicitacao, aprovado=True):
    """Envia e-mail ao solicitante quando a solicitação é aprovada ou rejeitada."""
    try:
        solicitante_email = solicitacao.user.email
        if not solicitante_email:
            return
        
        # Buscar nome do solicitante
        try:
            perfil = PerfilSolicitante.objects.get(user=solicitacao.user)
            nome_solicitante = perfil.nome_solicitante or solicitacao.user.get_full_name() or solicitacao.user.email
        except PerfilSolicitante.DoesNotExist:
            nome_solicitante = solicitacao.user.get_full_name() or solicitacao.user.email
        
        # Formatar data
        data_aprovacao = localtime(solicitacao.aprovado_em).strftime("%d/%m/%Y %H:%M") if solicitacao.aprovado_em else ""
        
        # Formatar valor
        valor_formatado = f"R$ {solicitacao.valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        if aprovado:
            subject = "Solicitação de Reembolso Aprovada - Intranet Parceiros"
            # Versão texto plano
            message = (
                f"Olá {nome_solicitante},\n\n"
                f"{'='*60}\n"
                f"SUA SOLICITAÇÃO DE REEMBOLSO FOI APROVADA\n"
                f"{'='*60}\n\n"
                f"DETALHES DA SOLICITAÇÃO:\n"
                f"{'-'*60}\n"
                f"ID: {solicitacao.pk}\n"
                f"Valor total: {valor_formatado}\n"
                f"Centro de custo: {solicitacao.centro_custo}\n"
                f"Data da solicitação: {localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}\n"
                f"Data da aprovação: {data_aprovacao}\n"
                f"{'-'*60}\n\n"
                f"O reembolso será processado conforme os procedimentos internos.\n\n"
                f"Atenciosamente,\n"
                f"Equipe Intranet Parceiros"
            )
            # Escapar caracteres especiais para HTML
            nome_solicitante_escaped = html.escape(nome_solicitante)
            centro_custo_escaped = html.escape(solicitacao.centro_custo)
            
            # Versão HTML com cores do site e verde para aprovação
            html_message = f"""
            <html>
            <head>
                <meta charset="UTF-8">
            </head>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <p>Olá {nome_solicitante_escaped},</p>
                <div style="border: 2px solid #27ae60; padding: 15px; margin: 20px 0; background-color: #f5f7fa;">
                    <h2 style="color: #27ae60; margin: 0; text-align: center;">
                        SUA SOLICITAÇÃO DE REEMBOLSO FOI APROVADA
                    </h2>
                </div>
                <div style="margin: 20px 0;">
                    <h3 style="color: #34495e; border-bottom: 2px solid #e1e8ed; padding-bottom: 10px;">
                        DETALHES DA SOLICITAÇÃO:
                    </h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>ID:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{solicitacao.pk}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Valor total:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{valor_formatado}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Centro de custo:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{centro_custo_escaped}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Data da solicitação:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}</td></tr>
                        <tr><td style="padding: 8px;"><strong>Data da aprovação:</strong></td><td style="padding: 8px;">{data_aprovacao}</td></tr>
                    </table>
                </div>
                <p>O reembolso será processado conforme os procedimentos internos.</p>
                <p>Atenciosamente,<br>Equipe Intranet Parceiros</p>
            </body>
            </html>
            """
        else:
            subject = "Solicitação de Reembolso Rejeitada - Intranet Parceiros"
            motivo = solicitacao.motivo_rejeicao or "Não informado"
            # Versão texto plano
            message = (
                f"Olá {nome_solicitante},\n\n"
                f"{'='*60}\n"
                f"SUA SOLICITAÇÃO DE REEMBOLSO FOI REJEITADA\n"
                f"{'='*60}\n\n"
                f"DETALHES DA SOLICITAÇÃO:\n"
                f"{'-'*60}\n"
                f"ID: {solicitacao.pk}\n"
                f"Valor total: {valor_formatado}\n"
                f"Centro de custo: {solicitacao.centro_custo}\n"
                f"Data da solicitação: {localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}\n"
                f"Data da rejeição: {data_aprovacao}\n"
                f"Motivo da rejeição: {motivo}\n"
                f"{'-'*60}\n\n"
                f"Se tiver dúvidas sobre a rejeição, entre em contato com o gestor administrativo.\n\n"
                f"Atenciosamente,\n"
                f"Equipe Intranet Parceiros"
            )
            # Escapar caracteres especiais para HTML
            motivo_escaped = html.escape(motivo)
            nome_solicitante_escaped = html.escape(nome_solicitante)
            centro_custo_escaped = html.escape(solicitacao.centro_custo)
            
            # Versão HTML com cores do site e vermelho para rejeição
            html_message = f"""
            <html>
            <head>
                <meta charset="UTF-8">
            </head>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #2c3e50;">
                <p>Olá {nome_solicitante_escaped},</p>
                <div style="border: 2px solid #e74c3c; padding: 15px; margin: 20px 0; background-color: #f5f7fa;">
                    <h2 style="color: #e74c3c; margin: 0; text-align: center;">
                        SUA SOLICITAÇÃO DE REEMBOLSO FOI REJEITADA
                    </h2>
                </div>
                <div style="margin: 20px 0;">
                    <h3 style="color: #34495e; border-bottom: 2px solid #e1e8ed; padding-bottom: 10px;">
                        DETALHES DA SOLICITAÇÃO:
                    </h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>ID:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{solicitacao.pk}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Valor total:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{valor_formatado}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Centro de custo:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{centro_custo_escaped}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Data da solicitação:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;"><strong>Data da rejeição:</strong></td><td style="padding: 8px; border-bottom: 1px solid #e1e8ed;">{data_aprovacao}</td></tr>
                        <tr><td style="padding: 8px; vertical-align: top;"><strong>Motivo da rejeição:</strong></td><td style="padding: 8px; white-space: pre-wrap; word-wrap: break-word;">{motivo_escaped}</td></tr>
                    </table>
                </div>
                <p>Se tiver dúvidas sobre a rejeição, entre em contato com o gestor administrativo.</p>
                <p>Atenciosamente,<br>Equipe Intranet Parceiros</p>
            </body>
            </html>
            """
        
        # Enviar e-mail com versão HTML e texto plano
        email = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[solicitante_email],
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=True)
    except Exception as e:
        # Log do erro sem interromper o fluxo
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Erro ao enviar e-mail de aprovação/rejeição: {e}")


def _enviar_email_nova_solicitacao_gestor(solicitacao, request=None, email_gestor=None):
    """Envia e-mail ao gestor quando uma nova solicitação é criada pelo seu solicitante."""
    try:
        if not email_gestor:
            return
        
        # Buscar nome do solicitante
        try:
            perfil = PerfilSolicitante.objects.get(user=solicitacao.user)
            nome_solicitante = perfil.nome_solicitante or solicitacao.user.get_full_name() or solicitacao.user.email
        except PerfilSolicitante.DoesNotExist:
            nome_solicitante = solicitacao.user.get_full_name() or solicitacao.user.email
        
        nome_solicitante_escaped = html.escape(nome_solicitante)
        email_solicitante_escaped = html.escape(solicitacao.user.email)
        centro_custo_escaped = html.escape(solicitacao.centro_custo)
        
        valor_formatado = f"R$ {solicitacao.valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        if request:
            aprovar_url = request.build_absolute_uri(reverse("intra:aprovar_reembolsos"))
        else:
            aprovar_url = "http://127.0.0.1:8000/aprovar-reembolsos/"
        
        subject = "Nova Solicitação de Reembolso Aguardando sua Aprovação - Intranet Parceiros"
        
        message = (
            f"Olá,\n\n"
            f"{'='*60}\n"
            f"NOVA SOLICITAÇÃO DE REEMBOLSO AGUARDANDO SUA APROVAÇÃO\n"
            f"{'='*60}\n\n"
            f"DETALHES DA SOLICITAÇÃO:\n"
            f"{'-'*60}\n"
            f"ID: {solicitacao.pk}\n"
            f"Solicitante: {nome_solicitante} ({solicitacao.user.email})\n"
            f"Valor total: {valor_formatado}\n"
            f"Centro de custo: {solicitacao.centro_custo}\n"
            f"Data da solicitação: {localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}\n"
            f"Status: Pendente - Aguardando sua aprovação\n"
            f"{'-'*60}\n\n"
            f"Para visualizar e processar a solicitação, acesse:\n"
            f"{aprovar_url}\n\n"
            f"Atenciosamente,\n"
            f"Equipe Intranet Parceiros"
        )
        
        html_message = f"""
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #2c3e50; margin: 0; padding: 0; background-color: #f5f7fa;">
            <div style="max-width: 600px; margin: 20px auto; background-color: #ffffff; border-radius: 6px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border: 1px solid #e1e8ed;">
                <div style="background: linear-gradient(135deg, #34495e 0%, #2c3e50 50%, #1a252f 100%); padding: 30px 20px; text-align: center;">
                    <h1 style="color: #ecf0f1; margin: 0; font-size: 24px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;">
                        Nova Solicitação de Reembolso
                    </h1>
                    <p style="color: #bdc3c7; margin: 10px 0 0 0; font-size: 16px; font-weight: 500;">
                        Aguardando sua aprovação
                    </p>
                </div>
                
                <div style="background-color: #fff3cd; border-left: 5px solid #34495e; padding: 15px 20px; margin: 20px;">
                    <p style="margin: 0; color: #856404; font-weight: bold; font-size: 14px;">
                        Ação necessária: Esta solicitação requer sua atenção
                    </p>
                </div>
                
                <div style="padding: 0 20px 20px 20px;">
                    <h2 style="color: #34495e; border-bottom: 3px solid #34495e; padding-bottom: 10px; margin: 20px 0 15px 0; font-size: 18px; font-weight: 600;">
                        Detalhes da Solicitação
                    </h2>
                    <table style="width: 100%; border-collapse: collapse; background-color: #f5f7fa; border-radius: 4px; overflow: hidden; border: 1px solid #e1e8ed;">
                        <tr style="background-color: #ecf0f1;">
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; width: 35%; border-bottom: 1px solid #e1e8ed;">ID da Solicitação:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed; font-weight: 600; font-size: 16px;">#{solicitacao.pk}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Solicitante:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed;">{nome_solicitante_escaped}<br><span style="color: #7f8c8d; font-size: 13px;">{email_solicitante_escaped}</span></td>
                        </tr>
                        <tr style="background-color: #ecf0f1;">
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Valor Total:</td>
                            <td style="padding: 12px 15px; color: #27ae60; border-bottom: 1px solid #e1e8ed; font-weight: 600; font-size: 18px;">{valor_formatado}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Centro de Custo:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed;">{centro_custo_escaped}</td>
                        </tr>
                        <tr style="background-color: #ecf0f1;">
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Data da Solicitação:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed;">{localtime(solicitacao.criado_em).strftime('%d/%m/%Y às %H:%M')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e;">Status:</td>
                            <td style="padding: 12px 15px;">
                                <span style="background-color: #f39c12; color: #ffffff; padding: 5px 12px; border-radius: 4px; font-weight: 600; font-size: 13px; text-transform: uppercase;">
                                    Pendente
                                </span>
                            </td>
                        </tr>
                    </table>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{aprovar_url}" style="display: inline-block; background: linear-gradient(135deg, #34495e 0%, #2c3e50 50%, #1a252f 100%); color: #ecf0f1; text-decoration: none; padding: 15px 40px; border-radius: 4px; font-weight: 600; font-size: 16px; box-shadow: 0 2px 6px rgba(52, 73, 94, 0.25);">
                            Visualizar e Processar Solicitação
                        </a>
                    </div>
                </div>
                
                <div style="background-color: #f5f7fa; padding: 20px; text-align: center; border-top: 1px solid #e1e8ed;">
                    <p style="margin: 0; color: #7f8c8d; font-size: 13px;">
                        Este é um e-mail automático. Por favor, não responda.<br>
                        <strong style="color: #34495e;">Equipe Intranet Parceiros</strong>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email_gestor],
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=True)
    except Exception as e:
        logger.error(f"Erro ao enviar e-mail de nova solicitação ao gestor: {e}")


def _enviar_email_nova_solicitacao(solicitacao, request=None):
    """Envia e-mail aos gestores administrativos quando uma nova solicitação é criada (legado - mantido para compatibilidade)."""
    try:
        # Buscar todos os gestores administrativos
        gestores = User.objects.filter(
            regras_usuario__role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO
        ).distinct()
        
        if not gestores.exists():
            return
        
        # Coletar e-mails dos gestores
        emails_gestores = []
        for gestor in gestores:
            if gestor.email:
                emails_gestores.append(gestor.email)
        
        if not emails_gestores:
            return
        
        # Buscar nome do solicitante
        try:
            perfil = PerfilSolicitante.objects.get(user=solicitacao.user)
            nome_solicitante = perfil.nome_solicitante or solicitacao.user.get_full_name() or solicitacao.user.email
        except PerfilSolicitante.DoesNotExist:
            nome_solicitante = solicitacao.user.get_full_name() or solicitacao.user.email
        
        # Escapar caracteres especiais para HTML
        nome_solicitante_escaped = html.escape(nome_solicitante)
        email_solicitante_escaped = html.escape(solicitacao.user.email)
        centro_custo_escaped = html.escape(solicitacao.centro_custo)
        
        # Formatar valor
        valor_formatado = f"R$ {solicitacao.valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        # URL para aprovação
        if request:
            aprovar_url = request.build_absolute_uri(reverse("intra:aprovar_reembolsos"))
        else:
            aprovar_url = "http://127.0.0.1:8000/aprovar-reembolsos/"
        
        subject = "Nova Solicitação de Reembolso Aguardando Aprovação - Intranet Parceiros"
        
        # Versão texto plano
        message = (
            f"Olá,\n\n"
            f"{'='*60}\n"
            f"NOVA SOLICITAÇÃO DE REEMBOLSO AGUARDANDO APROVAÇÃO\n"
            f"{'='*60}\n\n"
            f"DETALHES DA SOLICITAÇÃO:\n"
            f"{'-'*60}\n"
            f"ID: {solicitacao.pk}\n"
            f"Solicitante: {nome_solicitante} ({solicitacao.user.email})\n"
            f"Valor total: {valor_formatado}\n"
            f"Centro de custo: {solicitacao.centro_custo}\n"
            f"Data da solicitação: {localtime(solicitacao.criado_em).strftime('%d/%m/%Y %H:%M')}\n"
            f"Status: Pendente\n"
            f"{'-'*60}\n\n"
            f"Para visualizar e processar a solicitação, acesse:\n"
            f"{aprovar_url}\n\n"
            f"Atenciosamente,\n"
            f"Equipe Intranet Parceiros"
        )
        
        # Versão HTML com destaque profissional
        html_message = f"""
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #2c3e50; margin: 0; padding: 0; background-color: #f5f7fa;">
            <div style="max-width: 600px; margin: 20px auto; background-color: #ffffff; border-radius: 6px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border: 1px solid #e1e8ed;">
                <!-- Header com destaque -->
                <div style="background: linear-gradient(135deg, #34495e 0%, #2c3e50 50%, #1a252f 100%); padding: 30px 20px; text-align: center;">
                    <h1 style="color: #ecf0f1; margin: 0; font-size: 24px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;">
                        Nova Solicitação de Reembolso
                    </h1>
                    <p style="color: #bdc3c7; margin: 10px 0 0 0; font-size: 16px; font-weight: 500;">
                        Aguardando sua aprovação
                    </p>
                </div>
                
                <!-- Badge de urgência -->
                <div style="background-color: #fff3cd; border-left: 5px solid #34495e; padding: 15px 20px; margin: 20px;">
                    <p style="margin: 0; color: #856404; font-weight: bold; font-size: 14px;">
                        Ação necessária: Esta solicitação requer sua atenção
                    </p>
                </div>
                
                <!-- Detalhes da solicitação -->
                <div style="padding: 0 20px 20px 20px;">
                    <h2 style="color: #34495e; border-bottom: 3px solid #34495e; padding-bottom: 10px; margin: 20px 0 15px 0; font-size: 18px; font-weight: 600;">
                        Detalhes da Solicitação
                    </h2>
                    <table style="width: 100%; border-collapse: collapse; background-color: #f5f7fa; border-radius: 4px; overflow: hidden; border: 1px solid #e1e8ed;">
                        <tr style="background-color: #ecf0f1;">
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; width: 35%; border-bottom: 1px solid #e1e8ed;">ID da Solicitação:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed; font-weight: 600; font-size: 16px;">#{solicitacao.pk}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Solicitante:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed;">{nome_solicitante_escaped}<br><span style="color: #7f8c8d; font-size: 13px;">{email_solicitante_escaped}</span></td>
                        </tr>
                        <tr style="background-color: #ecf0f1;">
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Valor Total:</td>
                            <td style="padding: 12px 15px; color: #27ae60; border-bottom: 1px solid #e1e8ed; font-weight: 600; font-size: 18px;">{valor_formatado}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Centro de Custo:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed;">{centro_custo_escaped}</td>
                        </tr>
                        <tr style="background-color: #ecf0f1;">
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e; border-bottom: 1px solid #e1e8ed;">Data da Solicitação:</td>
                            <td style="padding: 12px 15px; color: #2c3e50; border-bottom: 1px solid #e1e8ed;">{localtime(solicitacao.criado_em).strftime('%d/%m/%Y às %H:%M')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; font-weight: 600; color: #34495e;">Status:</td>
                            <td style="padding: 12px 15px;">
                                <span style="background-color: #f39c12; color: #ffffff; padding: 5px 12px; border-radius: 4px; font-weight: 600; font-size: 13px; text-transform: uppercase;">
                                    Pendente
                                </span>
                            </td>
                        </tr>
                    </table>
                    
                    <!-- Botão de ação -->
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{aprovar_url}" style="display: inline-block; background: linear-gradient(135deg, #34495e 0%, #2c3e50 50%, #1a252f 100%); color: #ecf0f1; text-decoration: none; padding: 15px 40px; border-radius: 4px; font-weight: 600; font-size: 16px; box-shadow: 0 2px 6px rgba(52, 73, 94, 0.25);">
                            Visualizar e Processar Solicitação
                        </a>
                    </div>
                    
                    <p style="color: #7f8c8d; font-size: 13px; text-align: center; margin-top: 20px;">
                        Ou copie e cole este link no seu navegador:<br>
                        <a href="{aprovar_url}" style="color: #34495e; word-break: break-all; text-decoration: underline;">{aprovar_url}</a>
                    </p>
                </div>
                
                <!-- Footer -->
                <div style="background-color: #f5f7fa; padding: 20px; text-align: center; border-top: 1px solid #e1e8ed;">
                    <p style="margin: 0; color: #7f8c8d; font-size: 13px;">
                        Este é um e-mail automático. Por favor, não responda.<br>
                        <strong style="color: #34495e;">Equipe Intranet Parceiros</strong>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Enviar e-mail com versão HTML e texto plano
        email = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=emails_gestores,
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=True)
    except Exception as e:
        # Log do erro sem interromper o fluxo
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Erro ao enviar e-mail de nova solicitação: {e}")

# Dados mockados para o formulário de reembolso
CENTROS_CUSTO = [
    ("CC001", "Administrativo"),
    ("CC002", "Comercial"),
    ("CC003", "Marketing"),
    ("CC004", "TI / Tecnologia"),
    ("CC005", "Operações"),
]

CODIGOS_DESPESA = [
    ("DESP001", "Despesas Gerais"),
    ("DESP002", "Viagens"),
    ("DESP003", "Alimentação"),
    ("DESP004", "Transporte"),
    ("DESP005", "Materiais de Escritório"),
]

TIPOS_DESPESA = [
    ("DESLOCAMENTO", "Deslocamento"),
    ("LOCOMACAO", "Locomoção"),
    ("PEDAGIO", "Pedágio"),
    ("REFEICAO", "Refeição"),
    ("PASSAGENS", "Passagens de Ônibus"),
    ("OUTROS_MATERIAIS", "Outros Materiais"),
]


def login_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    form = LoginForm(request.POST or None)
    if form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        password = form.cleaned_data["password"]
        user = authenticate(request, email=email, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get("next") or settings.LOGIN_REDIRECT_URL
            return redirect(next_url)
        messages.error(request, "E-mail ou senha incorretos.")
    return render(request, "intra/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


def _gerar_senha():
    """Senha aleatória legível (12 caracteres, sem ambíguos 0/O, 1/l)."""
    return get_random_string(12, "abcdefghjkmnpqrstuvwxyz23456789ABCDEFGHJKMNPQRSTUVWXYZ")


def esqueceu_acesso_view(request):
    form = EsqueceuAcessoForm(request.POST or None)
    enviado = False
    if form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        nova_senha = _gerar_senha()
        login_url = request.build_absolute_uri(reverse("intra:login"))

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            user = None

        if user and _is_gestor_ou_gestor_admin(user):
            messages.info(
                request,
                "Para usuários Gestor ou Gestor Administrativo, a senha é definida exclusivamente no painel administrativo. Entre em contato com o administrador.",
            )
        elif user is None:
            # Primeiro acesso: cria usuário e envia senha
            username = email[:150] if len(email) > 150 else email
            user = User.objects.create_user(
                username=username,
                email=email,
                password=nova_senha,
            )
            PerfilSolicitante.objects.get_or_create(user=user)
            send_mail(
                subject="Acesso à Intranet Parceiros - Senha de acesso",
                message=(
                    f"Olá,\n\n"
                    f"Seu acesso à Intranet Parceiros foi criado.\n\n"
                    f"E-mail: {email}\n"
                    f"Senha: {nova_senha}\n\n"
                    f"Faça login em: {login_url}\n\n"
                    f"Recomendamos alterar sua senha após o primeiro acesso."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            enviado = True
        else:
            # Usuário já existe: altera senha e envia a nova
            user.set_password(nova_senha)
            user.save(update_fields=["password"])
            send_mail(
                subject="Intranet Parceiros - Nova senha de acesso",
                message=(
                    f"Olá,\n\n"
                    f"Você já estava cadastrado na Intranet Parceiros. "
                    f"Sua senha foi alterada conforme solicitado.\n\n"
                    f"E-mail: {email}\n"
                    f"Nova senha: {nova_senha}\n\n"
                    f"Faça login em: {login_url}\n\n"
                    f"Se não foi você quem solicitou, entre em contato com o suporte."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            enviado = True

    return render(
        request,
        "intra/esqueceu_acesso.html",
        {"form": form, "enviado": enviado},
    )


class RedefinirSenhaConfirmView(PasswordResetConfirmView):
    template_name = "intra/redefinir_senha_confirm.html"
    success_url = "/login/"
    post_reset_login = False

    def form_valid(self, form):
        result = super().form_valid(form)
        messages.success(self.request, "Senha redefinida. Faça login com sua nova senha.")
        return result


@login_required
def buscar_gestores_json(request):
    """Retorna lista de gestores para autocomplete."""
    query = request.GET.get("q", "").strip()
    
    # Busca todos os gestores ou filtra por query
    gestores_query = User.objects.filter(
        regras_usuario__role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO
    )
    
    if len(query) >= 2:
        # Filtra por nome ou email se houver query
        gestores_query = gestores_query.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)
        )
    
    gestores = gestores_query.distinct()[:20]  # Aumentado para 20 quando vazio
    
    resultados = []
    for gestor in gestores:
        # Montar nome completo
        partes_nome = []
        if gestor.first_name:
            partes_nome.append(gestor.first_name.strip())
        if gestor.last_name:
            partes_nome.append(gestor.last_name.strip())
        nome_completo = " ".join(partes_nome).strip()
        
        # Se não tiver nome, usar email
        if not nome_completo:
            nome_completo = gestor.email
        
        resultados.append({
            "id": gestor.id,
            "nome": nome_completo,
            "email": gestor.email,
            "display": f"{nome_completo} ({gestor.email})"
        })
    
    return JsonResponse({"gestores": resultados})


@login_required
def completar_cadastro_view(request):
    """Primeiro acesso: obriga preenchimento de nome e dados de pagamento."""
    perfil, _ = PerfilSolicitante.objects.get_or_create(user=request.user)
    form = CompletarCadastroForm(request.POST or None, instance=perfil)
    if form.is_valid():
        form.save()
        messages.success(request, "Cadastro concluído. Você já pode usar a intranet.")
        next_url = request.POST.get("next") or request.GET.get("next") or reverse("intra:home")
        return redirect(next_url)
    return render(request, "intra/completar_cadastro.html", {"form": form})


@login_required
def home(request):
    """Página inicial com sidebar."""
    return render(request, "intra/home.html")


@login_required
@medir_tempo
def meu_perfil(request):
    """Visualização e edição das informações do perfil do usuário."""
    perfil, _ = PerfilSolicitante.objects.get_or_create(user=request.user)
    
    # Processar formulário de trocar senha
    senha_form = TrocarSenhaForm(user=request.user, data=request.POST if 'trocar_senha' in request.POST else None)
    if 'trocar_senha' in request.POST:
        if senha_form.is_valid():
            request.user.set_password(senha_form.cleaned_data['nova_senha'])
            request.user.save()
            messages.success(request, "Senha alterada com sucesso!")
            return redirect("intra:meu_perfil")
        else:
            # Capturar erros específicos do formulário
            error_messages = []
            for field, errors in senha_form.errors.items():
                for error in errors:
                    error_messages.append(str(error))
            
            # Se houver erros de campo específicos, usar o primeiro
            if error_messages:
                messages.error(request, error_messages[0])
            else:
                messages.error(request, "Erro ao alterar senha. Verifique os campos e tente novamente.")
    
    # Processar formulário de editar pagamento
    if request.method == 'POST' and 'editar_pagamento' in request.POST:
        form = EditarPagamentoForm(request.POST, instance=perfil)
        if form.is_valid():
            # Salvar uma cópia dos valores originais antes de salvar
            original_values = {}
            fields_to_check = [
                'forma_pagamento', 'chave_pix', 'banco_pix', 'cpf_pix',
                'banco', 'agencia', 'conta_tipo', 'conta_numero', 'cpf_transferencia'
            ]
            
            # Obter valores originais do perfil
            perfil.refresh_from_db()
            for field in fields_to_check:
                value = getattr(perfil, field, None)
                original_values[field] = str(value).strip() if value else ''
            
            # Obter valores do formulário após clean (que já processa os campos hidden)
            cleaned_data = form.cleaned_data
            new_values = {}
            for field in fields_to_check:
                value = cleaned_data.get(field)
                new_values[field] = str(value).strip() if value else ''
            
            # Comparar valores antes de salvar
            has_changes = False
            for field in fields_to_check:
                old_value = original_values.get(field, '')
                new_value = new_values.get(field, '')
                
                if old_value != new_value:
                    has_changes = True
                    break
            
            # Sempre salvar o formulário (o método save() já limpa os campos corretamente)
            form.save()
            
            if has_changes:
                messages.success(request, "Dados de pagamento atualizados com sucesso!")
            else:
                messages.info(request, "Nenhuma alteração foi detectada nos dados de pagamento.")
            return redirect("intra:meu_perfil")
        else:
            # Não redirecionar quando houver erros - manter em modo de edição
            # Os erros serão exibidos inline nos campos do formulário
            pass
    else:
        form = EditarPagamentoForm(instance=perfil)
    
    # Se não foi POST de trocar senha, criar formulário vazio
    if 'trocar_senha' not in request.POST:
        senha_form = TrocarSenhaForm(user=request.user)
    
    return render(request, "intra/meu_perfil.html", {
        "perfil": perfil,
        "form": form,
        "senha_form": senha_form
    })


@login_required
def meus_reembolsos(request):
    """Lista de solicitações de reembolso do usuário."""
    # Otimizar query com prefetch_related para itens
    solicitacoes = SolicitacaoReembolso.objects.filter(user=request.user).prefetch_related('itens').order_by('-criado_em')
    
    # Paginação - 10 por página
    page_number = request.GET.get('page', 1)
    try:
        page_number = int(page_number)
    except (ValueError, TypeError):
        page_number = 1
    
    paginator = Paginator(solicitacoes, 10)
    try:
        page_obj = paginator.get_page(page_number)
    except (EmptyPage, InvalidPage):
        page_obj = paginator.get_page(1)
    
    # Converter para lista para processar em paralelo apenas os itens da página atual
    solicitacoes_list = list(page_obj)
    
    # Consultar status DocuSign em paralelo apenas para solicitações aprovadas pelo gestor admin
    solicitacoes_para_docusign = [
        sol for sol in solicitacoes_list 
        if sol.status_gestor_admin == SolicitacaoReembolso.STATUS_APROVADO
    ]
    status_docusign_map = _get_status_assinatura_docusign_parallel(solicitacoes_para_docusign)
    
    # Converter datas para timezone de Brasília e coletar códigos de despesa dos itens
    # Os objetos em solicitacoes_list são os mesmos do page_obj, então as modificações serão refletidas
    for sol in solicitacoes_list:
        sol.criado_em_brasilia = localtime(sol.criado_em)
        # Coletar códigos de despesa únicos dos itens (já está em cache do prefetch_related)
        codigos_despesa = []
        for item in sol.itens.all():
            if item.cod_despesa and item.cod_despesa not in codigos_despesa:
                codigos_despesa.append(item.cod_despesa)
        sol.codigos_despesa = ', '.join(codigos_despesa) if codigos_despesa else '—'
        # Usar resultado do cache/paralelo
        if sol.status_gestor_admin == SolicitacaoReembolso.STATUS_APROVADO:
            sol.status_docusign_info = status_docusign_map.get(sol.pk)
            # Atualizar status_docusign no objeto se foi atualizado
            if sol.status_docusign_info:
                sol.status_docusign = sol.status_docusign_info.get('status', sol.status_docusign)
        else:
            sol.status_docusign_info = None
        # Adicionar status descritivo (agora com status DocuSign atualizado)
        sol.status_descritivo = _get_status_descritivo(sol)
    
    return render(
        request,
        "intra/meus_reembolsos.html",
        {"page_obj": page_obj},
    )


@login_required
def reembolso_pdf(request, pk):
    """Gera PDF da solicitação de reembolso (solicitante, gestor ou gestor administrativo).
    Se houver envelope_id do DocuSign, retorna o PDF assinado."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if sol.user_id != request.user.id and not _is_gestor_ou_gestor_admin(request.user):
        return HttpResponse("Não autorizado.", status=403)
    
    # Se houver envelope_id do DocuSign, tentar baixar o PDF assinado
    if sol.envelope_id_docusign:
        try:
            pdf_bytes = baixar_pdf_assinado(sol.envelope_id_docusign)
            nome_arquivo = f"reembolso_{sol.pk}_{sol.criado_em.strftime('%Y%m%d')}_assinado.pdf"
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'inline; filename="{nome_arquivo}"'
            return response
        except Exception as e:
            logger.warning(f"Erro ao baixar PDF assinado do DocuSign: {e}. Retornando PDF original.")
            # Se falhar, continua com o PDF original
    
    # PDF original (sem assinatura ou se falhou ao baixar do DocuSign)
    pdf_bytes = gerar_pdf(sol)
    nome_arquivo = f"reembolso_{sol.pk}_{sol.criado_em.strftime('%Y%m%d')}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{nome_arquivo}"'
    return response


@login_required
def reembolso_detalhe_gestor_json(request, pk):
    """Retorna dados completos da solicitação de reembolso em JSON (para gestor visualizar)."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    is_gestor_simples = _is_gestor_simples(request.user)
    is_gestor_admin = _is_gestor(request.user)
    
    # Verificar se o usuário é o solicitante
    is_solicitante = sol.user_id == request.user.id
    
    # Permitir acesso se for gestor admin, gestor simples (com permissão) ou solicitante
    if not is_gestor_simples and not is_gestor_admin and not is_solicitante:
        return JsonResponse({"error": "Acesso restrito a Gestores, Gestores Administrativos ou ao Solicitante."}, status=403)
    
    # Verificar se o usuário tem permissão para ver esta solicitação
    if is_gestor_simples and not is_solicitante:
        # Gestor só pode ver solicitações onde ele é o gestor indicado (via nome_gestor da solicitação)
        nome_gestor_solicitacao = (sol.nome_gestor or "").strip()
        if not nome_gestor_solicitacao:
            return JsonResponse({"error": "Esta solicitação não possui gestor indicado."}, status=403)
        
        # Buscar nome completo do usuário logado
        partes_nome = []
        if request.user.first_name:
            partes_nome.append(request.user.first_name.strip())
        if request.user.last_name:
            partes_nome.append(request.user.last_name.strip())
        nome_completo_usuario = " ".join(partes_nome).strip()
        
        # Verificar correspondência
        corresponde = False
        if nome_completo_usuario and nome_gestor_solicitacao.lower() == nome_completo_usuario.lower():
            corresponde = True
        elif request.user.email and nome_gestor_solicitacao.lower() == request.user.email.lower():
            corresponde = True
        
        if not corresponde:
            return JsonResponse({"error": "Você não é o gestor indicado nesta solicitação."}, status=403)
    tipos_labels = dict(TIPOS_DESPESA)
    criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
    
    # Coletar códigos de despesa dos itens com descrições
    codigos_despesa = []
    codigos_despesa_com_descricao = []
    for item in sol.itens.all():
        if item.cod_despesa and item.cod_despesa not in codigos_despesa:
            codigos_despesa.append(item.cod_despesa)
            # Buscar descrição do código
            try:
                centro_custo = CentroCusto.objects.filter(CODIGO=item.cod_despesa).first()
                if centro_custo and centro_custo.DESCRICAO:
                    codigos_despesa_com_descricao.append(f"{item.cod_despesa} - {centro_custo.DESCRICAO}")
                else:
                    codigos_despesa_com_descricao.append(item.cod_despesa)
            except:
                codigos_despesa_com_descricao.append(item.cod_despesa)
    
    itens = []
    for item in sol.itens.all():
        # Buscar descrição do código de despesa
        cod_despesa_com_descricao = item.cod_despesa or ""
        if item.cod_despesa:
            try:
                centro_custo = CentroCusto.objects.filter(CODIGO=item.cod_despesa).first()
                if centro_custo and centro_custo.DESCRICAO:
                    cod_despesa_com_descricao = f"{item.cod_despesa} - {centro_custo.DESCRICAO}"
            except:
                pass
        
        # Buscar anexo do item
        anexo_url = None
        anexo_nome = None
        if item.anexo:
            anexo_url = item.anexo.url
            anexo_nome = item.anexo.name.split('/')[-1] if item.anexo.name else None
        
        itens.append({
            "tipo_despesa": tipos_labels.get(item.tipo_despesa, item.tipo_despesa),
            "descricao": item.descricao or "",
            "valor": float(item.valor),
            "cod_despesa": item.cod_despesa or "",
            "cod_despesa_com_descricao": cod_despesa_com_descricao,
            "data_despesa": item.data_despesa.strftime("%d/%m/%Y") if item.data_despesa else "",
            "anexo_url": anexo_url,
            "anexo_nome": anexo_nome,
        })
    
    # Manter anexos separados para compatibilidade (mas agora cada item já tem seu anexo)
    anexos = []
    for item in sol.itens.all():
        anexo_url = None
        anexo_nome = None
        if item.anexo:
            anexo_url = item.anexo.url
            anexo_nome = item.anexo.name.split('/')[-1] if item.anexo.name else None
        
        anexos.append({
            "tipo_despesa": tipos_labels.get(item.tipo_despesa, item.tipo_despesa),
            "descricao": item.descricao or "",
            "url": anexo_url,
            "nome": anexo_nome,
        })
    
    # Buscar dados do perfil do solicitante
    perfil = None
    try:
        perfil = PerfilSolicitante.objects.get(user=sol.user)
    except PerfilSolicitante.DoesNotExist:
        pass
    
    dados_solicitante = {}
    if perfil:
        dados_solicitante = {
            "nome": perfil.nome_solicitante or "",
            "email": sol.user.email or "",
            "banco": perfil.banco or "",
            "agencia": perfil.agencia or "",
            "conta_tipo": perfil.get_conta_tipo_display() if perfil.conta_tipo else "",
            "conta_numero": perfil.conta_numero or "",
            "chave_pix": perfil.chave_pix or "",
        }
    else:
        dados_solicitante = {
            "nome": "",
            "email": sol.user.email or "",
            "banco": "",
            "agencia": "",
            "conta_tipo": "",
            "conta_numero": "",
            "chave_pix": "",
        }
    
    # Informações de aprovação/rejeição do gestor
    aprovacao_gestor_info = {}
    if sol.status_gestor != SolicitacaoReembolso.STATUS_PENDENTE:
        aprovado_por_nome = ""
        aprovado_por_email = ""
        if sol.aprovado_por_gestor:
            aprovado_por_email = sol.aprovado_por_gestor.email or ""
            try:
                perfil_aprovador = PerfilSolicitante.objects.get(user=sol.aprovado_por_gestor)
                aprovado_por_nome = perfil_aprovador.nome_solicitante or ""
            except PerfilSolicitante.DoesNotExist:
                pass
        
        aprovado_em_brasilia = localtime(sol.aprovado_em_gestor) if sol.aprovado_em_gestor else None
        
        aprovacao_gestor_info = {
            "aprovado_por_nome": aprovado_por_nome,
            "aprovado_por_email": aprovado_por_email,
            "aprovado_em": aprovado_em_brasilia.strftime("%d/%m/%Y %H:%M") if aprovado_em_brasilia else "",
            "motivo_rejeicao": sol.motivo_rejeicao_gestor if sol.status_gestor == SolicitacaoReembolso.STATUS_REJEITADO else "",
        }
    
    # Informações de aprovação/rejeição do gestor administrativo
    aprovacao_gestor_admin_info = {}
    if sol.status_gestor_admin != SolicitacaoReembolso.STATUS_PENDENTE:
        aprovado_por_nome = ""
        aprovado_por_email = ""
        if sol.aprovado_por_gestor_admin:
            aprovado_por_email = sol.aprovado_por_gestor_admin.email or ""
            try:
                perfil_aprovador = PerfilSolicitante.objects.get(user=sol.aprovado_por_gestor_admin)
                aprovado_por_nome = perfil_aprovador.nome_solicitante or ""
            except PerfilSolicitante.DoesNotExist:
                pass
        
        aprovado_em_brasilia = localtime(sol.aprovado_em_gestor_admin) if sol.aprovado_em_gestor_admin else None
        
        aprovacao_gestor_admin_info = {
            "aprovado_por_nome": aprovado_por_nome,
            "aprovado_por_email": aprovado_por_email,
            "aprovado_em": aprovado_em_brasilia.strftime("%d/%m/%Y %H:%M") if aprovado_em_brasilia else "",
            "motivo_rejeicao": sol.motivo_rejeicao_gestor_admin if sol.status_gestor_admin == SolicitacaoReembolso.STATUS_REJEITADO else "",
        }
    
    # Para compatibilidade, manter aprovacao_info com base no status final
    aprovacao_info = {}
    if sol.status != SolicitacaoReembolso.STATUS_PENDENTE:
        if sol.status_gestor_admin != SolicitacaoReembolso.STATUS_PENDENTE:
            aprovacao_info = aprovacao_gestor_admin_info
        elif sol.status_gestor != SolicitacaoReembolso.STATUS_PENDENTE:
            aprovacao_info = aprovacao_gestor_info
    
    # Consultar status DocuSign primeiro se aprovado pelo gestor administrativo (para atualizar status)
    status_docusign_info = None
    if sol.status_gestor_admin == SolicitacaoReembolso.STATUS_APROVADO:
        status_docusign_info = _get_status_assinatura_docusign(sol)
        # Atualizar status_docusign no objeto se foi atualizado
        if status_docusign_info:
            sol.status_docusign = status_docusign_info.get('status', sol.status_docusign)
    
    # Calcular status descritivo (agora com status DocuSign atualizado)
    status_descritivo = _get_status_descritivo(sol)
    
    # Determinar motivo de rejeição (se houver)
    motivo_rejeicao = ""
    if sol.status_gestor == SolicitacaoReembolso.STATUS_REJEITADO:
        motivo_rejeicao = sol.motivo_rejeicao_gestor or ""
    elif sol.status_gestor_admin == SolicitacaoReembolso.STATUS_REJEITADO:
        motivo_rejeicao = sol.motivo_rejeicao_gestor_admin or ""
    
    # Buscar histórico de alterações
    historico = []
    for hist in sol.historico.all():
        historico.append({
            "acao": _formatar_acao_historico(hist.acao),
            "descricao": hist.descricao,
            "usuario": hist.usuario.get_full_name() if hist.usuario and hist.usuario.get_full_name() else (hist.usuario.email if hist.usuario else "Sistema"),
            "data": localtime(hist.criado_em).strftime("%d/%m/%Y %H:%M") if hist.criado_em else ""
        })
    
    return JsonResponse({
        "pk": sol.pk,
        "centro_custo": sol.centro_custo or "",
        "cod_despesa": list(set(codigos_despesa)) if codigos_despesa else [],  # Lista única de códigos
        "cod_despesa_com_descricao": codigos_despesa_com_descricao,  # Códigos com descrição
        "valor_total": float(sol.valor_total),
        "criado_em": criado_em_brasilia.strftime("%d/%m/%Y %H:%M") if criado_em_brasilia else "",
        "status": sol.status,
        "status_gestor": sol.status_gestor,
        "status_gestor_admin": sol.status_gestor_admin,
        "status_descritivo": status_descritivo,
        "status_docusign": status_docusign_info,
        "motivo_rejeicao": motivo_rejeicao,
        "itens": itens,
        "anexos": anexos,
        "solicitante": dados_solicitante,
        "aprovacao": aprovacao_info,
        "aprovacao_gestor": aprovacao_gestor_info,
        "aprovacao_gestor_admin": aprovacao_gestor_admin_info,
        "forma_pagamento": sol.forma_pagamento or "",
        "pix_chave": sol.pix_chave or "",
        "pix_banco": sol.pix_banco or "",
        "pix_cpf": sol.pix_cpf or "",
        "transf_banco": sol.transf_banco or "",
        "transf_agencia": sol.transf_agencia or "",
        "transf_conta_tipo": sol.transf_conta_tipo or "",
        "transf_conta_numero": sol.transf_conta_numero or "",
        "transf_cpf": sol.transf_cpf or "",
        "nome_gestor": sol.nome_gestor or "",  # Nome do gestor indicado na solicitação
        "data_pagamento_programada": sol.data_pagamento_programada.strftime("%d/%m/%Y") if (sol.data_pagamento_programada and is_gestor_admin) else None,  # Data programada para pagamento (apenas para gestor admin)
        "historico": historico,
        "is_gestor_admin": is_gestor_admin,  # Flag para identificar se é gestor administrativo
    })


@login_required
def reembolso_anexos_json(request, pk):
    """Retorna anexos da solicitação de reembolso em JSON (para gestor visualizar)."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    is_gestor_simples = _is_gestor_simples(request.user)
    is_gestor_admin = _is_gestor(request.user)
    
    if not is_gestor_simples and not is_gestor_admin:
        return JsonResponse({"error": "Acesso restrito a Gestores ou Gestores Administrativos."}, status=403)
    tipos_labels = dict(TIPOS_DESPESA)
    anexos = []
    for item in sol.itens.all():
        anexo_url = None
        anexo_nome = None
        if item.anexo:
            anexo_url = item.anexo.url
            anexo_nome = item.anexo.name.split('/')[-1] if item.anexo.name else None
        anexos.append({
            "tipo_despesa": tipos_labels.get(item.tipo_despesa, item.tipo_despesa),
            "descricao": item.descricao or "",
            "url": anexo_url,
            "nome": anexo_nome,
        })
    return JsonResponse({"anexos": anexos})


@login_required
def reembolso_detalhe_json(request, pk):
    """Retorna dados da solicitação de reembolso em JSON (para popup de visualização)."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if sol.user_id != request.user.id:
        return JsonResponse({"error": "Não autorizado."}, status=403)
    tipos_labels = dict(TIPOS_DESPESA)
    itens = []
    for item in sol.itens.all():
        # Buscar descrição do código de despesa
        cod_despesa_com_descricao = item.cod_despesa or ""
        if item.cod_despesa:
            try:
                centro_custo = CentroCusto.objects.filter(CODIGO=item.cod_despesa).first()
                if centro_custo and centro_custo.DESCRICAO:
                    cod_despesa_com_descricao = f"{item.cod_despesa} - {centro_custo.DESCRICAO}"
            except:
                pass
        
        # Buscar anexo do item
        anexo_url = None
        anexo_nome = None
        if item.anexo:
            anexo_url = item.anexo.url
            anexo_nome = item.anexo.name.split('/')[-1] if item.anexo.name else None
        
        itens.append({
            "tipo_despesa": tipos_labels.get(item.tipo_despesa, item.tipo_despesa),
            "descricao": item.descricao or "",
            "valor": float(item.valor),
            "km": float(item.km) if item.km is not None else None,
            "cod_despesa": item.cod_despesa or "",
            "cod_despesa_com_descricao": cod_despesa_com_descricao,
            "data_despesa": item.data_despesa.strftime("%d/%m/%Y") if item.data_despesa else "",
            "anexo_url": anexo_url,
            "anexo_nome": anexo_nome,
        })
    # Coletar códigos de despesa únicos dos itens com descrições
    codigos_despesa = []
    codigos_despesa_com_descricao = []
    for item in sol.itens.all():
        if item.cod_despesa and item.cod_despesa not in codigos_despesa:
            codigos_despesa.append(item.cod_despesa)
            # Buscar descrição do código
            try:
                centro_custo = CentroCusto.objects.filter(CODIGO=item.cod_despesa).first()
                if centro_custo and centro_custo.DESCRICAO:
                    codigos_despesa_com_descricao.append(f"{item.cod_despesa} - {centro_custo.DESCRICAO}")
                else:
                    codigos_despesa_com_descricao.append(item.cod_despesa)
            except:
                codigos_despesa_com_descricao.append(item.cod_despesa)
    criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
    
    # Consultar status DocuSign primeiro se aprovado pelo gestor administrativo (para atualizar status)
    status_docusign_info = None
    if sol.status_gestor_admin == SolicitacaoReembolso.STATUS_APROVADO:
        status_docusign_info = _get_status_assinatura_docusign(sol)
        # Atualizar status_docusign no objeto se foi atualizado
        if status_docusign_info:
            sol.status_docusign = status_docusign_info.get('status', sol.status_docusign)
    
    # Calcular status descritivo (agora com status DocuSign atualizado)
    status_descritivo = _get_status_descritivo(sol)
    
    # Determinar motivo de rejeição (se houver)
    motivo_rejeicao = ""
    if sol.status_gestor == SolicitacaoReembolso.STATUS_REJEITADO:
        motivo_rejeicao = sol.motivo_rejeicao_gestor or ""
    elif sol.status_gestor_admin == SolicitacaoReembolso.STATUS_REJEITADO:
        motivo_rejeicao = sol.motivo_rejeicao_gestor_admin or ""
    
    # Buscar histórico de alterações
    historico = []
    for hist in sol.historico.all():
        historico.append({
            "acao": _formatar_acao_historico(hist.acao),
            "descricao": hist.descricao,
            "usuario": hist.usuario.get_full_name() if hist.usuario and hist.usuario.get_full_name() else (hist.usuario.email if hist.usuario else "Sistema"),
            "data": localtime(hist.criado_em).strftime("%d/%m/%Y %H:%M") if hist.criado_em else ""
        })
    
    return JsonResponse({
        "pk": sol.pk,
        "centro_custo": sol.centro_custo,
        "cod_despesa": ', '.join(codigos_despesa_com_descricao) if codigos_despesa_com_descricao else '—',
        "valor_total": float(sol.valor_total),
        "criado_em": criado_em_brasilia.strftime("%d/%m/%Y %H:%M") if criado_em_brasilia else "",
        "status": sol.status,
        "status_descritivo": status_descritivo,
        "status_docusign": status_docusign_info,
        "motivo_rejeicao": motivo_rejeicao,
        "itens": itens,
        "forma_pagamento": sol.forma_pagamento or "",
        "pix_chave": sol.pix_chave or "",
        "pix_banco": sol.pix_banco or "",
        "pix_cpf": sol.pix_cpf or "",
        "transf_banco": sol.transf_banco or "",
        "transf_agencia": sol.transf_agencia or "",
        "transf_conta_tipo": sol.transf_conta_tipo or "",
        "transf_conta_numero": sol.transf_conta_numero or "",
        "transf_cpf": sol.transf_cpf or "",
        "nome_gestor": sol.nome_gestor or "",  # Nome do gestor indicado na solicitação
        "historico": historico,
    })


@login_required
def aprovar_reembolsos(request):
    """Lista solicitações de reembolso para o gestor ou gestor administrativo aprovar ou rejeitar."""
    is_gestor_simples = _is_gestor_simples(request.user)
    is_gestor_admin = _is_gestor(request.user)
    
    if not is_gestor_simples and not is_gestor_admin:
        return HttpResponse("Acesso restrito a Gestores ou Gestores Administrativos.", status=403)
    
    # Processar pagamentos programados que já passaram da data (apenas para gestor admin)
    if is_gestor_admin:
        _processar_pagamentos_programados()
    
    # Capturar parâmetro de busca
    busca = request.GET.get('busca', '').strip()
    
    # Query base - diferente para gestor e gestor administrativo
    if is_gestor_simples:
        # Gestor vê apenas solicitações onde o nome_gestor corresponde ao seu nome completo ou email
        # E que ainda não foram aprovadas/rejeitadas por ele
        solicitacoes = SolicitacaoReembolso.objects.select_related("user").filter(
            status_gestor=SolicitacaoReembolso.STATUS_PENDENTE
        )
        # Buscar nome completo do usuário logado
        partes_nome = []
        if request.user.first_name:
            partes_nome.append(request.user.first_name.strip())
        if request.user.last_name:
            partes_nome.append(request.user.last_name.strip())
        nome_completo_usuario = " ".join(partes_nome).strip()
        
        # Filtrar solicitações onde o nome_gestor corresponde ao nome completo ou email do usuário logado
        filtros_gestor = Q()
        if nome_completo_usuario:
            filtros_gestor |= Q(nome_gestor__iexact=nome_completo_usuario)
        if request.user.email:
            filtros_gestor |= Q(nome_gestor__iexact=request.user.email)
        
        solicitacoes = solicitacoes.filter(filtros_gestor)
    else:
        # Gestor Administrativo vê solicitações aprovadas pelo gestor que ainda não foram aprovadas/rejeitadas por ele
        # E também solicitações aprovadas por ele que estão aguardando pagamento (sem data programada)
        # NÃO inclui solicitações com pagamento agendado (elas aparecem apenas em ultimos_reembolsos)
        solicitacoes = SolicitacaoReembolso.objects.select_related("user").filter(
            Q(
                status_gestor=SolicitacaoReembolso.STATUS_APROVADO,
                status_gestor_admin=SolicitacaoReembolso.STATUS_PENDENTE
            ) | Q(
                status_gestor_admin=SolicitacaoReembolso.STATUS_APROVADO,
                pago=False,
                data_pagamento_programada__isnull=True  # Sem data programada (aguardando pagamento)
            )
        )
    
    # Aplicar filtro de busca se fornecido
    if busca:
        # Tentar buscar por ID se for um número
        try:
            id_busca = int(busca)
            solicitacoes = solicitacoes.filter(pk=id_busca)
        except ValueError:
            # Se não for número, buscar por outros campos
            solicitacoes = solicitacoes.filter(
                Q(user__email__icontains=busca) |
                Q(centro_custo__icontains=busca) |
                Q(cod_despesa__icontains=busca) |
                Q(user__first_name__icontains=busca) |
                Q(user__last_name__icontains=busca)
            )
    
    # Ordenar por data de criação (mais recentes primeiro)
    # Otimizar query com prefetch_related para evitar N+1
    solicitacoes = solicitacoes.select_related("user").prefetch_related(
        "user__perfil_solicitante"
    ).order_by('-criado_em')
    
    # Paginação - 5 por página
    paginator = Paginator(solicitacoes, 5)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Converter page_obj para lista para processar em paralelo
    page_obj_list = list(page_obj)
    
    # Consultar status DocuSign em paralelo apenas para solicitações aprovadas pelo gestor admin
    solicitacoes_para_docusign = [
        sol for sol in page_obj_list 
        if sol.status_gestor_admin == SolicitacaoReembolso.STATUS_APROVADO
    ]
    status_docusign_map = _get_status_assinatura_docusign_parallel(solicitacoes_para_docusign)
    
    # Converter datas para timezone de Brasília e adicionar informações do solicitante
    solicitacoes_com_data = []
    for sol in page_obj_list:
        sol.criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
        # Usar prefetch_related para evitar query adicional
        try:
            perfil = sol.user.perfil_solicitante
            sol.nome_solicitante = perfil.nome_solicitante or ""
        except (PerfilSolicitante.DoesNotExist, AttributeError):
            sol.nome_solicitante = ""
        # Garantir que sempre tenha um email ou username como fallback
        sol.email_solicitante = sol.user.email or sol.user.username or "—"
        # Usar resultado do cache/paralelo
        if sol.status_gestor_admin == SolicitacaoReembolso.STATUS_APROVADO:
            sol.status_docusign_info = status_docusign_map.get(sol.pk)
            # Atualizar status_docusign no objeto se foi atualizado
            if sol.status_docusign_info:
                sol.status_docusign = sol.status_docusign_info.get('status', sol.status_docusign)
        else:
            sol.status_docusign_info = None
        # Determinar qual status mostrar e se pode aprovar/rejeitar (agora com status DocuSign atualizado)
        sol.status_descritivo = _get_status_descritivo(sol)
        if is_gestor_simples:
            sol.pode_decidir = (sol.status_gestor == SolicitacaoReembolso.STATUS_PENDENTE)
            sol.pode_marcar_pago = False
            sol.pode_concluir = False
        else:
            sol.pode_decidir = (sol.status_gestor_admin == SolicitacaoReembolso.STATUS_PENDENTE and sol.status_gestor == SolicitacaoReembolso.STATUS_APROVADO)
            # Pode marcar como pago se está aprovado pelo gestor admin e não está pago
            sol.pode_marcar_pago = (sol.status_gestor_admin == SolicitacaoReembolso.STATUS_APROVADO and not sol.pago)
            # Pode concluir se está pago, tem envelope_id e está assinado
            status_docusign = sol.status_docusign or ''
            sol.pode_concluir = (sol.pago and sol.envelope_id_docusign and status_docusign.lower() in ['completed', 'signed'] and not sol.concluido)
        solicitacoes_com_data.append(sol)
    
    return render(
        request,
        "intra/aprovar_reembolsos.html",
        {
            "solicitacoes": solicitacoes_com_data,
            "page_obj": page_obj,
            "busca": busca,
            "is_gestor_simples": is_gestor_simples,
            "is_gestor_admin": is_gestor_admin,
        },
    )


@login_required
def reembolso_decidir(request, pk):
    """Aprova ou rejeita uma solicitação (gestor ou gestor administrativo)."""
    is_gestor_simples = _is_gestor_simples(request.user)
    is_gestor_admin = _is_gestor(request.user)
    
    if not is_gestor_simples and not is_gestor_admin:
        return HttpResponse("Acesso restrito a Gestores ou Gestores Administrativos.", status=403)
    
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    
    # Verificar se a solicitação pode ser processada pelo usuário atual
    if is_gestor_simples:
        # Gestor só pode processar se ainda estiver pendente para ele
        if sol.status_gestor != SolicitacaoReembolso.STATUS_PENDENTE:
            messages.warning(request, "Esta solicitação já foi processada pelo gestor.")
            return redirect("intra:aprovar_reembolsos")
        # Verificar se o gestor é realmente o gestor indicado na solicitação
        # Buscar nome completo do usuário logado
        partes_nome = []
        if request.user.first_name:
            partes_nome.append(request.user.first_name.strip())
        if request.user.last_name:
            partes_nome.append(request.user.last_name.strip())
        nome_completo_usuario = " ".join(partes_nome).strip()
        
        # Verificar se o nome_gestor da solicitação corresponde ao nome completo ou email do usuário logado
        nome_gestor_solicitacao = (sol.nome_gestor or "").strip()
        if not nome_gestor_solicitacao:
            messages.error(request, "Esta solicitação não possui gestor indicado.")
            return redirect("intra:aprovar_reembolsos")
        
        # Verificar correspondência
        corresponde = False
        if nome_completo_usuario and nome_gestor_solicitacao.lower() == nome_completo_usuario.lower():
            corresponde = True
        elif request.user.email and nome_gestor_solicitacao.lower() == request.user.email.lower():
            corresponde = True
        
        if not corresponde:
            messages.error(request, "Você não é o gestor indicado nesta solicitação.")
            return redirect("intra:aprovar_reembolsos")
    else:
        # Gestor Administrativo só pode processar se já foi aprovado pelo gestor
        if sol.status_gestor != SolicitacaoReembolso.STATUS_APROVADO:
            messages.warning(request, "Esta solicitação ainda não foi aprovada pelo gestor.")
            return redirect("intra:aprovar_reembolsos")
        if sol.status_gestor_admin != SolicitacaoReembolso.STATUS_PENDENTE:
            messages.warning(request, "Esta solicitação já foi processada pelo gestor administrativo.")
            return redirect("intra:aprovar_reembolsos")
    
    if request.method == "POST":
        acao = request.POST.get("acao")
        
        if is_gestor_simples:
            # Aprovação/rejeição do Gestor (primeiro nível)
            if acao == "aprovar":
                sol.status_gestor = SolicitacaoReembolso.STATUS_APROVADO
                sol.aprovado_por_gestor = request.user
                sol.aprovado_em_gestor = timezone.now()
                sol.motivo_rejeicao_gestor = ""
                sol.save()
                # Registrar no histórico
                HistoricoReembolso.objects.create(
                    solicitacao=sol,
                    acao="Aprovada pelo gestor",
                    descricao=f"Solicitação aprovada pelo gestor. Valor total: R$ {sol.valor_total:.2f}",
                    usuario=request.user
                )
                # Enviar e-mail ao solicitante informando aprovação do gestor
                _enviar_email_aprovacao_gestor(sol, aprovado=True)
                # Enviar e-mail ao gestor administrativo informando nova solicitação aprovada
                _enviar_email_nova_solicitacao_gestor_admin(sol, request)
                messages.success(request, "Solicitação aprovada pelo gestor. Aguardando aprovação do gestor administrativo.")
            elif acao == "rejeitar":
                motivo = (request.POST.get("motivo_rejeicao") or "").strip()
                if not motivo:
                    messages.error(request, "É obrigatório informar o motivo da rejeição.")
                    return redirect("intra:aprovar_reembolsos")
                # Rejeitar mas voltar status_gestor para PENDENTE para permitir edição
                sol.status_gestor = SolicitacaoReembolso.STATUS_REJEITADO
                sol.status = SolicitacaoReembolso.STATUS_REJEITADO
                sol.aprovado_por_gestor = request.user
                sol.aprovado_em_gestor = timezone.now()
                sol.motivo_rejeicao_gestor = motivo[:500]
                sol.save()
                # Registrar no histórico
                HistoricoReembolso.objects.create(
                    solicitacao=sol,
                    acao="Rejeitada pelo gestor",
                    descricao=f"Rejeitada pelo gestor. Motivo: {motivo[:500]}",
                    usuario=request.user
                )
                # Enviar e-mail ao solicitante informando rejeição do gestor
                _enviar_email_aprovacao_gestor(sol, aprovado=False)
                messages.success(request, "Solicitação rejeitada pelo gestor. O solicitante pode editar e reenviar.")
        else:
            # Aprovação/rejeição do Gestor Administrativo (segundo nível)
            if acao == "aprovar":
                sol.status_gestor_admin = SolicitacaoReembolso.STATUS_APROVADO
                sol.status = SolicitacaoReembolso.STATUS_AGUARDANDO_PAGAMENTO  # Status: Aprovado - Aguardando pagamento
                sol.pago = False  # Ainda não foi pago
                sol.aprovado_por_gestor_admin = request.user
                sol.aprovado_em_gestor_admin = timezone.now()
                sol.motivo_rejeicao_gestor_admin = ""
                sol.save()
                # Registrar no histórico
                HistoricoReembolso.objects.create(
                    solicitacao=sol,
                    acao="Aprovada pelo gestor administrativo",
                    descricao=f"Solicitação aprovada pelo gestor administrativo. Valor total: R$ {sol.valor_total:.2f}. Aguardando pagamento.",
                    usuario=request.user
                )
                # Enviar e-mail ao solicitante e ao gestor informando aprovação
                _enviar_email_aprovacao_final(sol, aprovado=True)
                messages.success(request, "Solicitação aprovada pelo gestor administrativo. Aguardando pagamento.")
            elif acao == "rejeitar":
                motivo = (request.POST.get("motivo_rejeicao") or "").strip()
                if not motivo:
                    messages.error(request, "É obrigatório informar o motivo da rejeição.")
                    return redirect("intra:aprovar_reembolsos")
                # Rejeitar mas voltar status_gestor_admin para PENDENTE para permitir edição
                sol.status_gestor_admin = SolicitacaoReembolso.STATUS_REJEITADO
                sol.status = SolicitacaoReembolso.STATUS_REJEITADO
                sol.aprovado_por_gestor_admin = request.user
                sol.aprovado_em_gestor_admin = timezone.now()
                sol.motivo_rejeicao_gestor_admin = motivo[:500]
                sol.save()
                # Registrar no histórico
                HistoricoReembolso.objects.create(
                    solicitacao=sol,
                    acao="Rejeitada pelo gestor administrativo",
                    descricao=f"Rejeitada pelo gestor administrativo. Motivo: {motivo[:500]}",
                    usuario=request.user
                )
                # Enviar e-mail ao solicitante e ao gestor informando rejeição
                _enviar_email_aprovacao_final(sol, aprovado=False)
                messages.success(request, "Solicitação rejeitada pelo gestor administrativo. O solicitante pode editar e reenviar.")
        
        return redirect("intra:aprovar_reembolsos")
    return redirect("intra:aprovar_reembolsos")


@login_required
def reembolso_marcar_pago(request, pk):
    """Marca uma solicitação como paga e envia para DocuSign."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    
    # Verificar se pode marcar como pago (deve estar aprovado pelo gestor admin e não estar pago)
    if sol.status_gestor_admin != SolicitacaoReembolso.STATUS_APROVADO or sol.pago:
        messages.error(request, "Esta solicitação não pode ser marcada como paga.")
        # Redirecionar de volta para a página de origem ou ultimos_reembolsos
        next_url = request.GET.get('next') or request.META.get('HTTP_REFERER', '')
        if 'ultimos-reembolsos' in next_url or not next_url:
            return redirect("intra:ultimos_reembolsos")
        return redirect(next_url)
    
    if request.method == "POST":
        # Marcar como pago
        sol.pago = True
        sol.pago_em = timezone.now()
        sol.status = SolicitacaoReembolso.STATUS_PAGO_AGUARDANDO_ASSINATURAS
        sol.save()
        
        # Enviar documento para assinatura no DocuSign
        try:
            # Gerar PDF da solicitação
            pdf_bytes = gerar_pdf(sol)
            
            # Obter dados do solicitante
            try:
                perfil_solicitante = PerfilSolicitante.objects.get(user=sol.user)
                nome_solicitante = perfil_solicitante.nome_solicitante or sol.user.get_full_name() or sol.user.email
                email_solicitante = sol.user.email
            except PerfilSolicitante.DoesNotExist:
                nome_solicitante = sol.user.get_full_name() or sol.user.email
                email_solicitante = sol.user.email
            
            # Obter dados do gestor
            email_gestor = None
            nome_gestor = None
            try:
                perfil_solicitante = PerfilSolicitante.objects.get(user=sol.user)
                if perfil_solicitante.email_gestor:
                    # Tentar obter nome do gestor se for usuário do sistema
                    try:
                        gestor_user = User.objects.get(email__iexact=perfil_solicitante.email_gestor)
                        email_gestor = gestor_user.email
                        nome_gestor = gestor_user.get_full_name() or perfil_solicitante.nome_gestor or email_gestor
                    except User.DoesNotExist:
                        # Se não for usuário, usar dados do perfil
                        email_gestor = perfil_solicitante.email_gestor
                        nome_gestor = perfil_solicitante.nome_gestor or email_gestor
            except PerfilSolicitante.DoesNotExist:
                pass
            
            # Enviar para DocuSign
            if email_solicitante:
                resposta_docusign = enviar_documento_para_assinatura(
                    pdf_bytes=pdf_bytes,
                    email_solicitante=email_solicitante,
                    nome_solicitante=nome_solicitante,
                    email_gestor=email_gestor,
                    nome_gestor=nome_gestor
                )
                envelope_id = resposta_docusign.get('envelopeId')
                status_envelope = resposta_docusign.get('status', 'sent')
                # Salvar envelope_id e status no banco
                sol.envelope_id_docusign = envelope_id
                sol.status_docusign = status_envelope
                sol.save()
                logger.info(f"Documento enviado para DocuSign. Envelope ID: {envelope_id}, Status: {status_envelope}")
                messages.success(request, "Solicitação marcada como paga. Documento enviado para assinatura no DocuSign.")
            else:
                logger.warning(f"Não foi possível enviar para DocuSign: email do solicitante não encontrado.")
                messages.success(request, "Solicitação marcada como paga. Erro ao enviar para DocuSign: email do solicitante não encontrado.")
        except Exception as e:
            logger.error(f"Erro ao enviar documento para DocuSign: {e}")
            messages.warning(request, f"Solicitação marcada como paga, mas houve erro ao enviar para DocuSign: {str(e)}")
        
        # Redirecionar de volta para a página de origem ou ultimos_reembolsos
        next_url = request.GET.get('next') or request.META.get('HTTP_REFERER', '')
        if 'ultimos-reembolsos' in next_url or not next_url:
            return redirect("intra:ultimos_reembolsos")
        return redirect(next_url)
    
    # Se não for POST, redirecionar
    return redirect("intra:ultimos_reembolsos")


@login_required
def reembolso_programar_pagamento(request, pk):
    """Programa uma data de pagamento ou marca como pago imediatamente."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    
    # Se ainda não foi aprovado pelo gestor admin, aprovar primeiro
    if sol.status_gestor_admin != SolicitacaoReembolso.STATUS_APROVADO:
        # Verificar se pode aprovar (deve estar aprovado pelo gestor)
        if sol.status_gestor != SolicitacaoReembolso.STATUS_APROVADO:
            messages.error(request, "Esta solicitação ainda não foi aprovada pelo gestor.")
            return redirect("intra:aprovar_reembolsos")
        if sol.status_gestor_admin != SolicitacaoReembolso.STATUS_PENDENTE:
            messages.error(request, "Esta solicitação já foi processada pelo gestor administrativo.")
            return redirect("intra:aprovar_reembolsos")
        
        # Aprovar pelo gestor admin
        sol.status_gestor_admin = SolicitacaoReembolso.STATUS_APROVADO
        sol.aprovado_por_gestor_admin = request.user
        sol.aprovado_em_gestor_admin = timezone.now()
        sol.motivo_rejeicao_gestor_admin = ""
        # Enviar e-mail ao solicitante e ao gestor informando aprovação
        _enviar_email_aprovacao_final(sol, aprovado=True)
    
    # Verificar se já está pago
    if sol.pago:
        messages.error(request, "Esta solicitação já foi paga.")
        return redirect("intra:aprovar_reembolsos")
    
    if request.method == "POST":
        data_pagamento = request.POST.get("data_pagamento", "").strip()
        marcar_como_pago = request.POST.get("marcar_como_pago", "") == "on"
        
        if marcar_como_pago:
            # Marcar como pago imediatamente (mesma lógica de reembolso_marcar_pago)
            sol.pago = True
            sol.pago_em = timezone.now()
            sol.status = SolicitacaoReembolso.STATUS_PAGO_AGUARDANDO_ASSINATURAS
            sol.data_pagamento_programada = None
            sol.save()
            
            # Enviar documento para assinatura no DocuSign
            try:
                # Gerar PDF da solicitação
                pdf_bytes = gerar_pdf(sol)
                
                # Obter dados do solicitante
                try:
                    perfil_solicitante = PerfilSolicitante.objects.get(user=sol.user)
                    nome_solicitante = perfil_solicitante.nome_solicitante or sol.user.get_full_name() or sol.user.email
                    email_solicitante = sol.user.email
                except PerfilSolicitante.DoesNotExist:
                    nome_solicitante = sol.user.get_full_name() or sol.user.email
                    email_solicitante = sol.user.email
                
                # Obter dados do gestor da solicitação
                email_gestor = None
                nome_gestor = sol.nome_gestor or None
                if nome_gestor:
                    # Tentar encontrar o usuário gestor pelo nome ou email
                    try:
                        partes_nome = nome_gestor.split()
                        if len(partes_nome) >= 2:
                            gestor_user = User.objects.filter(
                                first_name__iexact=partes_nome[0],
                                last_name__iexact=" ".join(partes_nome[1:])
                            ).first()
                        else:
                            gestor_user = User.objects.filter(
                                Q(email__iexact=nome_gestor) | 
                                Q(first_name__iexact=nome_gestor) |
                                Q(last_name__iexact=nome_gestor)
                            ).first()
                        
                        if gestor_user:
                            email_gestor = gestor_user.email
                            nome_gestor = gestor_user.get_full_name() or nome_gestor
                    except:
                        pass
                
                # Enviar para DocuSign
                if email_solicitante:
                    resposta_docusign = enviar_documento_para_assinatura(
                        pdf_bytes=pdf_bytes,
                        email_solicitante=email_solicitante,
                        nome_solicitante=nome_solicitante,
                        email_gestor=email_gestor,
                        nome_gestor=nome_gestor
                    )
                    envelope_id = resposta_docusign.get('envelopeId')
                    status_envelope = resposta_docusign.get('status', 'sent')
                    # Salvar envelope_id e status no banco
                    sol.envelope_id_docusign = envelope_id
                    sol.status_docusign = status_envelope
                    sol.save()
                    logger.info(f"Documento enviado para DocuSign. Envelope ID: {envelope_id}, Status: {status_envelope}")
                    messages.success(request, "Solicitação marcada como paga. Documento enviado para assinatura no DocuSign.")
                else:
                    logger.warning(f"Não foi possível enviar para DocuSign: email do solicitante não encontrado.")
                    messages.success(request, "Solicitação marcada como paga. Erro ao enviar para DocuSign: email do solicitante não encontrado.")
            except Exception as e:
                logger.error(f"Erro ao enviar documento para DocuSign: {e}")
                messages.warning(request, f"Solicitação marcada como paga, mas houve erro ao enviar para DocuSign: {str(e)}")
        elif data_pagamento:
            # Programar data de pagamento
            try:
                from datetime import datetime, date
                data_obj = datetime.strptime(data_pagamento, '%Y-%m-%d').date()
                hoje = date.today()
                
                # Capturar data anterior ANTES de alterar
                data_anterior = sol.data_pagamento_programada
                
                sol.data_pagamento_programada = data_obj
                
                # Se a data programada for hoje ou já passou, processar imediatamente
                if data_obj <= hoje:
                    # Processar como pago imediatamente
                    sol.pago = True
                    sol.pago_em = timezone.now()
                    sol.status = SolicitacaoReembolso.STATUS_PAGO_AGUARDANDO_ASSINATURAS
                    sol.data_pagamento_programada = None  # Limpar data programada após processar
                    sol.save()
                    
                    # Enviar documento para assinatura no DocuSign
                    try:
                        pdf_bytes = gerar_pdf(sol)
                        
                        try:
                            perfil_solicitante = PerfilSolicitante.objects.get(user=sol.user)
                            nome_solicitante = perfil_solicitante.nome_solicitante or sol.user.get_full_name() or sol.user.email
                            email_solicitante = sol.user.email
                        except PerfilSolicitante.DoesNotExist:
                            nome_solicitante = sol.user.get_full_name() or sol.user.email
                            email_solicitante = sol.user.email
                        
                        email_gestor = None
                        nome_gestor = sol.nome_gestor or None
                        if nome_gestor:
                            try:
                                partes_nome = nome_gestor.split()
                                if len(partes_nome) >= 2:
                                    gestor_user = User.objects.filter(
                                        first_name__iexact=partes_nome[0],
                                        last_name__iexact=" ".join(partes_nome[1:])
                                    ).first()
                                else:
                                    gestor_user = User.objects.filter(
                                        Q(email__iexact=nome_gestor) | 
                                        Q(first_name__iexact=nome_gestor) |
                                        Q(last_name__iexact=nome_gestor)
                                    ).first()
                                
                                if gestor_user:
                                    email_gestor = gestor_user.email
                                    nome_gestor = gestor_user.get_full_name() or nome_gestor
                            except:
                                pass
                        
                        if email_solicitante:
                            resposta_docusign = enviar_documento_para_assinatura(
                                pdf_bytes=pdf_bytes,
                                email_solicitante=email_solicitante,
                                nome_solicitante=nome_solicitante,
                                email_gestor=email_gestor,
                                nome_gestor=nome_gestor
                            )
                            envelope_id = resposta_docusign.get('envelopeId')
                            status_envelope = resposta_docusign.get('status', 'sent')
                            sol.envelope_id_docusign = envelope_id
                            sol.status_docusign = status_envelope
                            sol.save()
                            logger.info(f"Pagamento programado processado imediatamente. Solicitação #{sol.pk} marcada como paga e enviada para DocuSign.")
                            messages.success(request, f"Data de pagamento programada para {data_obj.strftime('%d/%m/%Y')}. Como a data é hoje ou já passou, a solicitação foi marcada como paga e enviada para assinatura no DocuSign.")
                        else:
                            messages.success(request, f"Data de pagamento programada para {data_obj.strftime('%d/%m/%Y')}. Solicitação marcada como paga, mas erro ao enviar para DocuSign: email do solicitante não encontrado.")
                    except Exception as e:
                        logger.error(f"Erro ao enviar documento para DocuSign ao processar pagamento programado imediatamente (Solicitação #{sol.pk}): {e}")
                        messages.warning(request, f"Data de pagamento programada para {data_obj.strftime('%d/%m/%Y')}. Solicitação marcada como paga, mas houve erro ao enviar para DocuSign.")
                else:
                    # Data futura - programar e definir status como PAGAMENTO_AGENDADO
                    sol.status = SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO
                    sol.save()
                    
                    # Registrar no histórico se houve alteração de data
                    if data_anterior and data_anterior != data_obj:
                        HistoricoReembolso.objects.create(
                            solicitacao=sol,
                            usuario=request.user,
                            acao="ALTERACAO_DATA_PAGAMENTO",
                            descricao=f"Data de pagamento alterada de {data_anterior.strftime('%d/%m/%Y')} para {data_obj.strftime('%d/%m/%Y')}"
                        )
                    elif not data_anterior:
                        # Primeira vez programando
                        HistoricoReembolso.objects.create(
                            solicitacao=sol,
                            usuario=request.user,
                            acao="PROGRAMACAO_PAGAMENTO",
                            descricao=f"Data de pagamento programada para {data_obj.strftime('%d/%m/%Y')}"
                        )
                    
                    messages.success(request, f"Data de pagamento programada para {data_obj.strftime('%d/%m/%Y')}.")
            except ValueError:
                messages.error(request, "Data de pagamento inválida.")
        else:
            messages.error(request, "Por favor, informe uma data de pagamento ou marque como pago.")
        
        # Verificar se veio da página de últimos reembolsos
        next_url = request.GET.get('next') or request.META.get('HTTP_REFERER', '')
        if next_url:
            if '/ultimos-reembolsos-gestor/' in next_url:
                return redirect("intra:ultimos_reembolsos_gestor")
            elif '/ultimos-reembolsos/' in next_url:
                return redirect("intra:ultimos_reembolsos")
        return redirect("intra:aprovar_reembolsos")
    
    # Se não for POST, redirecionar
    next_url = request.GET.get('next') or request.META.get('HTTP_REFERER', '')
    if next_url:
        if '/ultimos-reembolsos-gestor/' in next_url:
            return redirect("intra:ultimos_reembolsos_gestor")
        elif '/ultimos-reembolsos/' in next_url:
            return redirect("intra:ultimos_reembolsos")
    return redirect("intra:aprovar_reembolsos")


@login_required
def reembolso_concluir(request, pk):
    """Conclui uma solicitação após todas as assinaturas."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    
    # Verificar se pode concluir (deve estar assinado por todas as partes)
    if not sol.pago or not sol.envelope_id_docusign:
        messages.error(request, "Esta solicitação não pode ser concluída ainda.")
        # Redirecionar de volta para a página de origem ou ultimos_reembolsos
        next_url = request.GET.get('next') or request.META.get('HTTP_REFERER', '')
        if 'ultimos-reembolsos' in next_url or not next_url:
            return redirect("intra:ultimos_reembolsos")
        return redirect(next_url)
    
    # Verificar status do DocuSign
    status_docusign = sol.status_docusign or ''
    if status_docusign.lower() not in ['completed', 'signed']:
        messages.error(request, "A solicitação ainda não foi assinada por todas as partes.")
        # Redirecionar de volta para a página de origem ou ultimos_reembolsos
        next_url = request.GET.get('next') or request.META.get('HTTP_REFERER', '')
        if 'ultimos-reembolsos' in next_url or not next_url:
            return redirect("intra:ultimos_reembolsos")
        return redirect(next_url)
    
    if request.method == "POST":
        sol.concluido = True
        sol.concluido_em = timezone.now()
        sol.status = SolicitacaoReembolso.STATUS_CONCLUIDO
        sol.save()
        messages.success(request, "Solicitação concluída com sucesso.")
        # Redirecionar de volta para a página de origem ou ultimos_reembolsos
        next_url = request.GET.get('next') or request.META.get('HTTP_REFERER', '')
        if 'ultimos-reembolsos' in next_url or not next_url:
            return redirect("intra:ultimos_reembolsos")
        return redirect(next_url)
    
    # Se não for POST, redirecionar
    return redirect("intra:ultimos_reembolsos")


@login_required
def dashboard_gestor(request):
    """Dashboard com estatísticas de reembolsos para o gestor administrativo."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    
    # Capturar filtros do GET
    data_inicio = request.GET.get('data_inicio', '')
    data_fim = request.GET.get('data_fim', '')
    status_filtro = request.GET.get('status', '')
    centro_custo_filtro = request.GET.get('centro_custo', '')
    tipo_despesa_filtro = request.GET.get('tipo_despesa', '')
    id_filtro = request.GET.get('id', '').strip()
    
    # Construir filtros base
    filtros_solicitacao = Q()
    
    # Filtro de ID
    if id_filtro:
        try:
            id_valor = int(id_filtro)
            filtros_solicitacao &= Q(pk=id_valor)
        except ValueError:
            pass
    
    # Filtro de data início
    if data_inicio:
        try:
            data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            filtros_solicitacao &= Q(criado_em__date__gte=data_inicio_obj)
        except ValueError:
            pass
    
    # Filtro de data fim
    if data_fim:
        try:
            data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
            filtros_solicitacao &= Q(criado_em__date__lte=data_fim_obj)
        except ValueError:
            pass
    
    # Filtro de centro de custo
    if centro_custo_filtro:
        filtros_solicitacao &= Q(centro_custo=centro_custo_filtro)
    
    # Construir filtros base para as três categorias: Concluído, Em Processo, Rejeitado
    # Aplicar filtros comuns primeiro (data, centro_custo, tipo_despesa, id)
    filtros_comuns = Q()
    
    # Filtro de data início
    if data_inicio:
        try:
            data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            filtros_comuns &= Q(criado_em__date__gte=data_inicio_obj)
        except ValueError:
            pass
    
    # Filtro de data fim
    if data_fim:
        try:
            data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
            filtros_comuns &= Q(criado_em__date__lte=data_fim_obj)
        except ValueError:
            pass
    
    # Filtro de centro de custo
    if centro_custo_filtro:
        filtros_comuns &= Q(centro_custo=centro_custo_filtro)
    
    # Filtro de ID
    if id_filtro:
        try:
            id_valor = int(id_filtro)
            filtros_comuns &= Q(pk=id_valor)
        except ValueError:
            pass
    
    # Filtros específicos para cada categoria
    # Concluído: status=CONCLUIDO ou concluido=True
    filtros_concluidos = filtros_comuns & (
        Q(status=SolicitacaoReembolso.STATUS_CONCLUIDO) | Q(concluido=True)
    )
    
    # Em Processo: aprovadas pelo gestor que não são concluídas ou rejeitadas pelo gestor admin
    filtros_em_processo = filtros_comuns & (
        (
            Q(status_gestor=SolicitacaoReembolso.STATUS_APROVADO, concluido=False) &
            ~Q(status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO)
        ) | Q(
            status=SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO,
            pago=False,
            concluido=False
        )
    )
    
    # Rejeitados: status_gestor_admin=REJEITADO
    filtros_rejeitados = filtros_comuns & Q(
        status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO
    )
    
    # Aplicar filtro de status se especificado
    if status_filtro:
        if status_filtro == 'concluido' or status_filtro == SolicitacaoReembolso.STATUS_CONCLUIDO:
            # Mostrar apenas concluídos
            filtros_em_processo = Q(pk__in=[])
            filtros_rejeitados = Q(pk__in=[])
        elif status_filtro == SolicitacaoReembolso.STATUS_REJEITADO:
            # Mostrar apenas rejeitados
            filtros_concluidos = Q(pk__in=[])
            filtros_em_processo = Q(pk__in=[])
        elif status_filtro in [SolicitacaoReembolso.STATUS_AGUARDANDO_PAGAMENTO, 
                               SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO,
                               SolicitacaoReembolso.STATUS_PAGO_AGUARDANDO_ASSINATURAS]:
            # Mostrar apenas em processo com esse status específico
            filtros_em_processo = filtros_comuns & Q(status=status_filtro, concluido=False)
            filtros_concluidos = Q(pk__in=[])
            filtros_rejeitados = Q(pk__in=[])
    
    # Querysets base para cada categoria
    queryset_concluidos = SolicitacaoReembolso.objects.filter(filtros_concluidos)
    queryset_em_processo = SolicitacaoReembolso.objects.filter(filtros_em_processo)
    queryset_rejeitados = SolicitacaoReembolso.objects.filter(filtros_rejeitados)
    
    # Para compatibilidade com código existente, manter queryset_base
    queryset_base = queryset_concluidos
    
    # Calcular totais e contagens para cada categoria
    # Se houver filtro de tipo de despesa, calcular baseado nos itens
    if tipo_despesa_filtro:
        # Construir filtros para itens considerando tipo de despesa
        filtros_itens_comuns = Q(tipo_despesa=tipo_despesa_filtro)
        
        # Aplicar filtros de data
        if data_inicio:
            try:
                data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                filtros_itens_comuns &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
            except ValueError:
                pass
        
        if data_fim:
            try:
                data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                filtros_itens_comuns &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
            except ValueError:
                pass
        
        # Aplicar filtro de centro de custo
        if centro_custo_filtro:
            filtros_itens_comuns &= Q(solicitacao__centro_custo=centro_custo_filtro)
        
        # Aplicar filtro de ID
        if id_filtro:
            try:
                id_valor = int(id_filtro)
                filtros_itens_comuns &= Q(solicitacao__pk=id_valor)
            except ValueError:
                pass
        
        # Filtros específicos para cada categoria
        filtros_itens_concluidos = filtros_itens_comuns & (
            Q(solicitacao__status=SolicitacaoReembolso.STATUS_CONCLUIDO) | Q(solicitacao__concluido=True)
        )
        
        filtros_itens_em_processo = filtros_itens_comuns & (
            (
                Q(solicitacao__status_gestor=SolicitacaoReembolso.STATUS_APROVADO, solicitacao__concluido=False) &
                ~Q(solicitacao__status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO)
            ) | Q(
                solicitacao__status=SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO,
                solicitacao__pago=False,
                solicitacao__concluido=False
            )
        )
        
        filtros_itens_rejeitados = filtros_itens_comuns & Q(
            solicitacao__status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO
        )
        
        # Calcular totais baseados nos itens
        total_concluido = ItemReembolso.objects.filter(filtros_itens_concluidos).aggregate(
            total=Sum('valor')
        )['total'] or 0
        
        total_em_processo = ItemReembolso.objects.filter(filtros_itens_em_processo).aggregate(
            total=Sum('valor')
        )['total'] or 0
        
        total_rejeitado = ItemReembolso.objects.filter(filtros_itens_rejeitados).aggregate(
            total=Sum('valor')
        )['total'] or 0
        
        # Contagem de solicitações distintas por categoria (não itens)
        count_concluido = ItemReembolso.objects.filter(filtros_itens_concluidos).values('solicitacao').distinct().count()
        count_em_processo = ItemReembolso.objects.filter(filtros_itens_em_processo).values('solicitacao').distinct().count()
        count_rejeitado = ItemReembolso.objects.filter(filtros_itens_rejeitados).values('solicitacao').distinct().count()
    else:
        # Comportamento padrão: calcular baseado nas solicitações
        total_concluido = queryset_concluidos.aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        
        total_em_processo = queryset_em_processo.aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        
        total_rejeitado = queryset_rejeitados.aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        
        # Contagem por categoria
        count_concluido = queryset_concluidos.count()
        count_em_processo = queryset_em_processo.count()
        count_rejeitado = queryset_rejeitados.count()
    
    # Calcular quantos foram pagos por área (centro de custo)
    # Considerar apenas reembolsos concluídos que foram pagos (pago=True)
    filtros_pagos_por_area = filtros_comuns & Q(
        pago=True,
        status=SolicitacaoReembolso.STATUS_CONCLUIDO
    ) | Q(
        pago=True,
        concluido=True
    )
    
    if tipo_despesa_filtro:
        # Se houver filtro de tipo de despesa, usar itens
        filtros_itens_pagos = Q(
            solicitacao__pago=True,
            tipo_despesa=tipo_despesa_filtro
        ) & (
            Q(solicitacao__status=SolicitacaoReembolso.STATUS_CONCLUIDO) |
            Q(solicitacao__concluido=True)
        )
        
        if data_inicio:
            try:
                data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                filtros_itens_pagos &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
            except ValueError:
                pass
        
        if data_fim:
            try:
                data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                filtros_itens_pagos &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
            except ValueError:
                pass
        
        if centro_custo_filtro:
            filtros_itens_pagos &= Q(solicitacao__centro_custo=centro_custo_filtro)
        
        if id_filtro:
            try:
                id_valor = int(id_filtro)
                filtros_itens_pagos &= Q(solicitacao__pk=id_valor)
            except ValueError:
                pass
        
        pagos_por_area = ItemReembolso.objects.filter(
            filtros_itens_pagos
        ).values('solicitacao__centro_custo').annotate(
            total=Sum('valor'),
            count=Count('solicitacao', distinct=True)
        ).order_by('-total')
    else:
        pagos_por_area = SolicitacaoReembolso.objects.filter(
            filtros_pagos_por_area
        ).values('centro_custo').annotate(
            total=Sum('valor_total'),
            count=Count('id')
        ).order_by('-total')
    
    pagos_por_area_formatado = []
    for item in pagos_por_area:
        centro_label = dict(CENTROS_CUSTO).get(
            item.get('centro_custo') or item.get('solicitacao__centro_custo'), 
            item.get('centro_custo') or item.get('solicitacao__centro_custo')
        )
        pagos_por_area_formatado.append({
            'centro': centro_label,
            'valor': float(item['total']),
            'count': item['count']
        })
    
    # Gastos por tipo de despesa (usando itens)
    # Construir filtros para ItemReembolso usando relacionamento solicitacao__
    filtros_itens_tipo = Q()
    
    # Aplicar filtros de data via relacionamento
    if data_inicio:
        try:
            data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            filtros_itens_tipo &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
        except ValueError:
            pass
    
    if data_fim:
        try:
            data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
            filtros_itens_tipo &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
        except ValueError:
            pass
    
    # Filtro de status - se não especificado, mostrar apenas concluídos (comportamento padrão)
    if status_filtro and status_filtro != 'TODOS':
        filtros_itens_tipo &= Q(solicitacao__status=status_filtro)
    else:
        # Comportamento padrão: mostrar apenas concluídos para gastos por tipo
        filtros_itens_tipo &= (
            Q(solicitacao__status=SolicitacaoReembolso.STATUS_CONCLUIDO) |
            Q(solicitacao__concluido=True)
        )
    
    # Filtro de centro de custo via relacionamento
    if centro_custo_filtro:
        filtros_itens_tipo &= Q(solicitacao__centro_custo=centro_custo_filtro)
    
    # Filtro de tipo de despesa (campo direto de ItemReembolso)
    if tipo_despesa_filtro:
        filtros_itens_tipo &= Q(tipo_despesa=tipo_despesa_filtro)
    
    # Filtro de ID
    if id_filtro:
        try:
            id_valor = int(id_filtro)
            filtros_itens_tipo &= Q(solicitacao__pk=id_valor)
        except ValueError:
            pass
    
    gastos_por_tipo = ItemReembolso.objects.filter(
        filtros_itens_tipo
    ).values('tipo_despesa').annotate(
        total=Sum('valor')
    ).order_by('-total')
    
    tipos_labels = dict(TIPOS_DESPESA)
    gastos_por_tipo_formatado = []
    for item in gastos_por_tipo:
        gastos_por_tipo_formatado.append({
            'tipo': tipos_labels.get(item['tipo_despesa'], item['tipo_despesa']),
            'valor': float(item['total'])
        })
    
    # Gastos por centro de custo
    # Se houver filtro de tipo de despesa, calcular baseado nos itens
    if tipo_despesa_filtro:
        # Usar ItemReembolso para calcular gastos por centro de custo do tipo específico
        filtros_itens_centro = Q()
        
        # Aplicar filtros de data via relacionamento
        if data_inicio:
            try:
                data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                filtros_itens_centro &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
            except ValueError:
                pass
        
        if data_fim:
            try:
                data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                filtros_itens_centro &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
            except ValueError:
                pass
        
        # Filtro de status
        if status_filtro and status_filtro != 'TODOS':
            filtros_itens_centro &= Q(solicitacao__status=status_filtro)
        else:
            # Comportamento padrão: mostrar apenas concluídos para gastos por centro
            filtros_itens_centro &= (
                Q(solicitacao__status=SolicitacaoReembolso.STATUS_CONCLUIDO) |
                Q(solicitacao__concluido=True)
            )
        
        # Filtro de centro de custo via relacionamento
        if centro_custo_filtro:
            filtros_itens_centro &= Q(solicitacao__centro_custo=centro_custo_filtro)
        
        # Filtro de tipo de despesa (obrigatório neste caso)
        filtros_itens_centro &= Q(tipo_despesa=tipo_despesa_filtro)
        
        # Filtro de ID
        if id_filtro:
            try:
                id_valor = int(id_filtro)
                filtros_itens_centro &= Q(solicitacao__pk=id_valor)
            except ValueError:
                pass
        
        # Agrupar por centro de custo e somar valores dos itens
        gastos_por_centro = ItemReembolso.objects.filter(
            filtros_itens_centro
        ).values('solicitacao__centro_custo').annotate(
            total=Sum('valor')
        ).order_by('-total')
        
        gastos_por_centro_formatado = []
        for item in gastos_por_centro:
            centro_label = dict(CENTROS_CUSTO).get(item['solicitacao__centro_custo'], item['solicitacao__centro_custo'])
            gastos_por_centro_formatado.append({
                'centro': centro_label,
                'valor': float(item['total'])
            })
    else:
        # Comportamento padrão: calcular baseado nas solicitações concluídas
        gastos_por_centro = queryset_concluidos.values('centro_custo').annotate(
            total=Sum('valor_total')
        ).order_by('-total')
        
        gastos_por_centro_formatado = []
        for item in gastos_por_centro:
            centro_label = dict(CENTROS_CUSTO).get(item['centro_custo'], item['centro_custo'])
            gastos_por_centro_formatado.append({
                'centro': centro_label,
                'valor': float(item['total'])
            })
    
    # Reembolsos por mês (últimos 12 meses ou conforme filtro de data)
    if data_inicio:
        try:
            data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            doze_meses_atras = timezone.make_aware(datetime.combine(data_inicio_obj, datetime.min.time()))
        except ValueError:
            doze_meses_atras = timezone.now() - timedelta(days=365)
    else:
        doze_meses_atras = timezone.now() - timedelta(days=365)
    
    # Reembolsos por mês - combinar todas as categorias
    from django.db.models import F
    reembolsos_por_mes = SolicitacaoReembolso.objects.filter(
        Q(criado_em__gte=doze_meses_atras) & filtros_comuns
    ).annotate(
        mes=TruncMonth('criado_em')
    ).values('mes').annotate(
        total=Count('id'),
        valor_concluido=Sum('valor_total', filter=(
            Q(status=SolicitacaoReembolso.STATUS_CONCLUIDO) | Q(concluido=True)
        )),
        valor_em_processo=Sum('valor_total', filter=(
            (Q(status_gestor=SolicitacaoReembolso.STATUS_APROVADO, concluido=False) &
             ~Q(status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO)) |
            Q(status=SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO, pago=False, concluido=False)
        )),
        valor_rejeitado=Sum('valor_total', filter=Q(status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO))
    ).order_by('mes')
    
    reembolsos_por_mes_formatado = []
    for item in reembolsos_por_mes:
        mes_brasil = localtime(item['mes']).strftime('%m/%Y') if item['mes'] else ''
        reembolsos_por_mes_formatado.append({
            'mes': mes_brasil,
            'total': item['total'],
            'valor_concluido': float(item['valor_concluido'] or 0),
            'valor_em_processo': float(item['valor_em_processo'] or 0),
            'valor_rejeitado': float(item['valor_rejeitado'] or 0)
        })
    
    # Paginação para Reembolsos por Mês (5 por página)
    page_mes = request.GET.get('page_mes', 1)
    paginator_mes = Paginator(reembolsos_por_mes_formatado, 5)
    page_obj_mes = paginator_mes.get_page(page_mes)
    
    # Função helper para construir URL sem um filtro específico
    def get_url_without_filter(exclude_param):
        params = {}
        if data_inicio and exclude_param != 'data_inicio':
            params['data_inicio'] = data_inicio
        if data_fim and exclude_param != 'data_fim':
            params['data_fim'] = data_fim
        if status_filtro and exclude_param != 'status':
            params['status'] = status_filtro
        if centro_custo_filtro and exclude_param != 'centro_custo':
            params['centro_custo'] = centro_custo_filtro
        if tipo_despesa_filtro and exclude_param != 'tipo_despesa':
            params['tipo_despesa'] = tipo_despesa_filtro
        if id_filtro and exclude_param != 'id':
            params['id'] = id_filtro
        
        from django.http import QueryDict
        query_string = QueryDict('', mutable=True)
        query_string.update(params)
        return '?' + query_string.urlencode() if params else ''
    
    # Labels para os filtros - usar os nomes do STATUS_CHOICES
    status_labels = dict(SolicitacaoReembolso.STATUS_CHOICES)
    centro_labels = dict(CENTROS_CUSTO)
    tipo_labels = dict(TIPOS_DESPESA)
    
    # Construir URLs de paginação mantendo filtros
    def build_pagination_url(page_mes=None):
        params = {}
        if data_inicio:
            params['data_inicio'] = data_inicio
        if data_fim:
            params['data_fim'] = data_fim
        if status_filtro:
            params['status'] = status_filtro
        if centro_custo_filtro:
            params['centro_custo'] = centro_custo_filtro
        if tipo_despesa_filtro:
            params['tipo_despesa'] = tipo_despesa_filtro
        if id_filtro:
            params['id'] = id_filtro
        if page_mes:
            params['page_mes'] = page_mes
        
        from django.http import QueryDict
        query_string = QueryDict('', mutable=True)
        query_string.update(params)
        return '?' + query_string.urlencode() if params else ''
    
    # URLs de paginação
    pagination_urls = {
        'first': build_pagination_url(1) if page_obj_mes.has_other_pages() else '',
        'prev': build_pagination_url(page_obj_mes.previous_page_number) if page_obj_mes.has_previous() else '',
        'next': build_pagination_url(page_obj_mes.next_page_number) if page_obj_mes.has_next() else '',
        'last': build_pagination_url(page_obj_mes.paginator.num_pages) if page_obj_mes.has_other_pages() else '',
    }
    
    # Construir lista de badges de filtros ativos
    active_filter_badges = []
    if data_inicio:
        active_filter_badges.append({
            'label': f'Data Início: {data_inicio}',
            'remove_url': get_url_without_filter('data_inicio')
        })
    if data_fim:
        active_filter_badges.append({
            'label': f'Data Fim: {data_fim}',
            'remove_url': get_url_without_filter('data_fim')
        })
    if status_filtro:
        active_filter_badges.append({
            'label': f'Status: {status_labels.get(status_filtro, status_filtro)}',
            'remove_url': get_url_without_filter('status')
        })
    if centro_custo_filtro:
        active_filter_badges.append({
            'label': f'Centro de Custo: {centro_labels.get(centro_custo_filtro, centro_custo_filtro)}',
            'remove_url': get_url_without_filter('centro_custo')
        })
    if tipo_despesa_filtro:
        active_filter_badges.append({
            'label': f'Tipo de Despesa: {tipo_labels.get(tipo_despesa_filtro, tipo_despesa_filtro)}',
            'remove_url': get_url_without_filter('tipo_despesa')
        })
    if id_filtro:
        active_filter_badges.append({
            'label': f'ID: #{id_filtro}',
            'remove_url': get_url_without_filter('id')
        })
    
    # Últimos reembolsos aprovados (ordenados por data de aprovação do gestor admin)
    ultimos_aprovados = SolicitacaoReembolso.objects.filter(
        status=SolicitacaoReembolso.STATUS_APROVADO,
        aprovado_em_gestor_admin__isnull=False,
        concluido=True
    ).select_related('user', 'aprovado_por_gestor_admin').order_by('-aprovado_em_gestor_admin')[:10]
    
    # Últimos reembolsos rejeitados (ordenados por data de rejeição do gestor admin)
    ultimos_rejeitados = SolicitacaoReembolso.objects.filter(
        status=SolicitacaoReembolso.STATUS_REJEITADO,
        aprovado_em_gestor_admin__isnull=False,
        concluido=True
    ).select_related('user', 'aprovado_por_gestor_admin').order_by('-aprovado_em_gestor_admin')[:10]
    
    # Preparar dados dos últimos aprovados
    ultimos_aprovados_list = []
    for sol in ultimos_aprovados:
        try:
            perfil = PerfilSolicitante.objects.get(user=sol.user)
            nome_solicitante = perfil.nome_solicitante or sol.user.get_full_name() or sol.user.email
        except PerfilSolicitante.DoesNotExist:
            nome_solicitante = sol.user.get_full_name() or sol.user.email
        
        ultimos_aprovados_list.append({
            'id': sol.pk,
            'solicitante': nome_solicitante,
            'valor_total': float(sol.valor_total),
            'centro_custo': sol.centro_custo,
            'data_aprovacao': sol.aprovado_em_gestor_admin,
            'aprovado_por': sol.aprovado_por_gestor_admin.get_full_name() if sol.aprovado_por_gestor_admin else 'N/A',
        })
    
    # Preparar dados dos últimos rejeitados
    ultimos_rejeitados_list = []
    for sol in ultimos_rejeitados:
        try:
            perfil = PerfilSolicitante.objects.get(user=sol.user)
            nome_solicitante = perfil.nome_solicitante or sol.user.get_full_name() or sol.user.email
        except PerfilSolicitante.DoesNotExist:
            nome_solicitante = sol.user.get_full_name() or sol.user.email
        
        ultimos_rejeitados_list.append({
            'id': sol.pk,
            'solicitante': nome_solicitante,
            'valor_total': float(sol.valor_total),
            'centro_custo': sol.centro_custo,
            'data_rejeicao': sol.aprovado_em_gestor_admin,
            'rejeitado_por': sol.aprovado_por_gestor_admin.get_full_name() if sol.aprovado_por_gestor_admin else 'N/A',
            'motivo': sol.motivo_rejeicao_gestor_admin or 'Não informado',
        })
    
    # Estatísticas detalhadas por área (centro de custo)
    # Concluídos por área
    if tipo_despesa_filtro:
        concluidos_por_area = ItemReembolso.objects.filter(
            filtros_itens_concluidos
        ).values('solicitacao__centro_custo').annotate(
            total=Sum('valor'),
            count=Count('solicitacao', distinct=True)
        ).order_by('-total')
        
        rejeitados_por_area = ItemReembolso.objects.filter(
            filtros_itens_rejeitados
        ).values('solicitacao__centro_custo').annotate(
            total=Sum('valor'),
            count=Count('solicitacao', distinct=True)
        ).order_by('-total')
        
        em_processo_por_area = ItemReembolso.objects.filter(
            filtros_itens_em_processo
        ).values('solicitacao__centro_custo').annotate(
            total=Sum('valor'),
            count=Count('solicitacao', distinct=True)
        ).order_by('-total')
    else:
        concluidos_por_area = queryset_concluidos.values('centro_custo').annotate(
            total=Sum('valor_total'),
            count=Count('id')
        ).order_by('-total')
        
        rejeitados_por_area = queryset_rejeitados.values('centro_custo').annotate(
            total=Sum('valor_total'),
            count=Count('id')
        ).order_by('-total')
        
        em_processo_por_area = queryset_em_processo.values('centro_custo').annotate(
            total=Sum('valor_total'),
            count=Count('id')
        ).order_by('-total')
    
    # Formatar dados por área
    estatisticas_por_area = []
    todas_areas = set()
    
    # Coletar todas as áreas
    for item in concluidos_por_area:
        centro = item.get('centro_custo') or item.get('solicitacao__centro_custo')
        todas_areas.add(centro)
    for item in rejeitados_por_area:
        centro = item.get('centro_custo') or item.get('solicitacao__centro_custo')
        todas_areas.add(centro)
    for item in em_processo_por_area:
        centro = item.get('centro_custo') or item.get('solicitacao__centro_custo')
        todas_areas.add(centro)
    
    # Criar dicionários para busca rápida
    concluidos_dict = {}
    for item in concluidos_por_area:
        centro = item.get('centro_custo') or item.get('solicitacao__centro_custo')
        concluidos_dict[centro] = {
            'total': float(item['total']),
            'count': item['count']
        }
    
    rejeitados_dict = {}
    for item in rejeitados_por_area:
        centro = item.get('centro_custo') or item.get('solicitacao__centro_custo')
        rejeitados_dict[centro] = {
            'total': float(item['total']),
            'count': item['count']
        }
    
    em_processo_dict = {}
    for item in em_processo_por_area:
        centro = item.get('centro_custo') or item.get('solicitacao__centro_custo')
        em_processo_dict[centro] = {
            'total': float(item['total']),
            'count': item['count']
        }
    
    # Montar lista completa de estatísticas por área
    for centro in todas_areas:
        centro_label = centro_labels.get(centro, centro)
        concluido = concluidos_dict.get(centro, {'total': 0, 'count': 0})
        rejeitado = rejeitados_dict.get(centro, {'total': 0, 'count': 0})
        em_processo = em_processo_dict.get(centro, {'total': 0, 'count': 0})
        
        total_geral = concluido['total'] + rejeitado['total'] + em_processo['total']
        count_geral = concluido['count'] + rejeitado['count'] + em_processo['count']
        
        estatisticas_por_area.append({
            'centro': centro,
            'centro_label': centro_label,
            'concluido_total': concluido['total'],
            'concluido_count': concluido['count'],
            'rejeitado_total': rejeitado['total'],
            'rejeitado_count': rejeitado['count'],
            'em_processo_total': em_processo['total'],
            'em_processo_count': em_processo['count'],
            'total_geral': total_geral,
            'count_geral': count_geral,
        })
    
    # Ordenar por total geral (maior primeiro)
    estatisticas_por_area.sort(key=lambda x: x['total_geral'], reverse=True)
    
    # Dados para gráficos (JSON)
    areas_labels = [item['centro_label'] for item in estatisticas_por_area]
    areas_concluidos = [item['concluido_total'] for item in estatisticas_por_area]
    areas_rejeitados = [item['rejeitado_total'] for item in estatisticas_por_area]
    areas_em_processo = [item['em_processo_total'] for item in estatisticas_por_area]
    areas_concluidos_count = [item['concluido_count'] for item in estatisticas_por_area]
    areas_rejeitados_count = [item['rejeitado_count'] for item in estatisticas_por_area]
    
    # Dados para gráfico de linha (reembolsos por mês)
    meses_labels = [item['mes'] for item in reembolsos_por_mes_formatado]
    meses_concluidos = [item['valor_concluido'] for item in reembolsos_por_mes_formatado]
    meses_rejeitados = [item['valor_rejeitado'] for item in reembolsos_por_mes_formatado]
    meses_em_processo = [item['valor_em_processo'] for item in reembolsos_por_mes_formatado]
    
    # Dados para gráfico de pizza (gastos por tipo)
    tipos_labels_chart = [item['tipo'] for item in gastos_por_tipo_formatado]
    tipos_valores = [item['valor'] for item in gastos_por_tipo_formatado]
    
    # Dados para gráfico de Top Áreas por Tipo de Despesa
    # Agregar gastos por centro de custo e tipo de despesa
    gastos_por_area_tipo = ItemReembolso.objects.filter(
        filtros_itens_tipo
    ).values('solicitacao__centro_custo', 'tipo_despesa').annotate(
        total=Sum('valor')
    ).order_by('solicitacao__centro_custo', '-total')
    
    # Organizar dados por área
    areas_por_tipo = {}
    centros_custo_dict = dict(CENTROS_CUSTO)
    
    for item in gastos_por_area_tipo:
        centro_custo = item['solicitacao__centro_custo']
        tipo_despesa = item['tipo_despesa']
        total = float(item['total'])
        
        if centro_custo not in areas_por_tipo:
            areas_por_tipo[centro_custo] = {}
        
        tipo_label = tipos_labels.get(tipo_despesa, tipo_despesa)
        if tipo_label not in areas_por_tipo[centro_custo]:
            areas_por_tipo[centro_custo][tipo_label] = 0
        areas_por_tipo[centro_custo][tipo_label] += total
    
    # Preparar dados para o gráfico: top áreas e seus tipos de despesa
    areas_tipo_despesa_formatado = []
    for centro_custo, tipos in areas_por_tipo.items():
        centro_label = centros_custo_dict.get(centro_custo, centro_custo)
        total_area = sum(tipos.values())
        areas_tipo_despesa_formatado.append({
            'centro_custo': centro_custo,
            'centro_label': centro_label,
            'tipos': tipos,
            'total': total_area
        })
    
    # Ordenar por total e pegar top 10
    areas_tipo_despesa_formatado.sort(key=lambda x: x['total'], reverse=True)
    areas_tipo_despesa_formatado = areas_tipo_despesa_formatado[:10]
    
    # Calcular métricas de gestão (KPIs)
    total_geral_valor = total_concluido + total_em_processo + total_rejeitado
    total_geral_count = count_concluido + count_em_processo + count_rejeitado
    
    # Taxa de aprovação e rejeição
    taxa_aprovacao = (count_concluido / total_geral_count * 100) if total_geral_count > 0 else 0
    taxa_rejeicao = (count_rejeitado / total_geral_count * 100) if total_geral_count > 0 else 0
    taxa_em_processo = (count_em_processo / total_geral_count * 100) if total_geral_count > 0 else 0
    
    # Adicionar taxas e percentuais às estatísticas por área
    for area in estatisticas_por_area:
        total_area = area['count_geral']
        if total_area > 0:
            area['taxa_aprovacao'] = round((area['concluido_count'] / total_area * 100), 2)
            area['taxa_rejeicao'] = round((area['rejeitado_count'] / total_area * 100), 2)
            area['taxa_em_processo'] = round((area['em_processo_count'] / total_area * 100), 2)
        else:
            area['taxa_aprovacao'] = 0
            area['taxa_rejeicao'] = 0
            area['taxa_em_processo'] = 0
    
    # Rankings (apenas áreas com dados)
    area_mais_concluidos = None
    area_mais_rejeitados = None
    area_maior_taxa_rejeicao = None
    
    if estatisticas_por_area:
        areas_com_concluidos = [a for a in estatisticas_por_area if a['concluido_count'] > 0]
        areas_com_rejeitados = [a for a in estatisticas_por_area if a['rejeitado_count'] > 0]
        areas_com_taxa = [a for a in estatisticas_por_area if a['taxa_rejeicao'] > 0]
        
        if areas_com_concluidos:
            area_mais_concluidos = max(areas_com_concluidos, key=lambda x: x['concluido_count'])
        if areas_com_rejeitados:
            area_mais_rejeitados = max(areas_com_rejeitados, key=lambda x: x['rejeitado_count'])
        if areas_com_taxa:
            area_maior_taxa_rejeicao = max(areas_com_taxa, key=lambda x: x['taxa_rejeicao'])
    
    # Ordenar áreas por diferentes critérios para tabelas
    areas_ordenadas_concluidos = sorted(estatisticas_por_area, key=lambda x: x['concluido_count'], reverse=True)
    areas_ordenadas_rejeitados = sorted(estatisticas_por_area, key=lambda x: x['rejeitado_count'], reverse=True)
    areas_ordenadas_taxa_rejeicao = sorted(estatisticas_por_area, key=lambda x: x['taxa_rejeicao'], reverse=True)
    
    # Dados adicionais para gráficos avançados
    # Comparação de custos médios por centro
    custos_medios_por_centro = []
    for area in estatisticas_por_area:
        if area['count_geral'] > 0:
            custo_medio = area['total_geral'] / area['count_geral']
            custos_medios_por_centro.append({
                'centro': area['centro_label'],
                'custo_medio': float(custo_medio),
                'total': area['total_geral'],
                'count': area['count_geral']
            })
    
    # Ordenar por custo médio
    custos_medios_por_centro.sort(key=lambda x: x['custo_medio'], reverse=True)
    
    # Dados para gráfico de radar (performance por área)
    radar_labels = [item['centro_label'] for item in estatisticas_por_area[:10]]  # Top 10 áreas
    radar_data_concluidos = [item['taxa_aprovacao'] for item in estatisticas_por_area[:10]]
    radar_data_rejeitados = [item['taxa_rejeicao'] for item in estatisticas_por_area[:10]]
    
    # Dados para gráfico de barras empilhadas (status por centro)
    stacked_centros = [item['centro_label'] for item in estatisticas_por_area[:15]]  # Top 15
    stacked_concluidos = [item['concluido_total'] for item in estatisticas_por_area[:15]]
    stacked_em_processo = [item['em_processo_total'] for item in estatisticas_por_area[:15]]
    stacked_rejeitados = [item['rejeitado_total'] for item in estatisticas_por_area[:15]]
    
    # Dados para gráfico de diferença de custos entre centros
    diferenca_custos = []
    if len(custos_medios_por_centro) > 1:
        custo_max = max(custos_medios_por_centro, key=lambda x: x['custo_medio'])['custo_medio']
        for item in custos_medios_por_centro:
            diferenca = custo_max - item['custo_medio']
            diferenca_custos.append({
                'centro': item['centro'],
                'diferenca': float(diferenca),
                'custo_medio': item['custo_medio']
            })
    
    context = {
        'total_concluido': float(total_concluido),
        'total_em_processo': float(total_em_processo),
        'total_rejeitado': float(total_rejeitado),
        'count_concluido': count_concluido,
        'count_em_processo': count_em_processo,
        'count_rejeitado': count_rejeitado,
        'pagos_por_area': pagos_por_area_formatado,
        'gastos_por_tipo': gastos_por_tipo_formatado,
        'gastos_por_centro': gastos_por_centro_formatado,
        'reembolsos_por_mes': page_obj_mes,
        'page_obj_mes': page_obj_mes,
        'ultimos_aprovados': ultimos_aprovados_list,
        'ultimos_rejeitados': ultimos_rejeitados_list,
        # Filtros atuais
        'filtros': {
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'status': status_filtro,
            'centro_custo': centro_custo_filtro,
            'tipo_despesa': tipo_despesa_filtro,
            'id': id_filtro,
        },
        # Labels para exibição
        'status_label': status_labels.get(status_filtro, ''),
        'centro_label': centro_labels.get(centro_custo_filtro, ''),
        'tipo_label': tipo_labels.get(tipo_despesa_filtro, ''),
        # Badges de filtros ativos
        'active_filter_badges': active_filter_badges,
        # URLs de paginação
        'pagination_urls': pagination_urls,
        # Opções para os selects
        'centros_custo': CENTROS_CUSTO,
        'tipos_despesa': TIPOS_DESPESA,
        'status_choices': [
            ('', 'Todos'),
            ('concluido', 'Concluído'),
            ('em_processo', 'Em Processo'),
            (SolicitacaoReembolso.STATUS_REJEITADO, 'Rejeitado'),
            (SolicitacaoReembolso.STATUS_AGUARDANDO_PAGAMENTO, 'Aguardando Pagamento'),
            (SolicitacaoReembolso.STATUS_PAGO_AGUARDANDO_ASSINATURAS, 'Pago - Aguardando Assinaturas'),
        ],
        # Estatísticas por área
        'estatisticas_por_area': estatisticas_por_area,
        # Dados para gráficos (JSON)
        'areas_labels_json': json.dumps(areas_labels),
        'areas_concluidos_json': json.dumps(areas_concluidos),
        'areas_rejeitados_json': json.dumps(areas_rejeitados),
        'areas_em_processo_json': json.dumps(areas_em_processo),
        'areas_concluidos_count_json': json.dumps(areas_concluidos_count),
        'areas_rejeitados_count_json': json.dumps(areas_rejeitados_count),
        'meses_labels_json': json.dumps(meses_labels),
        'meses_concluidos_json': json.dumps(meses_concluidos),
        'meses_rejeitados_json': json.dumps(meses_rejeitados),
        'meses_em_processo_json': json.dumps(meses_em_processo),
        'tipos_labels_json': json.dumps(tipos_labels_chart),
        'tipos_valores_json': json.dumps(tipos_valores),
        # Dados para gráfico de Top Áreas por Tipo de Despesa
        'areas_tipo_despesa_json': json.dumps(areas_tipo_despesa_formatado),
        # Métricas de gestão (KPIs)
        'total_geral_valor': float(total_geral_valor),
        'total_geral_count': total_geral_count,
        'taxa_aprovacao': round(taxa_aprovacao, 2),
        'taxa_rejeicao': round(taxa_rejeicao, 2),
        'taxa_em_processo': round(taxa_em_processo, 2),
        # Rankings
        'area_mais_concluidos': area_mais_concluidos,
        'area_mais_rejeitados': area_mais_rejeitados,
        'area_maior_taxa_rejeicao': area_maior_taxa_rejeicao,
        # Áreas ordenadas para tabelas
        'areas_ordenadas_concluidos': areas_ordenadas_concluidos,
        'areas_ordenadas_rejeitados': areas_ordenadas_rejeitados,
        'areas_ordenadas_taxa_rejeicao': areas_ordenadas_taxa_rejeicao,
        # Áreas ordenadas em JSON para JavaScript
        'areas_ordenadas_concluidos_json': json.dumps(areas_ordenadas_concluidos),
        'areas_ordenadas_rejeitados_json': json.dumps(areas_ordenadas_rejeitados),
        'areas_ordenadas_taxa_rejeicao_json': json.dumps(areas_ordenadas_taxa_rejeicao),
        # Estatísticas por área para gráficos
        'estatisticas_por_area_json': json.dumps(estatisticas_por_area),
        # Dados para gráficos avançados
        'custos_medios_por_centro': custos_medios_por_centro,
        'diferenca_custos': diferenca_custos,
        'radar_labels_json': json.dumps(radar_labels),
        'radar_data_concluidos_json': json.dumps(radar_data_concluidos),
        'radar_data_rejeitados_json': json.dumps(radar_data_rejeitados),
        'stacked_centros_json': json.dumps(stacked_centros),
        'stacked_concluidos_json': json.dumps(stacked_concluidos),
        'stacked_em_processo_json': json.dumps(stacked_em_processo),
        'stacked_rejeitados_json': json.dumps(stacked_rejeitados),
        'custos_medios_labels_json': json.dumps([item['centro'] for item in custos_medios_por_centro]),
        'custos_medios_valores_json': json.dumps([item['custo_medio'] for item in custos_medios_por_centro]),
        'diferenca_custos_labels_json': json.dumps([item['centro'] for item in diferenca_custos]),
        'diferenca_custos_valores_json': json.dumps([item['diferenca'] for item in diferenca_custos]),
    }
    
    return render(request, "intra/dashboard_gestor.html", context)


@login_required
def dashboard_solicitacoes_json(request):
    """Retorna solicitações filtradas por status em JSON para o modal do dashboard."""
    if not _is_gestor(request.user):
        return JsonResponse({"error": "Acesso restrito a Gestores Administrativos."}, status=403)
    
    status_type = request.GET.get('status_type', 'total')  # total, concluidos, em_processo, rejeitados
    
    # Capturar filtros do GET (mesmos filtros do dashboard)
    data_inicio = request.GET.get('data_inicio', '')
    data_fim = request.GET.get('data_fim', '')
    centro_custo_filtro = request.GET.get('centro_custo', '')
    tipo_despesa_filtro = request.GET.get('tipo_despesa', '')
    id_filtro = request.GET.get('id', '').strip()
    
    # Construir filtros comuns
    filtros_comuns = Q()
    
    if id_filtro:
        try:
            id_valor = int(id_filtro)
            filtros_comuns &= Q(pk=id_valor)
        except ValueError:
            pass
    
    if data_inicio:
        try:
            data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            filtros_comuns &= Q(criado_em__date__gte=data_inicio_obj)
        except ValueError:
            pass
    
    if data_fim:
        try:
            data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
            filtros_comuns &= Q(criado_em__date__lte=data_fim_obj)
        except ValueError:
            pass
    
    if centro_custo_filtro:
        filtros_comuns &= Q(centro_custo=centro_custo_filtro)
    
    # Aplicar filtro de tipo de despesa via itens
    if tipo_despesa_filtro:
        filtros_itens = Q(tipo_despesa=tipo_despesa_filtro)
        if data_inicio:
            try:
                data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                filtros_itens &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
            except ValueError:
                pass
        if data_fim:
            try:
                data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                filtros_itens &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
            except ValueError:
                pass
        if centro_custo_filtro:
            filtros_itens &= Q(solicitacao__centro_custo=centro_custo_filtro)
        if id_filtro:
            try:
                id_valor = int(id_filtro)
                filtros_itens &= Q(solicitacao__pk=id_valor)
            except ValueError:
                pass
        solicitacoes_ids = ItemReembolso.objects.filter(filtros_itens).values_list('solicitacao_id', flat=True).distinct()
        filtros_comuns &= Q(pk__in=solicitacoes_ids)
    
    # Construir filtros específicos por status
    if status_type == 'concluidos':
        filtros = filtros_comuns & (
            Q(status=SolicitacaoReembolso.STATUS_CONCLUIDO) | Q(concluido=True)
        )
        queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
            "user__perfil_solicitante"
        ).filter(filtros).order_by('-concluido_em', '-criado_em')
    elif status_type == 'em_processo':
        filtros = filtros_comuns & (
            (
                Q(status_gestor=SolicitacaoReembolso.STATUS_APROVADO, concluido=False) &
                ~Q(status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO)
            ) | Q(
                status=SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO,
                pago=False,
                concluido=False
            )
        )
        queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
            "user__perfil_solicitante"
        ).filter(filtros).order_by('-criado_em')
    elif status_type == 'rejeitados':
        filtros = filtros_comuns & Q(
            status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO
        )
        queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
            "user__perfil_solicitante"
        ).filter(filtros).order_by('-aprovado_em_gestor_admin', '-criado_em')
    else:  # total
        queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
            "user__perfil_solicitante"
        ).filter(filtros_comuns).order_by('-criado_em')
    
    # Paginação - 10 por página
    page_number = request.GET.get('page', 1)
    try:
        page_number = int(page_number)
    except (ValueError, TypeError):
        page_number = 1
    
    paginator = Paginator(queryset, 10)
    try:
        page_obj = paginator.get_page(page_number)
    except:
        page_obj = paginator.get_page(1)
    
    # Serializar dados
    solicitacoes = []
    for sol in page_obj:
        perfil = sol.user.perfil_solicitante if hasattr(sol.user, 'perfil_solicitante') else None
        nome_solicitante = perfil.nome_solicitante if perfil else sol.user.get_full_name() or sol.user.email
        
        # Converter datas para timezone local (como feito em outras views)
        criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
        concluido_em_brasilia = localtime(sol.concluido_em) if sol.concluido_em else None
        aprovado_em_gestor_admin_brasilia = localtime(sol.aprovado_em_gestor_admin) if sol.aprovado_em_gestor_admin else None
        
        # Determinar data apropriada
        if status_type == 'concluidos':
            if concluido_em_brasilia:
                data_exibicao = concluido_em_brasilia.strftime("%d/%m/%Y %H:%M")
            elif criado_em_brasilia:
                data_exibicao = criado_em_brasilia.strftime("%d/%m/%Y %H:%M")
            else:
                data_exibicao = "—"
        elif status_type == 'rejeitados':
            if aprovado_em_gestor_admin_brasilia:
                data_exibicao = aprovado_em_gestor_admin_brasilia.strftime("%d/%m/%Y %H:%M")
            elif criado_em_brasilia:
                data_exibicao = criado_em_brasilia.strftime("%d/%m/%Y %H:%M")
            else:
                data_exibicao = "—"
        else:
            if criado_em_brasilia:
                data_exibicao = criado_em_brasilia.strftime("%d/%m/%Y %H:%M")
            else:
                data_exibicao = "—"
        
        # Determinar status descritivo
        status_descritivo = _get_status_descritivo(sol)
        
        solicitacoes.append({
            'pk': sol.pk,
            'nome_solicitante': nome_solicitante,
            'data_exibicao': data_exibicao,
            'centro_custo': sol.centro_custo or '-',
            'valor_total': float(sol.valor_total),
            'status_descritivo': status_descritivo,
        })
    
    return JsonResponse({
        'solicitacoes': solicitacoes,
        'total': paginator.count,
        'page': page_obj.number,
        'num_pages': paginator.num_pages,
        'has_previous': page_obj.has_previous(),
        'has_next': page_obj.has_next(),
        'previous_page_number': page_obj.previous_page_number() if page_obj.has_previous() else None,
        'next_page_number': page_obj.next_page_number() if page_obj.has_next() else None,
    })


@login_required
def reembolso(request):
    """Página de solicitação de reembolso com formulário."""
    # Debug: Verificar configuração S3 no início
    if request.method == 'POST':
        print(f"[DEBUG S3] Verificando configuração S3...")
        print(f"[DEBUG S3] DEFAULT_FILE_STORAGE: {settings.DEFAULT_FILE_STORAGE}")
        print(f"[DEBUG S3] AWS_STORAGE_BUCKET_NAME: {getattr(settings, 'AWS_STORAGE_BUCKET_NAME', 'NÃO CONFIGURADO')}")
        print(f"[DEBUG S3] AWS_S3_REGION_NAME: {getattr(settings, 'AWS_S3_REGION_NAME', 'NÃO CONFIGURADO')}")
        print(f"[DEBUG S3] MEDIA_URL: {getattr(settings, 'MEDIA_URL', 'NÃO CONFIGURADO')}")
        try:
            from storages.backends.s3boto3 import S3Boto3Storage
            storage = S3Boto3Storage()
            print(f"[DEBUG S3] Storage instanciado: {storage}")
            print(f"[DEBUG S3] Bucket name do storage: {storage.bucket_name}")
        except Exception as e:
            print(f"[DEBUG S3] ERRO ao instanciar storage: {str(e)}")
            import traceback
            print(f"[DEBUG S3] Traceback: {traceback.format_exc()}")
    # Buscar valores únicos de PROGRAMA para Centro de Custo
    programas = CentroCusto.objects.exclude(PROGRAMA__isnull=True).exclude(PROGRAMA__exact='').values_list("PROGRAMA", flat=True).distinct().order_by("PROGRAMA")
    
    # Buscar todos os registros de CentroCusto para Código de Despesa
    codigos = CentroCusto.objects.exclude(CODIGO__isnull=True).exclude(CODIGO__exact='').exclude(DESCRICAO__isnull=True).exclude(DESCRICAO__exact='').order_by("CODIGO")
    
    # Preparar dados para JSON organizados por PROGRAMA (para filtro dinâmico)
    # Estrutura: { "PROGRAMA1": [{"codigo": "...", "descricao": "..."}, ...], ... }
    codigos_por_programa = {}
    for c in codigos:
        programa = c.PROGRAMA or ""
        if programa not in codigos_por_programa:
            codigos_por_programa[programa] = []
        codigos_por_programa[programa].append({
            "codigo": c.CODIGO,
            "descricao": c.DESCRICAO
        })
    
    # Também manter a lista completa para compatibilidade
    codigos_json = [{"codigo": c.CODIGO, "descricao": c.DESCRICAO, "programa": c.PROGRAMA or ""} for c in codigos]
    
    # Buscar perfil do solicitante para pré-preencher dados de pagamento
    perfil = None
    try:
        perfil = PerfilSolicitante.objects.get(user=request.user)
    except PerfilSolicitante.DoesNotExist:
        pass
    
    # Verificar se é edição de uma solicitação rejeitada
    solicitacao_editar = None
    editar_id = request.GET.get("editar", "").strip()
    if editar_id:
        try:
            solicitacao_editar = SolicitacaoReembolso.objects.get(pk=int(editar_id), user=request.user)
            # Verificar se pode editar (deve estar rejeitada)
            if solicitacao_editar.status_gestor != SolicitacaoReembolso.STATUS_REJEITADO and solicitacao_editar.status_gestor_admin != SolicitacaoReembolso.STATUS_REJEITADO:
                messages.error(request, "Esta solicitação não pode ser editada.")
                return redirect("intra:meus_reembolsos")
        except (SolicitacaoReembolso.DoesNotExist, ValueError):
            messages.error(request, "Solicitação não encontrada.")
            return redirect("intra:meus_reembolsos")
    
    # Lista de bancos para o select
    BANCOS_CHOICES = [
        ('', 'Selecione...'),
        ('Banco do Brasil', 'Banco do Brasil'),
        ('Bradesco', 'Bradesco'),
        ('Itaú', 'Itaú'),
        ('Santander', 'Santander'),
        ('Caixa Econômica Federal', 'Caixa Econômica Federal'),
        ('Banco Inter', 'Banco Inter'),
        ('Nubank', 'Nubank'),
        ('Banco Original', 'Banco Original'),
        ('Banrisul', 'Banrisul'),
        ('Banco Safra', 'Banco Safra'),
        ('BTG Pactual', 'BTG Pactual'),
        ('Banco Pan', 'Banco Pan'),
        ('Banco Votorantim', 'Banco Votorantim'),
        ('Banco C6', 'Banco C6'),
        ('Banco Next', 'Banco Next'),
        ('Banco Neon', 'Banco Neon'),
        ('Banco Digio', 'Banco Digio'),
        ('Banco Will', 'Banco Will'),
        ('Banco Sofisa', 'Banco Sofisa'),
        ('Banco Rendimento', 'Banco Rendimento'),
        ('Carteira Digital', 'Carteira Digital'),
        ('Outro', 'Outro'),
    ]
    
    context = {
        "programas": programas,
        "codigos": codigos,
        "tipos_despesa": TIPOS_DESPESA,
        "tipos_despesa_json": json.dumps(TIPOS_DESPESA),
        "codigos_despesa_json": json.dumps(codigos_json),
        "codigos_por_programa_json": json.dumps(codigos_por_programa),
        "perfil": perfil,
        "solicitacao_editar": solicitacao_editar,
        "bancos_choices": BANCOS_CHOICES,
    }
    if request.method == "POST":
        centro_custo = request.POST.get("centro_custo", "").strip()
        valor_total = 0
        itens_dados = []
        for key, value in request.POST.items():
            if key.startswith("valor_") and value:
                try:
                    idx = key.replace("valor_", "")
                    # Remover pontos (milhares) e substituir vírgula por ponto para parseFloat
                    valor_limpo = str(value).replace(".", "").replace(",", ".")
                    v = float(valor_limpo)
                    valor_total += v
                    tipo = request.POST.get(f"tipo_despesa_{idx}", "").strip()
                    cod_despesa = request.POST.get(f"cod_despesa_{idx}", "").strip()
                    data_despesa_str = request.POST.get(f"data_despesa_{idx}", "").strip()
                    desc = request.POST.get(f"descricao_{idx}", "").strip()
                    data_despesa = None
                    if data_despesa_str:
                        from datetime import datetime
                        try:
                            data_despesa = datetime.strptime(data_despesa_str, "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            pass
                    itens_dados.append({
                        "idx": idx,  # Guardar o índice original do formulário
                        "tipo_despesa": tipo,
                        "cod_despesa": cod_despesa,
                        "data_despesa": data_despesa,
                        "descricao": desc,
                        "valor": v
                    })
                except (ValueError, TypeError):
                    pass
        if centro_custo:
            # Verificar se é edição de uma solicitação rejeitada
            editar_id = request.POST.get("editar_id", "").strip()
            sol = None
            if editar_id:
                try:
                    sol = SolicitacaoReembolso.objects.get(pk=int(editar_id), user=request.user)
                    # Verificar se pode editar (deve estar rejeitada)
                    if sol.status_gestor != SolicitacaoReembolso.STATUS_REJEITADO and sol.status_gestor_admin != SolicitacaoReembolso.STATUS_REJEITADO:
                        messages.error(request, "Esta solicitação não pode ser editada.")
                        return redirect("intra:reembolso")
                    # Guardar valores antigos para histórico
                    valores_antigos = {
                        'centro_custo': sol.centro_custo,
                        'valor_total': sol.valor_total,
                        'nome_gestor': sol.nome_gestor,
                        'forma_pagamento': sol.forma_pagamento,
                    }
                    # Guardar itens antigos com seus anexos antes de deletar
                    itens_antigos = list(sol.itens.all())
                    anexos_existentes = {}
                    for idx, item_antigo in enumerate(itens_antigos):
                        if item_antigo.anexo:
                            anexos_existentes[idx] = item_antigo.anexo
                    # Deletar itens antigos
                    sol.itens.all().delete()
                    # Resetar status para permitir nova aprovação
                    sol.status_gestor = SolicitacaoReembolso.STATUS_PENDENTE
                    sol.status_gestor_admin = SolicitacaoReembolso.STATUS_PENDENTE
                    sol.status = SolicitacaoReembolso.STATUS_PENDENTE
                    sol.motivo_rejeicao_gestor = ""
                    sol.motivo_rejeicao_gestor_admin = ""
                    sol.aprovado_por_gestor = None
                    sol.aprovado_em_gestor = None
                    sol.aprovado_por_gestor_admin = None
                    sol.aprovado_em_gestor_admin = None
                    sol.pago = False
                    sol.pago_em = None
                    sol.concluido = False
                    sol.concluido_em = None
                    sol.envelope_id_docusign = ""
                    sol.status_docusign = ""
                    # Atualizar data de envio para a data atual
                    sol.criado_em = timezone.now()
                except (SolicitacaoReembolso.DoesNotExist, ValueError):
                    messages.error(request, "Solicitação não encontrada.")
                    return redirect("intra:reembolso")
            
            # Capturar dados de pagamento
            forma_pagamento = request.POST.get("forma_pagamento", "").strip()
            pix_chave = request.POST.get("pix_chave", "").strip()
            pix_banco = request.POST.get("pix_banco", "").strip()
            # Se for "Outro", pegar o valor do campo outro
            if pix_banco == "Outro":
                pix_banco_outro = request.POST.get("pix_banco_outro", "").strip()
                if pix_banco_outro:
                    pix_banco = pix_banco_outro
            pix_cpf = request.POST.get("pix_cpf", "").strip()
            transf_banco = request.POST.get("transf_banco", "").strip()
            # Se for "Outro", pegar o valor do campo outro
            if transf_banco == "Outro":
                transf_banco_outro = request.POST.get("transf_banco_outro", "").strip()
                if transf_banco_outro:
                    transf_banco = transf_banco_outro
            transf_agencia = request.POST.get("transf_agencia", "").strip()
            transf_conta_tipo = request.POST.get("transf_conta_tipo", "").strip()
            transf_conta_numero = request.POST.get("transf_conta_numero", "").strip()
            transf_cpf = request.POST.get("transf_cpf", "").strip()
            
            # Validar CPF/CNPJ
            import re
            def validar_cpf_cnpj(valor):
                if not valor:
                    return False
                numeros = re.sub(r'\D', '', valor)
                return len(numeros) == 11 or len(numeros) == 14
            
            # Capturar nome do gestor
            nome_gestor = request.POST.get("nome_gestor", "").strip()
            
            # Validar CPF/CNPJ
            import re
            def validar_cpf_cnpj(valor):
                if not valor:
                    return False
                numeros = re.sub(r'\D', '', valor)
                return len(numeros) == 11 or len(numeros) == 14
            
            # Flag para indicar se há erro de validação
            erro_validacao = False
            
            if forma_pagamento == "PIX" and pix_cpf:
                if not validar_cpf_cnpj(pix_cpf):
                    messages.error(request, "CPF deve ter 11 dígitos ou CNPJ deve ter 14 dígitos.")
                    erro_validacao = True
            
            if forma_pagamento == "TRANSFERENCIA" and transf_cpf:
                if not validar_cpf_cnpj(transf_cpf):
                    messages.error(request, "CPF deve ter 11 dígitos ou CNPJ deve ter 14 dígitos.")
                    erro_validacao = True
            
            # Se houver erro de validação, renderizar o template com os dados do POST
            if erro_validacao:
                # Preparar contexto com os dados do POST para manter os campos preenchidos
                # Preparar itens do POST para manter os dados
                itens_post = []
                for key, value in request.POST.items():
                    if key.startswith("tipo_despesa_") and value:
                        idx = key.replace("tipo_despesa_", "")
                        item_post = {
                            "idx": idx,
                            "tipo_despesa": value,
                            "cod_despesa": request.POST.get(f"cod_despesa_{idx}", ""),
                            "data_despesa": request.POST.get(f"data_despesa_{idx}", ""),
                            "descricao": request.POST.get(f"descricao_{idx}", ""),
                            "valor": request.POST.get(f"valor_{idx}", ""),
                        }
                        itens_post.append(item_post)
                
                context = {
                    "programas": programas,
                    "codigos": codigos,
                    "tipos_despesa": TIPOS_DESPESA,
                    "tipos_despesa_json": json.dumps(TIPOS_DESPESA),
                    "codigos_despesa_json": json.dumps(codigos_json),
                    "codigos_por_programa_json": json.dumps(codigos_por_programa),
                    "perfil": perfil,
                    "solicitacao_editar": solicitacao_editar,
                    "bancos_choices": BANCOS_CHOICES,
                    "dados_post": {
                        "centro_custo": request.POST.get("centro_custo", ""),
                        "nome_gestor": nome_gestor,
                        "forma_pagamento": forma_pagamento,
                        "pix_chave": pix_chave,
                        "pix_banco": pix_banco,
                        "pix_cpf": pix_cpf,
                        "transf_banco": transf_banco,
                        "transf_agencia": transf_agencia,
                        "transf_conta_tipo": transf_conta_tipo,
                        "transf_conta_numero": transf_conta_numero,
                        "transf_cpf": transf_cpf,
                        "itens": itens_post,
                    }
                }
                return render(request, "intra/reembolso.html", context)
            
            if sol:
                # Atualizar solicitação existente
                alteracoes = []
                num_itens_antigos = len(itens_antigos)
                num_itens_novos = len([i for i in itens_dados if i.get("tipo_despesa")])
                
                # Verificar motivo de rejeição anterior
                motivo_rejeicao_anterior = ""
                if sol.motivo_rejeicao_gestor:
                    motivo_rejeicao_anterior = f"Motivo da rejeição pelo gestor: {sol.motivo_rejeicao_gestor}"
                elif sol.motivo_rejeicao_gestor_admin:
                    motivo_rejeicao_anterior = f"Motivo da rejeição pelo gestor administrativo: {sol.motivo_rejeicao_gestor_admin}"
                
                if sol.centro_custo != centro_custo:
                    alteracoes.append(f"Centro de custo alterado de '{sol.centro_custo}' para '{centro_custo}'")
                if float(sol.valor_total) != valor_total:
                    alteracoes.append(f"Valor total alterado de R$ {sol.valor_total:.2f} para R$ {valor_total:.2f}")
                if sol.nome_gestor != nome_gestor:
                    alteracoes.append(f"Nome do gestor alterado de '{sol.nome_gestor or '—'}' para '{nome_gestor or '—'}'")
                if sol.forma_pagamento != forma_pagamento:
                    alteracoes.append(f"Forma de pagamento alterada de '{sol.forma_pagamento or '—'}' para '{forma_pagamento or '—'}'")
                
                # Verificar mudanças nos itens
                if num_itens_antigos != num_itens_novos:
                    alteracoes.append(f"Número de itens alterado de {num_itens_antigos} para {num_itens_novos}")
                    if num_itens_novos > num_itens_antigos:
                        alteracoes.append(f"Item(ns) adicionado(s): {num_itens_novos - num_itens_antigos}")
                    else:
                        alteracoes.append(f"Item(ns) removido(s): {num_itens_antigos - num_itens_novos}")
                
                sol.centro_custo = centro_custo
                sol.cod_despesa = ""  # Não é mais obrigatório no nível da solicitação
                sol.valor_total = valor_total
                sol.forma_pagamento = forma_pagamento if forma_pagamento else None
                sol.pix_chave = pix_chave if pix_chave else None
                sol.pix_banco = pix_banco if pix_banco else None
                sol.pix_cpf = pix_cpf if pix_cpf else None
                sol.transf_banco = transf_banco if transf_banco else None
                sol.transf_agencia = transf_agencia if transf_agencia else None
                sol.transf_conta_tipo = transf_conta_tipo if transf_conta_tipo else None
                sol.transf_conta_numero = transf_conta_numero if transf_conta_numero else None
                sol.transf_cpf = transf_cpf if transf_cpf else None
                sol.nome_gestor = nome_gestor if nome_gestor else None
                sol.save()
                
                # Registrar no histórico
                descricao_historico = "Solicitação editada e reenviada após rejeição."
                if motivo_rejeicao_anterior:
                    descricao_historico += " " + motivo_rejeicao_anterior
                if alteracoes:
                    descricao_historico += " Alterações realizadas: " + "; ".join(alteracoes)
                HistoricoReembolso.objects.create(
                    solicitacao=sol,
                    acao="Solicitação editada",
                    descricao=descricao_historico,
                    usuario=request.user
                )
            else:
                # Criar nova solicitação
                sol = SolicitacaoReembolso.objects.create(
                user=request.user,
                centro_custo=centro_custo,
                cod_despesa="",  # Não é mais obrigatório no nível da solicitação
                valor_total=valor_total,
                forma_pagamento=forma_pagamento if forma_pagamento else None,
                pix_chave=pix_chave if pix_chave else None,
                pix_banco=pix_banco if pix_banco else None,
                pix_cpf=pix_cpf if pix_cpf else None,
                transf_banco=transf_banco if transf_banco else None,
                transf_agencia=transf_agencia if transf_agencia else None,
                transf_conta_tipo=transf_conta_tipo if transf_conta_tipo else None,
                transf_conta_numero=transf_conta_numero if transf_conta_numero else None,
                transf_cpf=transf_cpf if transf_cpf else None,
                nome_gestor=nome_gestor if nome_gestor else None,
            )
            for item in itens_dados:
                if item["tipo_despesa"]:
                    # Validar descrição obrigatória
                    if not item.get("descricao") or not item["descricao"].strip():
                        messages.error(request, "O campo 'Descrição' é obrigatório para todos os itens de despesa.")
                        return redirect("intra:reembolso")
                    
                    # Buscar anexo correspondente usando o índice original do formulário
                    anexo = None
                    anexo_key = f"anexo_{item['idx']}"
                    if anexo_key in request.FILES:
                        anexo = request.FILES[anexo_key]
                        print(f"[DEBUG S3] Arquivo recebido: {anexo_key}, nome: {anexo.name}, tamanho: {anexo.size}")
                        print(f"[DEBUG S3] Storage configurado: {settings.DEFAULT_FILE_STORAGE}")
                        print(f"[DEBUG S3] Bucket: {getattr(settings, 'AWS_STORAGE_BUCKET_NAME', 'NÃO CONFIGURADO')}")
                        logger.info(f"[DEBUG S3] Arquivo recebido: {anexo_key}, nome: {anexo.name}, tamanho: {anexo.size}")
                        logger.info(f"[DEBUG S3] Storage configurado: {settings.DEFAULT_FILE_STORAGE}")
                        logger.info(f"[DEBUG S3] Bucket: {getattr(settings, 'AWS_STORAGE_BUCKET_NAME', 'NÃO CONFIGURADO')}")
                    # Se não houver novo anexo e estiver editando, manter anexo existente
                    elif editar_id and 'anexos_existentes' in locals():
                        try:
                            item_idx = int(item['idx'])
                            if item_idx in anexos_existentes:
                                # Manter o anexo existente copiando a referência
                                anexo_existente = anexos_existentes[item_idx]
                                # Usar o arquivo existente diretamente (já está no bucket)
                                anexo = anexo_existente
                        except (ValueError, KeyError):
                            anexo = None
                    
                    # Debug antes de salvar
                    if anexo:
                        print(f"[DEBUG S3] Antes de salvar - Verificando storage do campo anexo")
                        field = ItemReembolso._meta.get_field('anexo')
                        print(f"[DEBUG S3] Storage do campo: {field.storage}")
                        print(f"[DEBUG S3] Tipo do storage: {type(field.storage)}")
                    
                    item_obj = ItemReembolso.objects.create(
                        solicitacao=sol,
                        tipo_despesa=item["tipo_despesa"],
                        cod_despesa=item["cod_despesa"],
                        data_despesa=item["data_despesa"],
                        descricao=item["descricao"],
                        valor=item["valor"],
                        anexo=anexo,
                    )
                    
                    # Debug após salvar
                    if item_obj.anexo:
                        print(f"[DEBUG S3] Item salvo - ID: {item_obj.pk}")
                        print(f"[DEBUG S3] Anexo name: {item_obj.anexo.name}")
                        print(f"[DEBUG S3] Storage class: {type(item_obj.anexo.storage)}")
                        print(f"[DEBUG S3] Storage object: {item_obj.anexo.storage}")
                        logger.info(f"[DEBUG S3] Item salvo - ID: {item_obj.pk}")
                        logger.info(f"[DEBUG S3] Anexo name: {item_obj.anexo.name}")
                        try:
                            # Verificar se o arquivo existe no S3
                            storage = item_obj.anexo.storage
                            file_exists = storage.exists(item_obj.anexo.name)
                            print(f"[DEBUG S3] Arquivo existe no S3? {file_exists}")
                            logger.info(f"[DEBUG S3] Arquivo existe no S3? {file_exists}")
                            
                            anexo_url = item_obj.anexo.url
                            print(f"[DEBUG S3] Anexo URL: {anexo_url}")
                            logger.info(f"[DEBUG S3] Anexo URL: {anexo_url}")
                            
                            # Tentar fazer upload manual se não existir
                            if not file_exists and anexo:
                                print(f"[DEBUG S3] Arquivo não existe no S3, tentando upload manual...")
                                try:
                                    # Ler o arquivo do request.FILES
                                    anexo.seek(0)  # Voltar ao início do arquivo
                                    storage.save(item_obj.anexo.name, anexo)
                                    print(f"[DEBUG S3] Upload manual realizado com sucesso!")
                                    logger.info(f"[DEBUG S3] Upload manual realizado com sucesso!")
                                except Exception as upload_error:
                                    print(f"[DEBUG S3] ERRO no upload manual: {str(upload_error)}")
                                    import traceback
                                    print(f"[DEBUG S3] Traceback: {traceback.format_exc()}")
                                    logger.error(f"[DEBUG S3] ERRO no upload manual: {str(upload_error)}")
                                    logger.error(f"[DEBUG S3] Traceback: {traceback.format_exc()}")
                        except Exception as e:
                            print(f"[DEBUG S3] ERRO ao verificar/obter URL do anexo: {str(e)}")
                            import traceback
                            print(f"[DEBUG S3] Traceback: {traceback.format_exc()}")
                            logger.error(f"[DEBUG S3] Erro ao obter URL do anexo: {str(e)}")
                            logger.error(f"[DEBUG S3] Traceback: {traceback.format_exc()}")
                    else:
                        print(f"[DEBUG S3] Item salvo sem anexo - ID: {item_obj.pk}")
                        logger.warning(f"[DEBUG S3] Item salvo sem anexo - ID: {item_obj.pk}")
            # Verificar se o solicitante é gestor - se for, pular primeiro nível
            is_solicitante_gestor = _is_gestor_simples(request.user)
            
            if is_solicitante_gestor:
                # Se o solicitante é gestor, vai direto para gestor administrativo
                sol.status_gestor = SolicitacaoReembolso.STATUS_APROVADO
                sol.aprovado_por_gestor = request.user  # Auto-aprovado
                sol.aprovado_em_gestor = timezone.now()
                sol.save()
                # Registrar no histórico (já foi criado antes, então não precisa criar novamente)
                # Enviar e-mail aos gestores administrativos
                _enviar_email_nova_solicitacao_gestor_admin(sol, request)
            else:
                # Se não é gestor, enviar e-mail ao gestor do solicitante
                try:
                    perfil = PerfilSolicitante.objects.get(user=request.user)
                    if perfil.email_gestor:
                        # Enviar e-mail ao gestor
                        _enviar_email_nova_solicitacao_gestor(sol, request, perfil.email_gestor)
                except PerfilSolicitante.DoesNotExist:
                    pass
            # Verificar se foi edição
            editar_id_final = request.POST.get("editar_id", "").strip()
            if editar_id_final:
                messages.success(request, f"Solicitação de reembolso editada e reenviada com sucesso! ID da solicitação: #{sol.pk}")
                return redirect("intra:meus_reembolsos")
            else:
                messages.success(request, f"Solicitação de reembolso enviada com sucesso! ID da solicitação: #{sol.pk}")
        return redirect("intra:reembolso")
    return render(request, "intra/reembolso.html", context)


@login_required
def ultimos_reembolsos(request):
    """Lista os últimos reembolsos aprovados e rejeitados para o gestor administrativo."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    
    # Processar pagamentos programados que já passaram da data
    _processar_pagamentos_programados()
    
    # Capturar filtros do GET
    data_inicio = request.GET.get('data_inicio', '')
    data_fim = request.GET.get('data_fim', '')
    status_filtro = request.GET.get('status', '')
    centro_custo_filtro = request.GET.get('centro_custo', '')
    tipo_despesa_filtro = request.GET.get('tipo_despesa', '')
    id_filtro = request.GET.get('id', '').strip()
    tabela_atual = request.GET.get('tabela_atual', 'em-processo')  # Tabela atual sendo visualizada
    
    # Construir filtros base para 3 categorias: Em Processo, Concluído, Rejeitado
    # Em Processo: solicitações aprovadas pelo gestor que não são concluídas ou rejeitadas pelo gestor admin
    # Inclui também solicitações com pagamento agendado (STATUS_PAGAMENTO_AGENDADO)
    # NÃO inclui: aguardando_gestor (status_gestor = PENDENTE) e rejeitado_gestor (status_gestor = REJEITADO)
    filtros_em_processo = (
        Q(
            status_gestor=SolicitacaoReembolso.STATUS_APROVADO,  # Apenas aprovadas pelo gestor
            concluido=False
        ) & ~Q(
            status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO
        )
    ) | Q(
        status=SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO,
        pago=False,
        concluido=False
    )
    
    filtros_rejeitados = Q(
        status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO
    )
    
    filtros_concluidos = Q(
        status=SolicitacaoReembolso.STATUS_CONCLUIDO
    ) | Q(
        concluido=True  # Compatibilidade com dados antigos
    )
    
    # Aplicar filtros comuns
    if id_filtro:
        try:
            id_valor = int(id_filtro)
            filtros_em_processo &= Q(pk=id_valor)
            filtros_rejeitados &= Q(pk=id_valor)
            filtros_concluidos &= Q(pk=id_valor)
        except ValueError:
            pass
    
    if data_inicio:
        try:
            data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            filtros_em_processo &= Q(criado_em__date__gte=data_inicio_obj)
            filtros_rejeitados &= Q(aprovado_em_gestor_admin__date__gte=data_inicio_obj)
            filtros_concluidos &= Q(concluido_em__date__gte=data_inicio_obj)
        except ValueError:
            pass
    
    if data_fim:
        try:
            data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
            filtros_em_processo &= Q(criado_em__date__lte=data_fim_obj)
            filtros_rejeitados &= Q(aprovado_em_gestor_admin__date__lte=data_fim_obj)
            filtros_concluidos &= Q(concluido_em__date__lte=data_fim_obj)
        except ValueError:
            pass
    
    if centro_custo_filtro:
        filtros_em_processo &= Q(centro_custo=centro_custo_filtro)
        filtros_rejeitados &= Q(centro_custo=centro_custo_filtro)
        filtros_concluidos &= Q(centro_custo=centro_custo_filtro)
    
    # Filtro de status - aplicar apenas na tabela atual
    # IMPORTANTE: Este filtro deve ser aplicado DEPOIS dos outros filtros comuns
    # para garantir que apenas a tabela atual seja filtrada
    if status_filtro:
        if tabela_atual == 'concluidos':
            # Para concluídos, filtrar por status CONCLUIDO
            if status_filtro == SolicitacaoReembolso.STATUS_CONCLUIDO:
                # Aplicar filtro de status: mostrar APENAS os que têm status=CONCLUIDO
                # Substituir o filtro base para garantir que mostra apenas status=CONCLUIDO
                # Manter os outros filtros já aplicados (id, data, centro_custo, etc)
                filtros_base_concluidos = Q(status=SolicitacaoReembolso.STATUS_CONCLUIDO)
                # Reaplicar filtros comuns que já foram aplicados
                if id_filtro:
                    try:
                        id_valor = int(id_filtro)
                        filtros_base_concluidos &= Q(pk=id_valor)
                    except ValueError:
                        pass
                if data_inicio:
                    try:
                        data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                        filtros_base_concluidos &= Q(concluido_em__date__gte=data_inicio_obj)
                    except ValueError:
                        pass
                if data_fim:
                    try:
                        data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                        filtros_base_concluidos &= Q(concluido_em__date__lte=data_fim_obj)
                    except ValueError:
                        pass
                if centro_custo_filtro:
                    filtros_base_concluidos &= Q(centro_custo=centro_custo_filtro)
                filtros_concluidos = filtros_base_concluidos
            else:
                filtros_concluidos = Q(pk__in=[])  # Nenhum resultado se o status não for CONCLUIDO
        elif tabela_atual == 'rejeitados':
            # Para rejeitados, verificar status_gestor_admin
            if status_filtro == SolicitacaoReembolso.STATUS_REJEITADO:
                # O filtro base já inclui status_gestor_admin=REJEITADO, então apenas manter
                # Mas vamos garantir que também verifica o campo status
                filtros_rejeitados = filtros_rejeitados & (Q(status=SolicitacaoReembolso.STATUS_REJEITADO) | Q(status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO))
            else:
                filtros_rejeitados = Q(pk__in=[])  # Nenhum resultado se o status não for REJEITADO
        else:  # em-processo
            # Para em-processo, aplicar filtro de status específico
            # Substituir o filtro base para garantir que mostra APENAS o status selecionado
            # O filtro base de em-processo inclui várias condições, mas quando há filtro de status,
            # devemos mostrar APENAS o status selecionado
            filtros_base_em_processo = Q(status=status_filtro, concluido=False) & ~Q(status_gestor_admin=SolicitacaoReembolso.STATUS_REJEITADO)
            # Reaplicar filtros comuns que já foram aplicados
            if id_filtro:
                try:
                    id_valor = int(id_filtro)
                    filtros_base_em_processo &= Q(pk=id_valor)
                except ValueError:
                    pass
            if data_inicio:
                try:
                    data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                    filtros_base_em_processo &= Q(criado_em__date__gte=data_inicio_obj)
                except ValueError:
                    pass
            if data_fim:
                try:
                    data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                    filtros_base_em_processo &= Q(criado_em__date__lte=data_fim_obj)
                except ValueError:
                    pass
            if centro_custo_filtro:
                filtros_base_em_processo &= Q(centro_custo=centro_custo_filtro)
            filtros_em_processo = filtros_base_em_processo
    
    # Filtro de tipo de despesa (via itens)
    if tipo_despesa_filtro:
        # Obter IDs das solicitações que têm itens com esse tipo de despesa
        filtros_itens = Q(tipo_despesa=tipo_despesa_filtro)
        
        if data_inicio:
            try:
                data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                filtros_itens &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
            except ValueError:
                pass
        
        if data_fim:
            try:
                data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                filtros_itens &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
            except ValueError:
                pass
        
        if centro_custo_filtro:
            filtros_itens &= Q(solicitacao__centro_custo=centro_custo_filtro)
        
        if id_filtro:
            try:
                id_valor = int(id_filtro)
                filtros_itens &= Q(solicitacao__pk=id_valor)
            except ValueError:
                pass
        
        # Se houver filtro de status, aplicar também no filtro de itens
        if status_filtro:
            filtros_itens &= Q(solicitacao__status=status_filtro)
        
        solicitacoes_ids = ItemReembolso.objects.filter(filtros_itens).values_list('solicitacao_id', flat=True).distinct()
        # Aplicar apenas na tabela atual se houver filtro de status
        if status_filtro:
            if tabela_atual == 'concluidos':
                filtros_concluidos &= Q(pk__in=solicitacoes_ids)
            elif tabela_atual == 'rejeitados':
                filtros_rejeitados &= Q(pk__in=solicitacoes_ids)
            else:  # em-processo
                # Garantir que o filtro de status não seja sobrescrito
                filtros_em_processo = filtros_em_processo & Q(pk__in=solicitacoes_ids)
        else:
            filtros_em_processo &= Q(pk__in=solicitacoes_ids)
            filtros_rejeitados &= Q(pk__in=solicitacoes_ids)
            filtros_concluidos &= Q(pk__in=solicitacoes_ids)
    
    # Buscar solicitações nas 3 categorias com prefetch_related para evitar N+1
    em_processo_queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
        "user__perfil_solicitante"
    ).filter(
        filtros_em_processo
    ).order_by('-criado_em')
    
    rejeitados_queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
        "user__perfil_solicitante"
    ).filter(
        filtros_rejeitados
    ).order_by('-aprovado_em_gestor_admin')
    
    concluidos_queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
        "user__perfil_solicitante"
    ).filter(
        filtros_concluidos
    ).order_by('-concluido_em')
    
    # Paginação - 10 por página
    page_em_processo = request.GET.get('page_em_processo', 1)
    page_rejeitados = request.GET.get('page_rejeitados', 1)
    page_concluidos = request.GET.get('page_concluidos', 1)
    
    paginator_em_processo = Paginator(em_processo_queryset, 10)
    paginator_rejeitados = Paginator(rejeitados_queryset, 10)
    paginator_concluidos = Paginator(concluidos_queryset, 10)
    
    page_obj_em_processo = paginator_em_processo.get_page(page_em_processo)
    page_obj_rejeitados = paginator_rejeitados.get_page(page_rejeitados)
    page_obj_concluidos = paginator_concluidos.get_page(page_concluidos)
    
    # Consultar status DocuSign em paralelo para todas as solicitações em processo
    em_processo_list = list(page_obj_em_processo)
    status_docusign_map = _get_status_assinatura_docusign_parallel(em_processo_list)
    
    # Preparar dados para exibição - Em Processo
    em_processo_com_data = []
    for sol in em_processo_list:
        sol.criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
        # Usar prefetch_related para evitar query adicional
        try:
            perfil = sol.user.perfil_solicitante
            sol.nome_solicitante = perfil.nome_solicitante or ""
        except (PerfilSolicitante.DoesNotExist, AttributeError):
            sol.nome_solicitante = ""
        sol.email_solicitante = sol.user.email or sol.user.username or "—"
        # Usar resultado do cache/paralelo
        sol.status_docusign_info = status_docusign_map.get(sol.pk)
        sol.status_descritivo = _get_status_descritivo(sol)
        # Pode marcar como pago se tem pagamento agendado
        sol.pode_marcar_pago = (sol.status == SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO and not sol.pago)
        em_processo_com_data.append(sol)
    
    # Preparar dados para exibição - Rejeitados
    rejeitados_list = list(page_obj_rejeitados)
    rejeitados_com_data = []
    for sol in rejeitados_list:
        sol.criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
        sol.aprovado_em_brasilia = localtime(sol.aprovado_em_gestor_admin) if sol.aprovado_em_gestor_admin else None
        # Usar prefetch_related para evitar query adicional
        try:
            perfil = sol.user.perfil_solicitante
            sol.nome_solicitante = perfil.nome_solicitante or ""
        except (PerfilSolicitante.DoesNotExist, AttributeError):
            sol.nome_solicitante = ""
        sol.email_solicitante = sol.user.email or sol.user.username or "—"
        sol.status_descritivo = _get_status_descritivo(sol)
        rejeitados_com_data.append(sol)
    
    # Preparar dados para exibição - Concluídos
    concluidos_list = list(page_obj_concluidos)
    concluidos_com_data = []
    for sol in concluidos_list:
        sol.criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
        sol.concluido_em_brasilia = localtime(sol.concluido_em) if sol.concluido_em else None
        # Usar prefetch_related para evitar query adicional
        try:
            perfil = sol.user.perfil_solicitante
            sol.nome_solicitante = perfil.nome_solicitante or ""
        except (PerfilSolicitante.DoesNotExist, AttributeError):
            sol.nome_solicitante = ""
        sol.email_solicitante = sol.user.email or sol.user.username or "—"
        sol.status_descritivo = _get_status_descritivo(sol)
        concluidos_com_data.append(sol)
    
    # Função helper para construir URL sem um filtro específico
    def get_url_without_filter(exclude_param):
        params = {}
        if data_inicio and exclude_param != 'data_inicio':
            params['data_inicio'] = data_inicio
        if data_fim and exclude_param != 'data_fim':
            params['data_fim'] = data_fim
        if status_filtro and exclude_param != 'status':
            params['status'] = status_filtro
        if centro_custo_filtro and exclude_param != 'centro_custo':
            params['centro_custo'] = centro_custo_filtro
        if tipo_despesa_filtro and exclude_param != 'tipo_despesa':
            params['tipo_despesa'] = tipo_despesa_filtro
        if id_filtro and exclude_param != 'id':
            params['id'] = id_filtro
        
        from django.http import QueryDict
        query_string = QueryDict('', mutable=True)
        query_string.update(params)
        return '?' + query_string.urlencode() if params else ''
    
    # Labels para os filtros - usar os nomes do STATUS_CHOICES
    status_labels = dict(SolicitacaoReembolso.STATUS_CHOICES)
    centro_labels = dict(CENTROS_CUSTO)
    tipo_labels = dict(TIPOS_DESPESA)
    
    # Construir lista de badges de filtros ativos
    active_filter_badges = []
    if data_inicio:
        active_filter_badges.append({
            'label': f'Data Início: {data_inicio}',
            'remove_url': get_url_without_filter('data_inicio')
        })
    if data_fim:
        active_filter_badges.append({
            'label': f'Data Fim: {data_fim}',
            'remove_url': get_url_without_filter('data_fim')
        })
    if status_filtro:
        active_filter_badges.append({
            'label': f'Status: {status_labels.get(status_filtro, status_filtro)}',
            'remove_url': get_url_without_filter('status')
        })
    if centro_custo_filtro:
        active_filter_badges.append({
            'label': f'Centro de Custo: {centro_labels.get(centro_custo_filtro, centro_custo_filtro)}',
            'remove_url': get_url_without_filter('centro_custo')
        })
    if tipo_despesa_filtro:
        active_filter_badges.append({
            'label': f'Tipo de Despesa: {tipo_labels.get(tipo_despesa_filtro, tipo_despesa_filtro)}',
            'remove_url': get_url_without_filter('tipo_despesa')
        })
    if id_filtro:
        active_filter_badges.append({
            'label': f'ID: #{id_filtro}',
            'remove_url': get_url_without_filter('id')
        })
    
    # Opções para status baseado na tabela atual
    # Em Processo: apenas status que aparecem nessa tabela
    # Concluídos: apenas CONCLUIDO
    # Rejeitados: apenas REJEITADO
    if tabela_atual == 'concluidos':
        status_choices = [
            (SolicitacaoReembolso.STATUS_CONCLUIDO, 'Concluído'),
        ]
    elif tabela_atual == 'rejeitados':
        status_choices = [
            (SolicitacaoReembolso.STATUS_REJEITADO, 'Rejeitado'),
        ]
    else:  # em-processo
        status_choices = [
            (SolicitacaoReembolso.STATUS_AGUARDANDO_PAGAMENTO, 'Aprovado - Aguardando pagamento'),
            (SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO, 'Solicitação aprovada - Pagamento agendado'),
            (SolicitacaoReembolso.STATUS_PAGO_AGUARDANDO_ASSINATURAS, 'Pago - Aguardando assinaturas'),
        ]
    
    return render(
        request,
        "intra/ultimos_reembolsos.html",
        {
            "em_processo": em_processo_com_data,
            "rejeitados": rejeitados_com_data,
            "concluidos": concluidos_com_data,
            "page_obj_em_processo": page_obj_em_processo,
            "page_obj_rejeitados": page_obj_rejeitados,
            "page_obj_concluidos": page_obj_concluidos,
            "tabela_atual": tabela_atual,
            "filtros": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "status": status_filtro,
                "centro_custo": centro_custo_filtro,
                "tipo_despesa": tipo_despesa_filtro,
                "id": id_filtro,
            },
            "status_choices": status_choices,
            "centros_custo": CENTROS_CUSTO,
            "tipos_despesa": TIPOS_DESPESA,
            "active_filter_badges": active_filter_badges,
        },
    )


@login_required
def ultimos_reembolsos_gestor(request):
    """Lista os últimos reembolsos aprovados e rejeitados pelo gestor simples."""
    if not _is_gestor_simples(request.user):
        return HttpResponse("Acesso restrito a Gestores.", status=403)
    
    # Capturar filtros do GET
    data_inicio = request.GET.get('data_inicio', '')
    data_fim = request.GET.get('data_fim', '')
    status_filtro = request.GET.get('status', '')
    centro_custo_filtro = request.GET.get('centro_custo', '')
    tipo_despesa_filtro = request.GET.get('tipo_despesa', '')
    id_filtro = request.GET.get('id', '').strip()
    tabela_atual = request.GET.get('tabela_atual', 'em-processo')  # Tabela atual sendo visualizada
    
    # Construir filtros base para 3 categorias: Em Processo, Concluído, Rejeitado
    # Em Processo: tudo que não é concluído ou rejeitado pelo gestor
    filtros_em_processo = Q(
        aprovado_por_gestor=request.user,
        concluido=False
    ) & ~Q(
        status_gestor=SolicitacaoReembolso.STATUS_REJEITADO
    )
    
    filtros_rejeitados = Q(
        status_gestor=SolicitacaoReembolso.STATUS_REJEITADO,
        aprovado_por_gestor=request.user
    )
    
    filtros_concluidos = Q(
        concluido=True,
        aprovado_por_gestor=request.user
    )
    
    # Aplicar filtros comuns
    if id_filtro:
        try:
            id_valor = int(id_filtro)
            filtros_em_processo &= Q(pk=id_valor)
            filtros_rejeitados &= Q(pk=id_valor)
            filtros_concluidos &= Q(pk=id_valor)
        except ValueError:
            pass
    
    if data_inicio:
        try:
            data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
            filtros_em_processo &= Q(criado_em__date__gte=data_inicio_obj)
            filtros_rejeitados &= Q(aprovado_em_gestor__date__gte=data_inicio_obj)
            filtros_concluidos &= Q(concluido_em__date__gte=data_inicio_obj)
        except ValueError:
            pass
    
    if data_fim:
        try:
            data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
            filtros_em_processo &= Q(criado_em__date__lte=data_fim_obj)
            filtros_rejeitados &= Q(aprovado_em_gestor__date__lte=data_fim_obj)
            filtros_concluidos &= Q(concluido_em__date__lte=data_fim_obj)
        except ValueError:
            pass
    
    if centro_custo_filtro:
        filtros_em_processo &= Q(centro_custo=centro_custo_filtro)
        filtros_rejeitados &= Q(centro_custo=centro_custo_filtro)
        filtros_concluidos &= Q(centro_custo=centro_custo_filtro)
    
    # Filtro de status - aplicar apenas na tabela atual
    # IMPORTANTE: Este filtro deve ser aplicado DEPOIS dos outros filtros comuns
    # para garantir que apenas a tabela atual seja filtrada
    if status_filtro:
        if tabela_atual == 'concluidos':
            # Para concluídos, filtrar por status CONCLUIDO
            if status_filtro == SolicitacaoReembolso.STATUS_CONCLUIDO:
                # Aplicar filtro de status: mostrar APENAS os que têm status=CONCLUIDO
                # Substituir o filtro base para garantir que mostra apenas status=CONCLUIDO
                # Manter os outros filtros já aplicados (id, data, centro_custo, etc)
                filtros_base_concluidos = Q(status=SolicitacaoReembolso.STATUS_CONCLUIDO, aprovado_por_gestor=request.user)
                # Reaplicar filtros comuns que já foram aplicados
                if id_filtro:
                    try:
                        id_valor = int(id_filtro)
                        filtros_base_concluidos &= Q(pk=id_valor)
                    except ValueError:
                        pass
                if data_inicio:
                    try:
                        data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                        filtros_base_concluidos &= Q(concluido_em__date__gte=data_inicio_obj)
                    except ValueError:
                        pass
                if data_fim:
                    try:
                        data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                        filtros_base_concluidos &= Q(concluido_em__date__lte=data_fim_obj)
                    except ValueError:
                        pass
                if centro_custo_filtro:
                    filtros_base_concluidos &= Q(centro_custo=centro_custo_filtro)
                filtros_concluidos = filtros_base_concluidos
            else:
                filtros_concluidos = Q(pk__in=[])  # Nenhum resultado se o status não for CONCLUIDO
        elif tabela_atual == 'rejeitados':
            # Para rejeitados, verificar status_gestor
            if status_filtro == SolicitacaoReembolso.STATUS_REJEITADO:
                # O filtro base já inclui status_gestor=REJEITADO, então apenas manter
                # Mas vamos garantir que também verifica o campo status
                filtros_rejeitados = filtros_rejeitados & (Q(status=SolicitacaoReembolso.STATUS_REJEITADO) | Q(status_gestor=SolicitacaoReembolso.STATUS_REJEITADO))
            else:
                filtros_rejeitados = Q(pk__in=[])  # Nenhum resultado se o status não for REJEITADO
        else:  # em-processo
            # Para em-processo, aplicar filtro de status específico
            # Substituir o filtro base para garantir que mostra APENAS o status selecionado
            # O filtro base de em-processo inclui várias condições, mas quando há filtro de status,
            # devemos mostrar APENAS o status selecionado
            filtros_base_em_processo = Q(status=status_filtro, aprovado_por_gestor=request.user, concluido=False) & ~Q(status_gestor=SolicitacaoReembolso.STATUS_REJEITADO)
            # Reaplicar filtros comuns que já foram aplicados
            if id_filtro:
                try:
                    id_valor = int(id_filtro)
                    filtros_base_em_processo &= Q(pk=id_valor)
                except ValueError:
                    pass
            if data_inicio:
                try:
                    data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                    filtros_base_em_processo &= Q(criado_em__date__gte=data_inicio_obj)
                except ValueError:
                    pass
            if data_fim:
                try:
                    data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                    filtros_base_em_processo &= Q(criado_em__date__lte=data_fim_obj)
                except ValueError:
                    pass
            if centro_custo_filtro:
                filtros_base_em_processo &= Q(centro_custo=centro_custo_filtro)
            filtros_em_processo = filtros_base_em_processo
    
    # Filtro de tipo de despesa (via itens)
    if tipo_despesa_filtro:
        filtros_itens = Q(tipo_despesa=tipo_despesa_filtro)
        
        if data_inicio:
            try:
                data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                filtros_itens &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
            except ValueError:
                pass
        
        if data_fim:
            try:
                data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                filtros_itens &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
            except ValueError:
                pass
        
        if centro_custo_filtro:
            filtros_itens &= Q(solicitacao__centro_custo=centro_custo_filtro)
        
        if id_filtro:
            try:
                id_valor = int(id_filtro)
                filtros_itens &= Q(solicitacao__pk=id_valor)
            except ValueError:
                pass
        
        # Se houver filtro de status, aplicar também no filtro de itens
        if status_filtro:
            filtros_itens &= Q(solicitacao__status=status_filtro)
        
        solicitacoes_ids = ItemReembolso.objects.filter(filtros_itens).values_list('solicitacao_id', flat=True).distinct()
        # Aplicar apenas na tabela atual se houver filtro de status
        if status_filtro:
            if tabela_atual == 'concluidos':
                filtros_concluidos &= Q(pk__in=solicitacoes_ids)
            elif tabela_atual == 'rejeitados':
                filtros_rejeitados &= Q(pk__in=solicitacoes_ids)
            else:  # em-processo
                # Garantir que o filtro de status não seja sobrescrito
                filtros_em_processo = filtros_em_processo & Q(pk__in=solicitacoes_ids)
        else:
            filtros_em_processo &= Q(pk__in=solicitacoes_ids)
            filtros_rejeitados &= Q(pk__in=solicitacoes_ids)
            filtros_concluidos &= Q(pk__in=solicitacoes_ids)
    
    # Buscar solicitações nas 3 categorias com prefetch_related para evitar N+1
    em_processo_queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
        "user__perfil_solicitante"
    ).filter(
        filtros_em_processo
    ).order_by('-criado_em')
    
    rejeitados_queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
        "user__perfil_solicitante"
    ).filter(
        filtros_rejeitados
    ).order_by('-aprovado_em_gestor')
    
    concluidos_queryset = SolicitacaoReembolso.objects.select_related("user").prefetch_related(
        "user__perfil_solicitante"
    ).filter(
        filtros_concluidos
    ).order_by('-concluido_em')
    
    # Paginação - 10 por página
    page_em_processo = request.GET.get('page_em_processo', 1)
    page_rejeitados = request.GET.get('page_rejeitados', 1)
    page_concluidos = request.GET.get('page_concluidos', 1)
    
    paginator_em_processo = Paginator(em_processo_queryset, 10)
    paginator_rejeitados = Paginator(rejeitados_queryset, 10)
    paginator_concluidos = Paginator(concluidos_queryset, 10)
    
    page_obj_em_processo = paginator_em_processo.get_page(page_em_processo)
    page_obj_rejeitados = paginator_rejeitados.get_page(page_rejeitados)
    page_obj_concluidos = paginator_concluidos.get_page(page_concluidos)
    
    # Consultar status DocuSign em paralelo para todas as solicitações
    em_processo_list = list(page_obj_em_processo)
    status_docusign_map = _get_status_assinatura_docusign_parallel(em_processo_list)
    
    # Preparar dados para exibição - Em Processo
    em_processo_com_data = []
    for sol in em_processo_list:
        sol.criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
        sol.aprovado_em_brasilia = localtime(sol.aprovado_em_gestor) if sol.aprovado_em_gestor else None
        # Usar prefetch_related para evitar query adicional
        try:
            perfil = sol.user.perfil_solicitante
            sol.nome_solicitante = perfil.nome_solicitante or ""
        except (PerfilSolicitante.DoesNotExist, AttributeError):
            sol.nome_solicitante = ""
        sol.email_solicitante = sol.user.email or sol.user.username or "—"
        # Usar resultado do cache/paralelo
        sol.status_docusign_info = status_docusign_map.get(sol.pk)
        sol.status_descritivo = _get_status_descritivo(sol)
        em_processo_com_data.append(sol)
    
    rejeitados_list = list(page_obj_rejeitados)
    rejeitados_com_data = []
    for sol in rejeitados_list:
        sol.criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
        sol.aprovado_em_brasilia = localtime(sol.aprovado_em_gestor) if sol.aprovado_em_gestor else None
        # Usar prefetch_related para evitar query adicional
        try:
            perfil = sol.user.perfil_solicitante
            sol.nome_solicitante = perfil.nome_solicitante or ""
        except (PerfilSolicitante.DoesNotExist, AttributeError):
            sol.nome_solicitante = ""
        sol.email_solicitante = sol.user.email or sol.user.username or "—"
        # Adicionar status descritivo
        sol.status_descritivo = _get_status_descritivo(sol)
        rejeitados_com_data.append(sol)
    
    concluidos_list = list(page_obj_concluidos)
    concluidos_com_data = []
    for sol in concluidos_list:
        sol.criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
        sol.concluido_em_brasilia = localtime(sol.concluido_em) if sol.concluido_em else None
        # Usar prefetch_related para evitar query adicional
        try:
            perfil = sol.user.perfil_solicitante
            sol.nome_solicitante = perfil.nome_solicitante or ""
        except (PerfilSolicitante.DoesNotExist, AttributeError):
            sol.nome_solicitante = ""
        sol.email_solicitante = sol.user.email or sol.user.username or "—"
        # Adicionar status descritivo
        sol.status_descritivo = _get_status_descritivo(sol)
        concluidos_com_data.append(sol)
    
    # Função helper para construir URL sem um filtro específico
    def get_url_without_filter(exclude_param):
        params = {}
        if data_inicio and exclude_param != 'data_inicio':
            params['data_inicio'] = data_inicio
        if data_fim and exclude_param != 'data_fim':
            params['data_fim'] = data_fim
        if status_filtro and exclude_param != 'status':
            params['status'] = status_filtro
        if centro_custo_filtro and exclude_param != 'centro_custo':
            params['centro_custo'] = centro_custo_filtro
        if tipo_despesa_filtro and exclude_param != 'tipo_despesa':
            params['tipo_despesa'] = tipo_despesa_filtro
        if id_filtro and exclude_param != 'id':
            params['id'] = id_filtro
        
        from django.http import QueryDict
        query_string = QueryDict('', mutable=True)
        query_string.update(params)
        return '?' + query_string.urlencode() if params else ''
    
    # Labels para os filtros - usar os nomes do STATUS_CHOICES
    status_labels = dict(SolicitacaoReembolso.STATUS_CHOICES)
    centro_labels = dict(CENTROS_CUSTO)
    tipo_labels = dict(TIPOS_DESPESA)
    
    # Construir lista de badges de filtros ativos
    active_filter_badges = []
    if data_inicio:
        active_filter_badges.append({
            'label': f'Data Início: {data_inicio}',
            'remove_url': get_url_without_filter('data_inicio')
        })
    if data_fim:
        active_filter_badges.append({
            'label': f'Data Fim: {data_fim}',
            'remove_url': get_url_without_filter('data_fim')
        })
    if status_filtro:
        active_filter_badges.append({
            'label': f'Status: {status_labels.get(status_filtro, status_filtro)}',
            'remove_url': get_url_without_filter('status')
        })
    if centro_custo_filtro:
        active_filter_badges.append({
            'label': f'Centro de Custo: {centro_labels.get(centro_custo_filtro, centro_custo_filtro)}',
            'remove_url': get_url_without_filter('centro_custo')
        })
    if tipo_despesa_filtro:
        active_filter_badges.append({
            'label': f'Tipo de Despesa: {tipo_labels.get(tipo_despesa_filtro, tipo_despesa_filtro)}',
            'remove_url': get_url_without_filter('tipo_despesa')
        })
    if id_filtro:
        active_filter_badges.append({
            'label': f'ID: #{id_filtro}',
            'remove_url': get_url_without_filter('id')
        })
    
    # Opções para status baseado na tabela atual
    # Em Processo: apenas status que aparecem nessa tabela
    # Concluídos: apenas CONCLUIDO
    # Rejeitados: apenas REJEITADO
    if tabela_atual == 'concluidos':
        status_choices = [
            (SolicitacaoReembolso.STATUS_CONCLUIDO, 'Concluído'),
        ]
    elif tabela_atual == 'rejeitados':
        status_choices = [
            (SolicitacaoReembolso.STATUS_REJEITADO, 'Rejeitado'),
        ]
    else:  # em-processo
        status_choices = [
            (SolicitacaoReembolso.STATUS_AGUARDANDO_PAGAMENTO, 'Aprovado - Aguardando pagamento'),
            (SolicitacaoReembolso.STATUS_PAGAMENTO_AGENDADO, 'Solicitação aprovada - Pagamento agendado'),
            (SolicitacaoReembolso.STATUS_PAGO_AGUARDANDO_ASSINATURAS, 'Pago - Aguardando assinaturas'),
        ]
    
    return render(
        request,
        "intra/ultimos_reembolsos_gestor.html",
        {
            "em_processo": em_processo_com_data,
            "rejeitados": rejeitados_com_data,
            "concluidos": concluidos_com_data,
            "page_obj_em_processo": page_obj_em_processo,
            "page_obj_rejeitados": page_obj_rejeitados,
            "page_obj_concluidos": page_obj_concluidos,
            "tabela_atual": tabela_atual,
            "filtros": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "status": status_filtro,
                "centro_custo": centro_custo_filtro,
                "tipo_despesa": tipo_despesa_filtro,
                "id": id_filtro,
            },
            "status_choices": status_choices,
            "centros_custo": CENTROS_CUSTO,
            "tipos_despesa": TIPOS_DESPESA,
            "active_filter_badges": active_filter_badges,
        },
    )


@login_required
def concluir_reembolso(request, pk):
    """Conclui uma solicitação de reembolso aprovada."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    
    # Verificar se está aprovado e não concluído
    if sol.status_gestor_admin != SolicitacaoReembolso.STATUS_APROVADO:
        messages.error(request, "Apenas solicitações aprovadas podem ser concluídas.")
        return redirect("intra:ultimos_reembolsos")
    
    if sol.concluido:
        messages.warning(request, "Esta solicitação já foi concluída.")
        return redirect("intra:ultimos_reembolsos")
    
    if request.method == "POST":
        sol.concluido = True
        sol.concluido_em = timezone.now()
        sol.save()
        messages.success(request, f"Solicitação #{sol.pk} concluída com sucesso!")
        return redirect("intra:ultimos_reembolsos")
    
    return redirect("intra:ultimos_reembolsos")
