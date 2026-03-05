from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib.auth.views import PasswordResetConfirmView
from django.core.mail import send_mail
from django.conf import settings
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.utils.timezone import localtime
from django.utils.crypto import get_random_string
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth
from django.core.paginator import Paginator
from datetime import datetime, timedelta
import json

from .forms import LoginForm, EsqueceuAcessoForm, CompletarCadastroForm
from .models import PerfilSolicitante, RegraUsuario, SolicitacaoReembolso, ItemReembolso, CentroCusto
from .pdf_reembolso import gerar_pdf

User = get_user_model()


def _is_gestor(user):
    """Usuário com regra Gestor Administrativo (Regras de usuários no Admin)."""
    return (
        user.is_authenticated
        and user.regras_usuario.filter(role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO).exists()
    )

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

        if user and _is_gestor(user):
            messages.info(
                request,
                "Para usuários Gestor Administrativo, a senha é definida exclusivamente no painel administrativo. Entre em contato com o administrador.",
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
def meu_perfil(request):
    """Visualização somente leitura das informações do perfil do usuário."""
    perfil, _ = PerfilSolicitante.objects.get_or_create(user=request.user)
    return render(request, "intra/meu_perfil.html", {"perfil": perfil})


@login_required
def meus_reembolsos(request):
    """Lista de solicitações de reembolso do usuário."""
    solicitacoes = SolicitacaoReembolso.objects.filter(user=request.user).prefetch_related('itens').order_by('-criado_em')
    # Converter datas para timezone de Brasília e coletar códigos de despesa dos itens
    solicitacoes_com_data = []
    for sol in solicitacoes:
        sol.criado_em_brasilia = localtime(sol.criado_em)
        # Coletar códigos de despesa únicos dos itens
        codigos_despesa = []
        for item in sol.itens.all():
            if item.cod_despesa and item.cod_despesa not in codigos_despesa:
                codigos_despesa.append(item.cod_despesa)
        sol.codigos_despesa = ', '.join(codigos_despesa) if codigos_despesa else '—'
        solicitacoes_com_data.append(sol)
    return render(
        request,
        "intra/meus_reembolsos.html",
        {"solicitacoes": solicitacoes_com_data},
    )


@login_required
def reembolso_pdf(request, pk):
    """Gera PDF da solicitação de reembolso (solicitante ou gestor)."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if sol.user_id != request.user.id and not _is_gestor(request.user):
        return HttpResponse("Não autorizado.", status=403)
    pdf_bytes = gerar_pdf(sol)
    nome_arquivo = f"reembolso_{sol.pk}_{sol.criado_em.strftime('%Y%m%d')}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{nome_arquivo}"'
    return response


@login_required
def reembolso_detalhe_gestor_json(request, pk):
    """Retorna dados completos da solicitação de reembolso em JSON (para gestor visualizar)."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if not _is_gestor(request.user):
        return JsonResponse({"error": "Acesso restrito a Gestores Administrativos."}, status=403)
    tipos_labels = dict(TIPOS_DESPESA)
    criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
    
    # Coletar códigos de despesa dos itens
    codigos_despesa = []
    for item in sol.itens.all():
        if item.cod_despesa:
            codigos_despesa.append(item.cod_despesa)
    
    itens = []
    for item in sol.itens.all():
        itens.append({
            "tipo_despesa": tipos_labels.get(item.tipo_despesa, item.tipo_despesa),
            "descricao": item.descricao or "",
            "valor": float(item.valor),
            "cod_despesa": item.cod_despesa or "",
            "data_despesa": item.data_despesa.strftime("%d/%m/%Y") if item.data_despesa else "",
            "anexo_url": None,  # Será preenchido quando houver campo de anexo
            "anexo_nome": None,
        })
    
    # Coletar anexos dos itens
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
            "nome_gestor": perfil.nome_gestor or "",
            "email_gestor": perfil.email_gestor or "",
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
            "nome_gestor": "",
            "email_gestor": "",
        }
    
    # Informações de aprovação/rejeição
    aprovacao_info = {}
    if sol.status != SolicitacaoReembolso.STATUS_PENDENTE:
        aprovado_por_nome = ""
        aprovado_por_email = ""
        if sol.aprovado_por:
            aprovado_por_email = sol.aprovado_por.email or ""
            try:
                perfil_aprovador = PerfilSolicitante.objects.get(user=sol.aprovado_por)
                aprovado_por_nome = perfil_aprovador.nome_solicitante or ""
            except PerfilSolicitante.DoesNotExist:
                pass
        
        aprovado_em_brasilia = localtime(sol.aprovado_em) if sol.aprovado_em else None
        
        aprovacao_info = {
            "aprovado_por_nome": aprovado_por_nome,
            "aprovado_por_email": aprovado_por_email,
            "aprovado_em": aprovado_em_brasilia.strftime("%d/%m/%Y %H:%M") if aprovado_em_brasilia else "",
            "motivo_rejeicao": sol.motivo_rejeicao if sol.status == SolicitacaoReembolso.STATUS_REJEITADO else "",
        }
    
    return JsonResponse({
        "pk": sol.pk,
        "centro_custo": sol.centro_custo or "",
        "cod_despesa": list(set(codigos_despesa)) if codigos_despesa else [],  # Lista única de códigos
        "valor_total": float(sol.valor_total),
        "criado_em": criado_em_brasilia.strftime("%d/%m/%Y %H:%M") if criado_em_brasilia else "",
        "status": sol.status,
        "itens": itens,
        "anexos": anexos,
        "solicitante": dados_solicitante,
        "aprovacao": aprovacao_info,
    })


@login_required
def reembolso_anexos_json(request, pk):
    """Retorna anexos da solicitação de reembolso em JSON (para gestor visualizar)."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if not _is_gestor(request.user):
        return JsonResponse({"error": "Acesso restrito a Gestores Administrativos."}, status=403)
    tipos_labels = dict(TIPOS_DESPESA)
    anexos = []
    for item in sol.itens.all():
        anexos.append({
            "tipo_despesa": tipos_labels.get(item.tipo_despesa, item.tipo_despesa),
            "descricao": item.descricao or "",
            "url": None,  # Será preenchido quando houver campo de anexo
            "nome": None,
        })
    return JsonResponse({"anexos": anexos})


@login_required
def reembolso_detalhe_json(request, pk):
    """Retorna dados da solicitação de reembolso em JSON (para popup de visualização)."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if sol.user_id != request.user.id:
        return JsonResponse({"error": "Não autorizado."}, status=403)
    tipos_labels = dict(TIPOS_DESPESA)
    itens = [
        {
            "tipo_despesa": tipos_labels.get(item.tipo_despesa, item.tipo_despesa),
            "descricao": item.descricao or "",
            "valor": float(item.valor),
            "km": float(item.km) if item.km is not None else None,
        }
        for item in sol.itens.all()
    ]
    # Coletar códigos de despesa únicos dos itens
    codigos_despesa = []
    for item in sol.itens.all():
        if item.cod_despesa and item.cod_despesa not in codigos_despesa:
            codigos_despesa.append(item.cod_despesa)
    criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
    return JsonResponse({
        "pk": sol.pk,
        "centro_custo": sol.centro_custo,
        "cod_despesa": ', '.join(codigos_despesa) if codigos_despesa else '—',
        "valor_total": float(sol.valor_total),
        "criado_em": criado_em_brasilia.strftime("%d/%m/%Y %H:%M") if criado_em_brasilia else "",
        "status": sol.status,
        "motivo_rejeicao": sol.motivo_rejeicao or "",
        "itens": itens,
    })


@login_required
def aprovar_reembolsos(request):
    """Lista solicitações de reembolso para o gestor aprovar ou rejeitar."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    
    # Capturar parâmetro de busca
    busca = request.GET.get('busca', '').strip()
    
    # Query base
    solicitacoes = SolicitacaoReembolso.objects.select_related("user").all()
    
    # Aplicar filtro de busca se fornecido
    if busca:
        solicitacoes = solicitacoes.filter(
            Q(user__email__icontains=busca) |
            Q(centro_custo__icontains=busca) |
            Q(cod_despesa__icontains=busca) |
            Q(user__first_name__icontains=busca) |
            Q(user__last_name__icontains=busca)
        )
    
    # Ordenar por data de criação (mais recentes primeiro)
    solicitacoes = solicitacoes.order_by('-criado_em')
    
    # Paginação - 5 por página
    paginator = Paginator(solicitacoes, 5)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Converter datas para timezone de Brasília
    solicitacoes_com_data = []
    for sol in page_obj:
        sol.criado_em_brasilia = localtime(sol.criado_em) if sol.criado_em else None
        solicitacoes_com_data.append(sol)
    
    return render(
        request,
        "intra/aprovar_reembolsos.html",
        {
            "solicitacoes": solicitacoes_com_data,
            "page_obj": page_obj,
            "busca": busca,
        },
    )


@login_required
def reembolso_decidir(request, pk):
    """Aprova ou rejeita uma solicitação (somente gestor)."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if sol.status != SolicitacaoReembolso.STATUS_PENDENTE:
        messages.warning(request, "Esta solicitação já foi processada.")
        return redirect("intra:aprovar_reembolsos")
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "aprovar":
            sol.status = SolicitacaoReembolso.STATUS_APROVADO
            sol.aprovado_por = request.user
            sol.aprovado_em = timezone.now()
            sol.motivo_rejeicao = ""
            sol.save()
            messages.success(request, "Solicitação aprovada.")
        elif acao == "rejeitar":
            motivo = (request.POST.get("motivo_rejeicao") or "").strip()
            if not motivo:
                messages.error(request, "É obrigatório informar o motivo da rejeição.")
                return redirect("intra:aprovar_reembolsos")
            sol.status = SolicitacaoReembolso.STATUS_REJEITADO
            sol.aprovado_por = request.user
            sol.aprovado_em = timezone.now()
            sol.motivo_rejeicao = motivo[:500]
            sol.save()
            messages.success(request, "Solicitação rejeitada.")
        return redirect("intra:aprovar_reembolsos")
    return redirect("intra:aprovar_reembolsos")


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
    
    # Construir filtros base
    filtros_solicitacao = Q()
    
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
    
    # Filtro de status
    if status_filtro and status_filtro != 'TODOS':
        filtros_solicitacao &= Q(status=status_filtro)
    
    # Filtro de centro de custo
    if centro_custo_filtro:
        filtros_solicitacao &= Q(centro_custo=centro_custo_filtro)
    
    # Estatísticas gerais com filtros
    queryset_base = SolicitacaoReembolso.objects.filter(filtros_solicitacao)
    
    # Se houver filtro de tipo de despesa, calcular totais baseados nos itens
    if tipo_despesa_filtro:
        # Construir filtros para itens considerando tipo de despesa
        filtros_itens_aprovado = Q(solicitacao__status=SolicitacaoReembolso.STATUS_APROVADO, tipo_despesa=tipo_despesa_filtro)
        filtros_itens_rejeitado = Q(solicitacao__status=SolicitacaoReembolso.STATUS_REJEITADO, tipo_despesa=tipo_despesa_filtro)
        filtros_itens_pendente = Q(solicitacao__status=SolicitacaoReembolso.STATUS_PENDENTE, tipo_despesa=tipo_despesa_filtro)
        
        # Aplicar filtros de data
        if data_inicio:
            try:
                data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
                filtros_itens_aprovado &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
                filtros_itens_rejeitado &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
                filtros_itens_pendente &= Q(solicitacao__criado_em__date__gte=data_inicio_obj)
            except ValueError:
                pass
        
        if data_fim:
            try:
                data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
                filtros_itens_aprovado &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
                filtros_itens_rejeitado &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
                filtros_itens_pendente &= Q(solicitacao__criado_em__date__lte=data_fim_obj)
            except ValueError:
                pass
        
        # Aplicar filtro de centro de custo
        if centro_custo_filtro:
            filtros_itens_aprovado &= Q(solicitacao__centro_custo=centro_custo_filtro)
            filtros_itens_rejeitado &= Q(solicitacao__centro_custo=centro_custo_filtro)
            filtros_itens_pendente &= Q(solicitacao__centro_custo=centro_custo_filtro)
        
        # Calcular totais baseados nos itens
        total_aprovado = ItemReembolso.objects.filter(filtros_itens_aprovado).aggregate(
            total=Sum('valor')
        )['total'] or 0
        
        total_rejeitado = ItemReembolso.objects.filter(filtros_itens_rejeitado).aggregate(
            total=Sum('valor')
        )['total'] or 0
        
        total_pendente = ItemReembolso.objects.filter(filtros_itens_pendente).aggregate(
            total=Sum('valor')
        )['total'] or 0
        
        # Contagem de solicitações distintas por status (não itens)
        count_aprovado = ItemReembolso.objects.filter(filtros_itens_aprovado).values('solicitacao').distinct().count()
        count_rejeitado = ItemReembolso.objects.filter(filtros_itens_rejeitado).values('solicitacao').distinct().count()
        count_pendente = ItemReembolso.objects.filter(filtros_itens_pendente).values('solicitacao').distinct().count()
    else:
        # Comportamento padrão: calcular baseado nas solicitações
        total_aprovado = queryset_base.filter(status=SolicitacaoReembolso.STATUS_APROVADO).aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        
        total_rejeitado = queryset_base.filter(status=SolicitacaoReembolso.STATUS_REJEITADO).aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        
        total_pendente = queryset_base.filter(status=SolicitacaoReembolso.STATUS_PENDENTE).aggregate(
            total=Sum('valor_total')
        )['total'] or 0
        
        # Contagem por status
        count_aprovado = queryset_base.filter(status=SolicitacaoReembolso.STATUS_APROVADO).count()
        count_rejeitado = queryset_base.filter(status=SolicitacaoReembolso.STATUS_REJEITADO).count()
        count_pendente = queryset_base.filter(status=SolicitacaoReembolso.STATUS_PENDENTE).count()
    
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
    
    # Filtro de status - se não especificado, mostrar apenas aprovados (comportamento original)
    if status_filtro and status_filtro != 'TODOS':
        filtros_itens_tipo &= Q(solicitacao__status=status_filtro)
    else:
        # Comportamento padrão: mostrar apenas aprovados para gastos por tipo
        filtros_itens_tipo &= Q(solicitacao__status=SolicitacaoReembolso.STATUS_APROVADO)
    
    # Filtro de centro de custo via relacionamento
    if centro_custo_filtro:
        filtros_itens_tipo &= Q(solicitacao__centro_custo=centro_custo_filtro)
    
    # Filtro de tipo de despesa (campo direto de ItemReembolso)
    if tipo_despesa_filtro:
        filtros_itens_tipo &= Q(tipo_despesa=tipo_despesa_filtro)
    
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
            filtros_itens_centro &= Q(solicitacao__status=SolicitacaoReembolso.STATUS_APROVADO)
        
        # Filtro de centro de custo via relacionamento
        if centro_custo_filtro:
            filtros_itens_centro &= Q(solicitacao__centro_custo=centro_custo_filtro)
        
        # Filtro de tipo de despesa (obrigatório neste caso)
        filtros_itens_centro &= Q(tipo_despesa=tipo_despesa_filtro)
        
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
        # Comportamento padrão: calcular baseado nas solicitações
        gastos_por_centro = queryset_base.filter(
            status=SolicitacaoReembolso.STATUS_APROVADO
        ).values('centro_custo').annotate(
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
    
    reembolsos_por_mes = queryset_base.filter(
        criado_em__gte=doze_meses_atras
    ).annotate(
        mes=TruncMonth('criado_em')
    ).values('mes').annotate(
        total=Count('id'),
        valor_aprovado=Sum('valor_total', filter=Q(status=SolicitacaoReembolso.STATUS_APROVADO)),
        valor_rejeitado=Sum('valor_total', filter=Q(status=SolicitacaoReembolso.STATUS_REJEITADO)),
        valor_pendente=Sum('valor_total', filter=Q(status=SolicitacaoReembolso.STATUS_PENDENTE))
    ).order_by('mes')
    
    reembolsos_por_mes_formatado = []
    for item in reembolsos_por_mes:
        mes_brasil = localtime(item['mes']).strftime('%m/%Y') if item['mes'] else ''
        reembolsos_por_mes_formatado.append({
            'mes': mes_brasil,
            'total': item['total'],
            'valor_aprovado': float(item['valor_aprovado'] or 0),
            'valor_rejeitado': float(item['valor_rejeitado'] or 0),
            'valor_pendente': float(item['valor_pendente'] or 0)
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
        
        from django.http import QueryDict
        query_string = QueryDict('', mutable=True)
        query_string.update(params)
        return '?' + query_string.urlencode() if params else ''
    
    # Labels para os filtros
    status_labels = dict([
        ('', 'Todos'),
        (SolicitacaoReembolso.STATUS_APROVADO, 'Aprovado'),
        (SolicitacaoReembolso.STATUS_REJEITADO, 'Rejeitado'),
        (SolicitacaoReembolso.STATUS_PENDENTE, 'Pendente'),
    ])
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
    
    context = {
        'total_aprovado': float(total_aprovado),
        'total_rejeitado': float(total_rejeitado),
        'total_pendente': float(total_pendente),
        'count_aprovado': count_aprovado,
        'count_rejeitado': count_rejeitado,
        'count_pendente': count_pendente,
        'gastos_por_tipo': gastos_por_tipo_formatado,
        'gastos_por_centro': gastos_por_centro_formatado,
        'reembolsos_por_mes': page_obj_mes,
        'page_obj_mes': page_obj_mes,
        # Filtros atuais
        'filtros': {
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'status': status_filtro,
            'centro_custo': centro_custo_filtro,
            'tipo_despesa': tipo_despesa_filtro,
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
            (SolicitacaoReembolso.STATUS_APROVADO, 'Aprovado'),
            (SolicitacaoReembolso.STATUS_REJEITADO, 'Rejeitado'),
            (SolicitacaoReembolso.STATUS_PENDENTE, 'Pendente'),
        ],
    }
    
    return render(request, "intra/dashboard_gestor.html", context)


@login_required
def reembolso(request):
    """Página de solicitação de reembolso com formulário."""
    # Buscar valores únicos de PROGRAMA para Centro de Custo
    programas = CentroCusto.objects.exclude(PROGRAMA__isnull=True).exclude(PROGRAMA__exact='').values_list("PROGRAMA", flat=True).distinct().order_by("PROGRAMA")
    
    # Buscar todos os registros de CentroCusto para Código de Despesa
    codigos = CentroCusto.objects.exclude(CODIGO__isnull=True).exclude(CODIGO__exact='').exclude(DESCRICAO__isnull=True).exclude(DESCRICAO__exact='').order_by("CODIGO")
    
    # Preparar dados para JSON (para JavaScript dinâmico)
    codigos_json = [{"codigo": c.CODIGO, "descricao": c.DESCRICAO} for c in codigos]
    
    context = {
        "programas": programas,
        "codigos": codigos,
        "tipos_despesa": TIPOS_DESPESA,
        "tipos_despesa_json": json.dumps(TIPOS_DESPESA),
        "codigos_despesa_json": json.dumps(codigos_json),
    }
    if request.method == "POST":
        centro_custo = request.POST.get("centro_custo", "").strip()
        valor_total = 0
        itens_dados = []
        for key, value in request.POST.items():
            if key.startswith("valor_") and value:
                try:
                    idx = key.replace("valor_", "")
                    v = float(str(value).replace(",", "."))
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
            sol = SolicitacaoReembolso.objects.create(
                user=request.user,
                centro_custo=centro_custo,
                cod_despesa="",  # Não é mais obrigatório no nível da solicitação
                valor_total=valor_total,
            )
            for item in itens_dados:
                if item["tipo_despesa"]:
                    # Buscar anexo correspondente usando o índice original do formulário
                    anexo = None
                    anexo_key = f"anexo_{item['idx']}"
                    if anexo_key in request.FILES:
                        anexo = request.FILES[anexo_key]
                    ItemReembolso.objects.create(
                        solicitacao=sol,
                        tipo_despesa=item["tipo_despesa"],
                        cod_despesa=item["cod_despesa"],
                        data_despesa=item["data_despesa"],
                        descricao=item["descricao"],
                        valor=item["valor"],
                        anexo=anexo,
                    )
        messages.success(request, "Solicitação de reembolso enviada com sucesso!")
        return redirect("intra:reembolso")
    return render(request, "intra/reembolso.html", context)
