"""
Gera PDF de reembolso conforme modelo MODELO_REEMBOLSO.pdf
"""
import os
from io import BytesIO
from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from intra.docusign_integration import DOCUSIGN_ANCHOR_GESTOR, DOCUSIGN_ANCHOR_SOLICITANTE

try:
    from PyPDF2 import PdfReader, PdfWriter
    PYPDF2_AVAILABLE = True
except ImportError:
    try:
        from pypdf import PdfReader, PdfWriter
        PYPDF2_AVAILABLE = True
    except ImportError:
        PYPDF2_AVAILABLE = False

# Mapeamento tipo_despesa (valor no form) -> rótulo no PDF (como no modelo)
ROTULOS_TIPO = {
    "REFEICAO": "REFEIÇÃO",
    "LOCOMACAO": "LOCOMOÇÃO",
    "PASSAGENS": "PASSAGENS",
    "PEDAGIO": "PEDÁGIO",
    "DESLOCAMENTO": "DESLOCAMENTO KM",
    "OUTROS_MATERIAIS": "OUTROS",
}

# Lista de classificações para orientações
CLASSIFICACOES = [
    "HOSPEDAGEM",
    "REFEIÇÃO",
    "LOCOMOÇÃO",
    "PASSAGENS",
    "TELEFONIA FIXA",
    "TELEFONIA MÓVEL",
    "MATERIAL ESCRITÓRIO",
    "TREINAMENTO",
    "ESTACIONAMENTO",
    "DESLOCAMENTO KM",
    "PEDÁGIO",
    "OUTROS",
]


def _formatar_valor(valor):
    if valor is None or valor == "":
        return "-"
    try:
        v = float(valor)
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "-"


def _linhas_quebra_pdf(canvas_obj, texto, largura_max, fonte="Helvetica", tamanho=7):
    """Retorna lista de linhas (sem desenhar) - para pre-calcular espaco."""
    if not texto or texto == "-":
        return []
    palavras = texto.split()
    linhas = []
    linha_atual = ""
    for palavra in palavras:
        teste_linha = linha_atual + (" " if linha_atual else "") + palavra
        if canvas_obj.stringWidth(teste_linha, fonte, tamanho) <= largura_max:
            linha_atual = teste_linha
        else:
            if linha_atual:
                linhas.append(linha_atual)
            if canvas_obj.stringWidth(palavra, fonte, tamanho) > largura_max:
                linha_atual = ""
                for char in palavra:
                    if canvas_obj.stringWidth(linha_atual + char, fonte, tamanho) <= largura_max:
                        linha_atual += char
                    else:
                        if linha_atual:
                            linhas.append(linha_atual)
                        linha_atual = char
            else:
                linha_atual = palavra
    if linha_atual:
        linhas.append(linha_atual)
    return linhas


def _quebrar_texto(canvas_obj, texto, x, y, largura_max, fonte="Helvetica", tamanho=7):
    """
    Quebra um texto longo em múltiplas linhas dentro de uma largura máxima.
    Retorna a posição Y final após escrever todas as linhas.
    """
    if not texto or texto == "-":
        return y
    
    # Dividir o texto em palavras
    palavras = texto.split()
    linhas = []
    linha_atual = ""
    
    for palavra in palavras:
        # Testar se a palavra cabe na linha atual
        teste_linha = linha_atual + (" " if linha_atual else "") + palavra
        largura_teste = canvas_obj.stringWidth(teste_linha, fonte, tamanho)
        
        if largura_teste <= largura_max:
            linha_atual = teste_linha
        else:
            # Se a linha atual não está vazia, adicionar às linhas e começar nova
            if linha_atual:
                linhas.append(linha_atual)
            # Se a palavra sozinha é maior que a largura, quebrar ela
            if canvas_obj.stringWidth(palavra, fonte, tamanho) > largura_max:
                # Quebrar palavra em caracteres
                for char in palavra:
                    teste_char = linha_atual + char
                    if canvas_obj.stringWidth(teste_char, fonte, tamanho) <= largura_max:
                        linha_atual = teste_char
                    else:
                        if linha_atual:
                            linhas.append(linha_atual)
                        linha_atual = char
            else:
                linha_atual = palavra
    
    # Adicionar última linha
    if linha_atual:
        linhas.append(linha_atual)
    
    # Desenhar todas as linhas
    y_atual = y
    for linha in linhas:
        canvas_obj.setFont(fonte, tamanho)
        canvas_obj.drawString(x, y_atual, linha)
        y_atual -= 3.5 * mm  # Espaçamento entre linhas
    
    return y_atual


def gerar_pdf(solicitacao):
    """
    Gera o PDF da solicitação de reembolso conforme o modelo.
    Ordem: Folha de rosto (primeira) -> Anexos (se houver)
    solicitacao: instância de SolicitacaoReembolso com itens e user com perfil_solicitante.
    """
    # --- Coletar e separar anexos ---
    anexos_pdf = []
    anexos_imagem = []
    
    for item in solicitacao.itens.all():
        if item.anexo:
            anexo_nome = item.anexo.name.split('/')[-1] if item.anexo.name else "Anexo"
            extensao = anexo_nome.lower().split('.')[-1] if '.' in anexo_nome else ''
            
            if extensao == 'pdf':
                anexos_pdf.append({
                    "arquivo": item.anexo,
                    "nome": anexo_nome,
                })
            elif extensao in ['jpg', 'jpeg', 'png', 'gif', 'bmp']:
                anexos_imagem.append({
                    "arquivo": item.anexo,
                    "nome": anexo_nome,
                })
    
    # --- Merge de PDFs anexados (se houver) ---
    pdfs_merged = BytesIO()
    if anexos_pdf and PYPDF2_AVAILABLE:
        writer = PdfWriter()
        for anexo in anexos_pdf:
            try:
                arquivo = anexo['arquivo'].open('rb')
                reader = PdfReader(arquivo)
                for page in reader.pages:
                    writer.add_page(page)
                arquivo.close()
            except Exception as e:
                # Se houver erro ao ler o PDF, continua com os outros
                pass
        if len(writer.pages) > 0:
            writer.write(pdfs_merged)
            pdfs_merged.seek(0)
        else:
            pdfs_merged = None
    else:
        pdfs_merged = None
    
    # --- Gerar folha de rosto ---
    folha_rosto = _gerar_folha_rosto(solicitacao)
    
    # --- Merge final: Folha de rosto (primeira) -> Anexos (se houver) ---
    if PYPDF2_AVAILABLE:
        writer_final = PdfWriter()
        
        # 1. Adicionar folha de rosto primeiro
        reader_rosto = PdfReader(BytesIO(folha_rosto))
        for page in reader_rosto.pages:
            writer_final.add_page(page)
        
        # 2. Adicionar imagens (criar PDFs para cada imagem)
        if anexos_imagem:
            for anexo in anexos_imagem:
                try:
                    img_pdf = _gerar_pdf_imagem(anexo)
                    if img_pdf:
                        reader_img = PdfReader(BytesIO(img_pdf))
                        for page in reader_img.pages:
                            writer_final.add_page(page)
                except Exception as e:
                    # Se houver erro, continua
                    pass
        
        # 3. Adicionar PDFs anexados
        if pdfs_merged:
            reader_merged = PdfReader(pdfs_merged)
            for page in reader_merged.pages:
                writer_final.add_page(page)
        
        # Gerar PDF final
        buffer_final = BytesIO()
        writer_final.write(buffer_final)
        buffer_final.seek(0)
        return buffer_final.getvalue()
    else:
        # Se PyPDF2 não estiver disponível, retornar apenas a folha de rosto
        # (e tentar adicionar imagens manualmente)
        return folha_rosto


def _gerar_folha_rosto(solicitacao):
    """
    Gera apenas a folha de rosto do reembolso.
    Retorna bytes do PDF da folha de rosto.
    """
    buffer = BytesIO()
    w, h = A4
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = w, h
    margin_left = 20 * mm
    margin_right = width - 20 * mm

    def _resolver_nome_gestor_para_pdf(solic):
        """
        Resolve um nome amigável do gestor para exibir no PDF a partir do campo
        solicitacao.nome_gestor (que armazena o e-mail informado no formulário).
        - Se houver um usuário com esse e-mail, usa o nome do perfil (PerfilSolicitante.nome_solicitante)
          ou o nome completo do usuário; como fallback, o e-mail do usuário.
        - Se não houver usuário, deriva um nome do próprio e-mail (parte antes do @),
          com formatação Title Case; como fallback final, retorna o texto original.
        """
        try:
            from django.contrib.auth import get_user_model
            from intra.models import PerfilSolicitante
            User = get_user_model()
            aprovador = (solic.nome_gestor or "").strip()
            if not aprovador:
                return "-"
            try:
                gestor_user = User.objects.get(email__iexact=aprovador)
                try:
                    perfil = PerfilSolicitante.objects.get(user=gestor_user)
                    nome = (perfil.nome_solicitante or "").strip()
                    if nome:
                        return nome
                except PerfilSolicitante.DoesNotExist:
                    pass
                nome = (gestor_user.get_full_name() or "").strip()
                if nome:
                    return nome
                return gestor_user.email or "-"
            except User.DoesNotExist:
                if "@" in aprovador:
                    candidato = aprovador.split("@")[0].replace(".", " ").replace("_", " ").strip()
                    return candidato.title() or aprovador
                return aprovador
        except Exception:
            return (solic.nome_gestor or "-")
    
    # --- Logo no canto superior direito (mais para cima) ---
    logo_paths = [
        os.path.join(settings.BASE_DIR, 'intra', 'static', 'admin', 'img', 'logos', 'PE_Logo_Original_vertical.png'),
        os.path.join(settings.BASE_DIR, 'static', 'admin', 'img', 'logos', 'PE_Logo_Original_vertical.png'),
        os.path.join('intra', 'static', 'admin', 'img', 'logos', 'PE_Logo_Original_vertical.png'),
    ]
    logo_path = None
    for path in logo_paths:
        if os.path.exists(path):
            logo_path = path
            break
    
    logo_width = 0
    logo_height = 0
    if logo_path:
        try:
            logo = ImageReader(logo_path)
            logo_width = 40 * mm
            logo_height = 50 * mm
            logo_x = margin_right - logo_width + 10 * mm  # Mais para direita
            logo_y = height - logo_height + 5 * mm  # Mantém a posição vertical
            c.drawImage(logo, logo_x, logo_y, width=logo_width, height=logo_height, preserveAspectRatio=True)
        except Exception:
            pass
    
    # --- Cabeçalho (Associação) no canto esquerdo ---
    y_header = height - 15 * mm  # Mais para cima também
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin_left, y_header, "A ASSOCIAÇÃO PARCEIROS DA EDUCAÇÃO")
    y_header -= 5 * mm
    c.setFont("Helvetica", 9)
    c.drawString(margin_left, y_header, "CNPJ: 06.878.967/0001-57")
    y_header -= 4 * mm
    c.drawString(margin_left, y_header, "Av. Paulista, 967 - 3º Andar")
    y_header -= 4 * mm
    c.drawString(margin_left, y_header, "Bela Vista, São Paulo - SP")
    y_header -= 4 * mm
    c.drawString(margin_left, y_header, "CEP: 01311-100")
    
    # --- Título centralizado abaixo do cabeçalho ---
    y_title = y_header - 8 * mm
    c.setFont("Helvetica-Bold", 12)
    numero = f"{solicitacao.pk:02d}/{solicitacao.criado_em.year}" if solicitacao.criado_em else f"{solicitacao.pk:02d}/2025"
    title_text = f"NOTA DE DÉBITO / REEMBOLSO - N° {numero}"
    title_width = c.stringWidth(title_text, "Helvetica-Bold", 12)
    title_x = (width - title_width) / 2
    c.drawString(title_x, y_title, title_text)
    
    y = y_title - 15 * mm
    
    # --- Seção Reembolso de Despesas ---
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin_left, y, "Reembolso de Despesas")
    y -= 8 * mm
    
    # --- Solicito o reembolso do valor de ---
    c.setFont("Helvetica", 10)
    c.drawString(margin_left, y, "Solicito o reembolso do valor de:")
    valor_str = _formatar_valor(solicitacao.valor_total)
    c.setFont("Helvetica-Bold", 10)
    valor_x = margin_left + c.stringWidth("Solicito o reembolso do valor de: ", "Helvetica", 10)
    c.drawString(valor_x, y, f"R$ {valor_str}")
    y -= 8 * mm
    
    # --- Referente ao... ---
    c.setFont("Helvetica", 9)
    c.drawString(margin_left, y, "Referente ao reembolso de despesas relacionadas aos seguintes eventos:")
    y -= 8 * mm
    
    # --- Tabela: Nota de débito para Reembolso de Despesas ---
    y_table_start = y
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin_left, y, "Nota de débito para Reembolso de Despesas")
    y -= 6 * mm
    
    # Definir colunas da tabela com mais espaçamento entre campos (sem GESTÃO APE)
    col_programa = margin_left
    col_cod = margin_left + 45 * mm     # Mais espaço
    col_data = margin_left + 62 * mm    # Mais espaço
    col_classif = margin_left + 77 * mm # Mais espaço
    col_desc = margin_left + 97 * mm   # Mais espaço
    col_km = margin_left + 132 * mm     # Mais espaço
    col_valor = margin_left + 145 * mm # Mais espaço
    
    # Largura máxima da tabela para não sobrepor orientações
    table_right = margin_left + 95 * mm
    
    # Cabeçalho da tabela
    c.setFont("Helvetica-Bold", 7)  # Fonte menor para cabeçalhos
    c.drawString(col_programa, y, "Programa:")
    c.drawString(col_cod, y, "CÓD. DESP.")
    c.drawString(col_data, y, "DATA")
    c.drawString(col_classif, y, "CLASSIF.")
    c.drawString(col_desc, y, "DESCRIÇÃO")
    c.drawString(col_km, y, "km")
    c.drawString(col_valor, y, "VALOR")
    y -= 5 * mm
    
    # Linha separadora (até a coluna VALOR)
    c.line(margin_left, y, col_valor + 20 * mm, y)
    y -= 3 * mm
    
    # Dados da tabela
    itens = list(solicitacao.itens.all())
    data_str = solicitacao.criado_em.strftime("%d/%m/%Y") if solicitacao.criado_em else "-"
    
    # Buscar descrições dos códigos de despesa
    from intra.models import CentroCusto
    
    y_table_bottom = y
    
    if itens:
        c.setFont("Helvetica", 7)  # Fonte menor para dados
        y_min_tabela = 125 * mm
        idx = 0
        while idx < len(itens):
            item = itens[idx]
            # Pre-check: calcular linhas da descricao para saber se cabe
            largura_desc_pre = col_km - col_desc - 3 * mm
            desc_pre = (item.descricao or "-").strip()
            linhas_pre = _linhas_quebra_pdf(c, desc_pre, largura_desc_pre) if desc_pre != "-" else []
            n_linhas = max(1, len(linhas_pre))
            y_previsto = y - max(4 * mm, n_linhas * 3.5 * mm)
            # Se nao couber, criar nova pagina e refazer este item
            if y_previsto < y_min_tabela:
                c.showPage()
                y = height - 30 * mm
                c.setFont("Helvetica-Bold", 8)
                c.drawString(margin_left, y, "Reembolso - itens (continuacao)")
                y -= 6 * mm
                c.setFont("Helvetica-Bold", 7)
                c.drawString(col_programa, y, "Programa:")
                c.drawString(col_cod, y, "COD. DESP.")
                c.drawString(col_data, y, "DATA")
                c.drawString(col_classif, y, "CLASSIF.")
                c.drawString(col_desc, y, "DESCRICAO")
                c.drawString(col_km, y, "km")
                c.drawString(col_valor, y, "VALOR")
                y -= 5 * mm
                c.line(margin_left, y, col_valor + 20 * mm, y)
                y -= 3 * mm
                c.setFont("Helvetica", 7)
                y_min_tabela = 35 * mm
                continue
            # Programa (centro de custo) - mostrar em todos os itens
            programa = solicitacao.centro_custo or "-"
            # Calcular largura disponível até a próxima coluna
            largura_disponivel = col_cod - col_programa - 5 * mm
            # Tentar mostrar completo, se não couber usar "..." no final
            if c.stringWidth(programa, "Helvetica", 7) > largura_disponivel:
                # Reduzir até caber
                while len(programa) > 0 and c.stringWidth(programa + "...", "Helvetica", 7) > largura_disponivel:
                    programa = programa[:-1]
                programa = programa + "..."
            c.drawString(col_programa, y, programa)
            
            # Código de despesa (apenas o código, sem descrição na tabela)
            cod_desp = item.cod_despesa or "-"
            if len(cod_desp) > 10:
                cod_desp = cod_desp[:10]
            c.drawString(col_cod, y, cod_desp)
            
            # Data da despesa ou data da solicitação
            data_item = item.data_despesa.strftime("%d/%m/%Y") if item.data_despesa else data_str
            c.drawString(col_data, y, data_item)
            
            # Classificação (tipo de despesa) - mostrar completo, adaptar ao espaço disponível
            tipo_label = ROTULOS_TIPO.get(item.tipo_despesa, item.tipo_despesa.replace("_", " ").title())
            largura_classif = col_desc - col_classif - 3 * mm
            if c.stringWidth(tipo_label, "Helvetica", 7) > largura_classif:
                # Reduzir até caber
                tipo_original = tipo_label
                while len(tipo_label) > 0 and c.stringWidth(tipo_label + "...", "Helvetica", 7) > largura_classif:
                    tipo_label = tipo_label[:-1]
                tipo_label = tipo_label + "..."
            c.drawString(col_classif, y, tipo_label)
            
            # Descrição do item - quebrar em múltiplas linhas se necessário
            descricao_item = (item.descricao or "-").strip()
            largura_desc = col_km - col_desc - 3 * mm
            y_desc_final = _quebrar_texto(c, descricao_item, col_desc, y, largura_desc, "Helvetica", 7)
            
            # KM - alinhar com a primeira linha da descrição
            km_str = _formatar_valor(item.km) if item.km is not None else "-"
            c.drawString(col_km, y, km_str)
            
            # Valor - alinhar com a primeira linha da descrição
            val_str = _formatar_valor(item.valor)
            c.drawString(col_valor, y, val_str)
            
            # Se a descrição ocupou mais de uma linha, usar o Y final (mais baixo)
            # Caso contrário, usar o espaçamento padrão
            if y_desc_final < y - 4 * mm:
                y = y_desc_final
            else:
                y -= 4 * mm
            y_table_bottom = y
            idx += 1
    else:
        # Se não houver itens, mostrar apenas dados básicos
        c.setFont("Helvetica", 7)
        programa = solicitacao.centro_custo or "-"
        # Calcular largura disponível até a próxima coluna
        largura_disponivel = col_cod - col_programa - 5 * mm
        # Tentar mostrar completo, se não couber usar "..." no final
        if c.stringWidth(programa, "Helvetica", 7) > largura_disponivel:
            # Reduzir até caber
            while len(programa) > 0 and c.stringWidth(programa + "...", "Helvetica", 7) > largura_disponivel:
                programa = programa[:-1]
            programa = programa + "..."
        c.drawString(col_programa, y, programa)
        c.drawString(col_cod, y, "-")
        c.drawString(col_data, y, data_str)
        c.drawString(col_classif, y, "-")
        c.drawString(col_desc, y, "-")
        c.drawString(col_km, y, "-")
        c.drawString(col_valor, y, _formatar_valor(solicitacao.valor_total))
        y -= 4 * mm
        y_table_bottom = y
    
    # Preencher linhas restantes com "-" até o limite visual da tabela (como no modelo impresso)
    c.setFont("Helvetica", 7)
    while y > 120 * mm:
        c.drawString(col_programa, y, "-")
        c.drawString(col_cod, y, "-")
        c.drawString(col_data, y, "-")
        c.drawString(col_classif, y, "-")
        c.drawString(col_desc, y, "-")
        c.drawString(col_km, y, "-")
        c.drawString(col_valor, y, "-")
        y -= 4 * mm
        y_table_bottom = y
    
    # --- Depósito em conta (abaixo da tabela) ---
    y = y_table_bottom - 10 * mm
    c.setFont("Helvetica", 10)
    c.drawString(margin_left, y, "Solicito providenciar depósito em minha conta corrente, conforme dados abaixo:")
    y -= 8 * mm
    
    # Usar dados de pagamento da solicitação (não do cadastro)
    perfil = getattr(solicitacao.user, "perfil_solicitante", None)
    nome_solicitante = perfil.nome_solicitante if perfil else solicitacao.user.get_full_name() or solicitacao.user.email or '-'
    
    c.setFont("Helvetica", 9)
    c.drawString(margin_left, y, f"Nome: {nome_solicitante}")
    y -= 5 * mm
    
    # Verificar forma de pagamento da solicitação
    if solicitacao.forma_pagamento == 'PIX':
        # Dados PIX da solicitação
        pix_chave = solicitacao.pix_chave or solicitacao.user.email or '-'
        pix_banco = solicitacao.pix_banco or '-'
        pix_cpf = solicitacao.pix_cpf or 'XXX.XXX.XXX-XX'
        
        c.drawString(margin_left, y, f"PIX: {pix_chave}")
        y -= 5 * mm
        c.drawString(margin_left, y, f"CPF/CNPJ: {pix_cpf}")
        y -= 5 * mm
        c.drawString(margin_left, y, f"Banco: {pix_banco}")
        # Não exibir Agência e Conta para PIX
    elif solicitacao.forma_pagamento == 'TRANSFERENCIA':
        # Dados de transferência da solicitação
        transf_banco = solicitacao.transf_banco or '-'
        transf_agencia = solicitacao.transf_agencia or '-'
        transf_conta_numero = solicitacao.transf_conta_numero or '-'
        transf_cpf = solicitacao.transf_cpf or '-'
        
        # PIX pode ser do cadastro ou email do usuário como fallback
        pix_fallback = perfil.chave_pix if perfil and perfil.chave_pix else solicitacao.user.email or '-'
        cpf_fallback = transf_cpf if transf_cpf != '-' else 'XXX.XXX.XXX-XX'
        
        c.drawString(margin_left, y, f"PIX: {pix_fallback}")
        y -= 5 * mm
        c.drawString(margin_left, y, f"CPF/CNPJ: {cpf_fallback}")
        y -= 5 * mm
        # Mostrar o nome do banco (já está salvo, não precisa verificar se é "Outro")
        c.drawString(margin_left, y, f"Banco: {transf_banco}")
        
        # Se for Carteira Digital, mostrar os dados da carteira digital
        if transf_banco == 'Carteira Digital':
            y -= 5 * mm
            if transf_conta_numero and transf_conta_numero != '-':
                c.drawString(margin_left, y, f"Chave (E-mail, CPF ou Telefone): {transf_conta_numero}")
        else:
            # Para banco tradicional, mostrar agência e número da conta
            if transf_agencia and transf_agencia != '-':
                y -= 5 * mm
                c.drawString(margin_left, y, f"Agência: {transf_agencia}")
            if solicitacao.transf_conta_tipo:
                y -= 5 * mm
                tipo_conta = "Conta Corrente" if solicitacao.transf_conta_tipo == "CORRENTE" else "Poupança"
                c.drawString(margin_left, y, f"Tipo de Conta: {tipo_conta}")
            if transf_conta_numero and transf_conta_numero != '-':
                y -= 5 * mm
                c.drawString(margin_left, y, f"Conta: {transf_conta_numero}")
    else:
        # Fallback: usar dados do cadastro se não houver forma de pagamento definida
        if perfil:
            pix_chave = perfil.chave_pix or solicitacao.user.email or '-'
            c.drawString(margin_left, y, f"PIX: {pix_chave}")
            y -= 5 * mm
            c.drawString(margin_left, y, "CPF/CNPJ: XXX.XXX.XXX-XX")
            y -= 5 * mm
            c.drawString(margin_left, y, f"Banco: {perfil.banco or 'YYYY'}")
            y -= 5 * mm
            c.drawString(margin_left, y, f"Agência: {perfil.agencia or 'XXXX-X'}")
            y -= 5 * mm
            c.drawString(margin_left, y, f"Conta: {perfil.conta_numero or 'XXXXX-X'}")
        else:
            c.drawString(margin_left, y, "PIX: -")
            y -= 5 * mm
            c.drawString(margin_left, y, "CPF/CNPJ: XXX.XXX.XXX-XX")
            y -= 5 * mm
            c.drawString(margin_left, y, "Banco: YYYY")
            y -= 5 * mm
            c.drawString(margin_left, y, "Agência: XXXX-X")
            y -= 5 * mm
            c.drawString(margin_left, y, "Conta: XXXXX-X")
    
    y -= 10 * mm
    
    # --- Rodapé: Data e Assinaturas ---
    # Garantir que o rodapé apareça acima da margem inferior
    if y < 60 * mm:
        y = 60 * mm
    
    meses = ("janeiro", "fevereiro", "março", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
    if solicitacao.criado_em:
        data_ext = f"{solicitacao.criado_em.day} de {meses[solicitacao.criado_em.month - 1]} de {solicitacao.criado_em.year}"
    else:
        data_ext = "XX de XXXX de 2025"
    
    # --- Seção de Orientações primeiro (horizontal) ---
    if y < 50 * mm:
        y = 50 * mm
    
    y_orientacoes = y
    x_orientacoes = margin_left
    x_fator = margin_left + 110 * mm  # Ao lado direito das orientações
    
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x_orientacoes, y_orientacoes, "ORIENTAÇÕES")
    y_orientacoes -= 5 * mm
    
    c.setFont("Helvetica-Bold", 7)
    c.drawString(x_orientacoes, y_orientacoes, "CLASSIFICAÇÃO:")
    y_orientacoes -= 4 * mm
    
    # FATOR MULTIPLICADOR alinhado com CLASSIFICAÇÃO (um pouco mais para baixo)
    y_fator = y_orientacoes  # Alinhado com CLASSIFICAÇÃO
    c.setFont("Helvetica-Bold", 7)
    c.drawString(x_fator, y_fator, "FATOR MULTIPLICADOR:")
    y_fator -= 4 * mm
    
    c.setFont("Helvetica", 6)
    c.drawString(x_fator, y_fator, "PARA REEMBOLSO DE KM:")
    y_fator -= 3.5 * mm
    c.drawString(x_fator, y_fator, "MULTIPLICAR POR R$ 1,10")
    
    # Lista de classificações em formato horizontal (em colunas)
    c.setFont("Helvetica", 6)
    x_start = x_orientacoes
    y_current = y_orientacoes
    col_width = 35 * mm  # Largura de cada coluna
    max_cols = 3  # Máximo de 3 colunas
    
    for i, classificacao in enumerate(CLASSIFICACOES):
        col = i % max_cols
        row = i // max_cols
        
        if row > 0 and col == 0:
            y_current -= 3 * mm
        
        x_pos = x_start + (col * col_width)
        c.drawString(x_pos, y_current, classificacao)
    
    # Encontrar a posição mais baixa entre orientações e fator
    y_mais_baixo = min(y_current, y_fator)
    y = y_mais_baixo - 40 * mm  # Mais espaço para baixo (duas vezes mais)
    
    # Garantir espaço mínimo para as assinaturas e data (mas permitir ir mais para baixo)
    if y < 20 * mm:
        y = 20 * mm
    
    # Data um pouco mais para baixo e para a direita
    data_y = y - 10 * mm  # Mover data mais para baixo
    data_x = margin_right - -5 * mm  # Mover data um pouco para a direita
    c.setFont("Helvetica", 9)
    c.drawRightString(data_x, data_y, f"São Paulo, {data_ext}")
    
    # Linhas para assinaturas
    linha_y = y + 5 * mm  # Linha acima do texto (mais próxima)
    linha_largura = 50 * mm  # Largura da linha
    
    # Obter nome do gestor (texto legível; âncoras DocuSign são fixas e invisíveis)
    nome_gestor = _resolver_nome_gestor_para_pdf(solicitacao)
    tem_aprovador_no_pedido = bool((solicitacao.nome_gestor or "").strip())
    
    # Linha para Assinatura - Solicitante (âncora invisível para o DocuSign)
    c.line(margin_left, linha_y, margin_left + linha_largura, linha_y)
    c.saveState()
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica", 1)
    c.drawString(margin_left, linha_y, DOCUSIGN_ANCHOR_SOLICITANTE)
    c.restoreState()
    c.setFont("Helvetica", 9)
    c.drawString(margin_left, y, f"Assinatura — {nome_solicitante}")
    
    # Segundo signatário só se houver aprovador informado (evita âncora órfã no PDF)
    c.line(margin_left + 70 * mm, linha_y, margin_left + 70 * mm + linha_largura, linha_y)
    if tem_aprovador_no_pedido:
        c.saveState()
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica", 1)
        c.drawString(margin_left + 70 * mm, linha_y, DOCUSIGN_ANCHOR_GESTOR)
        c.restoreState()
        c.setFont("Helvetica", 9)
        c.drawString(margin_left + 70 * mm, y, f"Assinatura — {nome_gestor}")
    else:
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.drawString(margin_left + 70 * mm, y, "Aprovador (segunda assinatura): não informado")
        c.setFillColorRGB(0, 0, 0)
    
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def _gerar_pdf_imagem(anexo):
    """
    Gera um PDF com uma imagem anexada.
    Retorna bytes do PDF ou None em caso de erro.
    """
    try:
        buffer = BytesIO()
        w, h = A4
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = w, h
        margin_left = 20 * mm
        
        # Abrir o arquivo do storage (S3 ou local) e ler em memória
        arquivo = anexo['arquivo'].open('rb')
        arquivo_bytes = arquivo.read()
        arquivo.close()
        
        # Criar ImageReader a partir dos bytes
        img_buffer = BytesIO(arquivo_bytes)
        img = ImageReader(img_buffer)
        img_width, img_height = img.getSize()
        
        # Calcular dimensões para caber na página
        max_width = width - 2 * margin_left
        max_height = height - 60 * mm
        
        # Manter proporção
        ratio = min(max_width / img_width, max_height / img_height, 1.0)
        display_width = img_width * ratio
        display_height = img_height * ratio
        
        # Centralizar imagem
        x_img = (width - display_width) / 2
        y_img = height - 30 * mm - display_height
        
        # Título do anexo
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin_left, height - 20 * mm, f"Anexo: {anexo['nome']}")
        
        # Desenhar imagem
        c.drawImage(img, x_img, y_img, width=display_width, height=display_height, preserveAspectRatio=True)
        
        c.showPage()
        c.save()
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        return None
