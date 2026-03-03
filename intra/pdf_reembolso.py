"""
Gera PDF de reembolso conforme modelo MODELO_REEMBOLSO.pdf
"""
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# Mapeamento tipo_despesa (valor no form) -> rótulo no PDF (como no modelo)
ROTULOS_TIPO = {
    "REFEICAO": "REFEIÇÃO",
    "LOCOMACAO": "LOCOMOÇÃO",
    "PASSAGENS": "PASSAGENS",
    "PEDAGIO": "PEDÁGIO",
    "DESLOCAMENTO": "DESLOCAMENTO KM",
    "OUTROS_MATERIAIS": "OUTROS",
}


def _formatar_valor(valor):
    if valor is None or valor == "":
        return "-"
    try:
        v = float(valor)
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "-"


def gerar_pdf(solicitacao):
    """
    Gera o PDF da solicitação de reembolso conforme o modelo.
    solicitacao: instância de SolicitacaoReembolso com itens e user com perfil_solicitante.
    """
    buffer = BytesIO()
    w, h = A4
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = w, h
    margin_left = 20 * mm
    margin_right = width - 20 * mm
    y = height - 25 * mm

    # --- Cabeçalho (Associação) ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin_left, y, "ASSOCIAÇÃO PARCEIROS DA EDUCAÇÃO")
    y -= 5 * mm
    c.setFont("Helvetica", 10)
    c.drawString(margin_left, y, "CNPJ: 06.878.967/0001-57")
    y -= 4 * mm
    c.drawString(margin_left, y, "Rua Funchal, 513 - conjunto 71")
    y -= 4 * mm
    c.drawString(margin_left, y, "Vila Olímpia, São Paulo – SP")
    y -= 4 * mm
    c.drawString(margin_left, y, "CEP: 04551-909")
    y -= 10 * mm

    # --- Título ---
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin_left, y, "Reembolso de Despesas")
    y -= 10 * mm

    # --- Solicito o reembolso do valor de ---
    c.setFont("Helvetica", 10)
    c.drawString(margin_left, y, "Solicito o reembolso do valor de:")
    valor_str = _formatar_valor(solicitacao.valor_total)
    c.drawRightString(margin_right, y, f"R$ {valor_str}")
    y -= 8 * mm

    # --- Referente ao... Descrição ---
    c.drawString(margin_left, y, "Referente ao reembolso de despesas relacionadas aos seguintes eventos:")
    y -= 5 * mm
    c.drawString(margin_left, y, "Descrição:")
    descricao = " / ".join(
        [f"{solicitacao.centro_custo} - {solicitacao.cod_despesa}"]
        + [f"{i.tipo_despesa}: {i.descricao or '-'}" for i in solicitacao.itens.all()]
    )
    if len(descricao) > 100:
        descricao = descricao[:97] + "..."
    y -= 5 * mm
    c.drawString(margin_left, y, descricao[:80] if len(descricao) > 80 else descricao)
    y -= 10 * mm

    # --- Tabela: Nota de débito / Classificação ---
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin_left, y, "Nota de débito para Reembolso de Despesas")
    y -= 5 * mm
    c.setFont("Helvetica", 9)
    c.drawString(margin_left, y, f"Programa: {solicitacao.centro_custo}")
    y -= 5 * mm
    c.drawString(margin_left, y, "CLASSIFICAÇÃO")
    y -= 6 * mm

    # Cabeçalho da tabela: CÓD. DESP., DATA, TIPO, DESCRIÇÃO, km, VALOR, HOSPEDAGEM
    col_cod = margin_left
    col_data = margin_left + 22 * mm
    col_tipo = margin_left + 38 * mm
    col_desc = margin_left + 58 * mm
    col_km = margin_left + 95 * mm
    col_valor = margin_left + 105 * mm
    col_hosp = margin_left + 120 * mm
    c.setFont("Helvetica-Bold", 8)
    c.drawString(col_cod, y, "CÓD. DESP.")
    c.drawString(col_data, y, "DATA")
    c.drawString(col_tipo, y, "TIPO")
    c.drawString(col_desc, y, "DESCRIÇÃO")
    c.drawString(col_km, y, "km")
    c.drawString(col_valor, y, "VALOR")
    c.drawString(col_hosp, y, "HOSP.")
    y -= 5 * mm

    data_str = solicitacao.criado_em.strftime("%d/%m/%Y") if solicitacao.criado_em else "-"
    cod_desp = (solicitacao.cod_despesa[:10] if solicitacao.cod_despesa else "-")[:10]
    itens = list(solicitacao.itens.all())

    if itens:
        c.setFont("Helvetica", 9)
        for i, item in enumerate(itens):
            tipo_label = ROTULOS_TIPO.get(item.tipo_despesa, item.tipo_despesa.replace("_", " ").title())
            descricao_item = (item.descricao or "-").strip()[:35]
            val_str = _formatar_valor(item.valor)
            km_str = _formatar_valor(item.km) if item.km is not None else "-"
            if i == 0:
                c.drawString(col_cod, y, cod_desp)
                c.drawString(col_data, y, data_str)
            c.drawString(col_tipo, y, tipo_label[:14])
            c.drawString(col_desc, y, descricao_item)
            c.drawString(col_km, y, km_str)
            c.drawString(col_valor, y, val_str)
            c.drawString(col_hosp, y, "-")
            y -= 5 * mm
    else:
        c.setFont("Helvetica", 9)
        c.drawString(col_cod, y, cod_desp)
        c.drawString(col_data, y, data_str)
        c.drawString(col_tipo, y, "-")
        c.drawString(col_desc, y, "(sem itens)")
        c.drawString(col_km, y, "-")
        c.drawString(col_valor, y, _formatar_valor(solicitacao.valor_total))
        c.drawString(col_hosp, y, "-")
        y -= 5 * mm

    y -= 3 * mm
    c.setFont("Helvetica", 8)
    c.drawString(margin_left, y, "FATOR MULTIPLICADOR PARA REEMBOLSO DE KM: MULTIPLICAR POR R$ 1,10")
    y -= 12 * mm

    # --- Depósito em conta ---
    c.setFont("Helvetica", 10)
    c.drawString(margin_left, y, "Solicito providenciar depósito em minha conta corrente, conforme dados abaixo:")
    y -= 8 * mm

    perfil = getattr(solicitacao.user, "perfil_solicitante", None)
    if perfil:
        c.setFont("Helvetica", 9)
        c.drawString(margin_left, y, f"Nome: {perfil.nome_solicitante or '-'}")
        y -= 5 * mm
        c.drawString(margin_left, y, f"PIX  Email: {solicitacao.user.email or '-'}")
        y -= 5 * mm
        c.drawString(margin_left, y, "CPF/CNPJ: (conforme cadastro)")
        y -= 5 * mm
        c.drawString(margin_left, y, f"Banco: {perfil.banco or '-'}")
        y -= 5 * mm
        c.drawString(margin_left, y, f"Agencia: {perfil.agencia or '-'}")
        y -= 5 * mm
        c.drawString(margin_left, y, f"Conta: {perfil.conta_numero or '-'}")
    else:
        c.drawString(margin_left, y, "Dados de pagamento não cadastrados.")
    y -= 12 * mm

    # --- Rodapé ---
    c.setFont("Helvetica-Bold", 10)
    numero = f"{solicitacao.pk:02d}/{solicitacao.criado_em.year}" if solicitacao.criado_em else f"{solicitacao.pk:02d}/2025"
    c.drawString(margin_left, y, f"NOTA DE DÉBITO / REEMBOLSO - N° {numero}")
    y -= 6 * mm
    c.setFont("Helvetica", 9)
    c.drawString(margin_left, y, "Assinatura - Solicitante")
    c.drawString(margin_left + 70 * mm, y, "Assinatura - Gestor")
    y -= 5 * mm
    meses = ("janeiro", "fevereiro", "março", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
    if solicitacao.criado_em:
        data_ext = f"{solicitacao.criado_em.day} de {meses[solicitacao.criado_em.month - 1]} de {solicitacao.criado_em.year}"
    else:
        data_ext = "_____ de __________ de ______"
    c.drawRightString(margin_right, y, f"São Paulo, {data_ext}")
    y -= 5 * mm
    c.drawRightString(margin_right, y, f"R$ {_formatar_valor(solicitacao.valor_total)}")
    y -= 5 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(margin_right, y, "GESTÃO APE")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
