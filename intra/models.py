from django.db import models
from django.conf import settings
from django.utils import timezone
from storages.backends.s3boto3 import S3Boto3Storage


class MediaStorage(S3Boto3Storage):
    """Storage customizado para arquivos de mídia no S3."""
    location = ''
    file_overwrite = False


class PerfilSolicitante(models.Model):
    """Dados obrigatórios do solicitante (primeiro acesso)."""

    CONTA_TIPO = [
        ("CORRENTE", "Conta Corrente"),
        ("POUPANCA", "Poupança"),
    ]

    FORMA_PAGAMENTO_CHOICES = [
        ("PIX", "PIX"),
        ("TRANSFERENCIA", "Transferência Bancária"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_solicitante",
    )
    nome_solicitante = models.CharField("Nome do solicitante", max_length=200, blank=True)
    forma_pagamento = models.CharField(
        "Forma de Pagamento",
        max_length=20,
        choices=FORMA_PAGAMENTO_CHOICES,
        blank=True,
    )
    # Campos para PIX
    chave_pix = models.CharField("Chave PIX", max_length=100, blank=True)
    banco_pix = models.CharField("Banco (PIX)", max_length=100, blank=True)
    cpf_pix = models.CharField("CPF/CNPJ (PIX)", max_length=18, blank=True)
    # Campos para Transferência Bancária
    banco = models.CharField("Banco", max_length=100, blank=True)
    agencia = models.CharField("Agência", max_length=20, blank=True)
    conta_tipo = models.CharField(
        "Tipo de conta",
        max_length=10,
        choices=CONTA_TIPO,
        blank=True,
    )
    conta_numero = models.CharField("Conta Corrente ou Poupança", max_length=30, blank=True)
    cpf_transferencia = models.CharField("CPF/CNPJ (Transferência)", max_length=18, blank=True)
    # Campos de gestor (mantidos para compatibilidade, mas não mais obrigatórios no cadastro)
    nome_gestor = models.CharField("Nome do gestor", max_length=200, blank=True)
    email_gestor = models.EmailField("E-mail do gestor", max_length=200, blank=True)

    class Meta:
        verbose_name = "Perfil do solicitante"
        verbose_name_plural = "Perfis dos solicitantes"

    @property
    def dados_completos(self):
        """True se todos os campos obrigatórios estão preenchidos."""
        if not self.nome_solicitante or not self.nome_solicitante.strip():
            return False
        
        if not self.forma_pagamento:
            return False
        
        if self.forma_pagamento == "PIX":
            return bool(
                self.chave_pix and self.chave_pix.strip()
                and self.banco_pix and self.banco_pix.strip()
                and self.cpf_pix and self.cpf_pix.strip()
            )
        elif self.forma_pagamento == "TRANSFERENCIA":
            return bool(
                self.banco and self.banco.strip()
                and self.agencia and self.agencia.strip()
                and self.conta_tipo
                and self.conta_numero and self.conta_numero.strip()
                and self.cpf_transferencia and self.cpf_transferencia.strip()
            )
        
        return False

    def __str__(self):
        return self.nome_solicitante or str(self.user)


class SolicitacaoReembolso(models.Model):
    """Solicitação de reembolso enviada pelo usuário."""

    STATUS_PENDENTE = "PENDENTE"
    STATUS_APROVADO = "APROVADO"
    STATUS_REJEITADO = "REJEITADO"
    STATUS_AGUARDANDO_PAGAMENTO = "AGUARDANDO_PAGAMENTO"
    STATUS_PAGAMENTO_AGENDADO = "PAGAMENTO_AGENDADO"
    STATUS_PAGO_AGUARDANDO_ASSINATURAS = "PAGO_AGUARDANDO_ASSINATURAS"
    STATUS_ASSINADO_TODAS_PARTES = "ASSINADO_TODAS_PARTES"
    STATUS_CONCLUIDO = "CONCLUIDO"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_APROVADO, "Aprovado"),
        (STATUS_REJEITADO, "Rejeitado"),
        (STATUS_AGUARDANDO_PAGAMENTO, "Aprovado - Aguardando pagamento"),
        (STATUS_PAGAMENTO_AGENDADO, "Solicitação aprovada - Pagamento agendado"),
        (STATUS_PAGO_AGUARDANDO_ASSINATURAS, "Pago - Aguardando assinaturas"),
        (STATUS_ASSINADO_TODAS_PARTES, "Assinado por todas as partes"),
        (STATUS_CONCLUIDO, "Concluído"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="solicitacoes_reembolso",
    )
    centro_custo = models.CharField("Centro de custo", max_length=50)
    cod_despesa = models.CharField("Código no orçamento", max_length=50)
    valor_total = models.DecimalField(
        "Valor total",
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    criado_em = models.DateTimeField("Data da solicitação", auto_now_add=True)
    # Status geral (final) - será APROVADO apenas quando gestor admin aprovar
    status = models.CharField(
        "Status",
        max_length=50,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
    )
    # Campos de aprovação do Gestor (primeiro nível)
    status_gestor = models.CharField(
        "Status do Gestor",
        max_length=50,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
    )
    aprovado_por_gestor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reembolsos_aprovados_gestor",
    )
    aprovado_em_gestor = models.DateTimeField("Data da decisão do gestor", null=True, blank=True)
    motivo_rejeicao_gestor = models.CharField(
        "Motivo da rejeição pelo gestor",
        max_length=500,
        blank=True,
    )
    # Campos de aprovação do Gestor Administrativo (segundo nível)
    status_gestor_admin = models.CharField(
        "Status do Gestor Administrativo",
        max_length=50,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
    )
    aprovado_por_gestor_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reembolsos_aprovados_gestor_admin",
    )
    aprovado_em_gestor_admin = models.DateTimeField("Data da decisão do gestor administrativo", null=True, blank=True)
    motivo_rejeicao_gestor_admin = models.CharField(
        "Motivo da rejeição pelo gestor administrativo",
        max_length=500,
        blank=True,
    )
    # Campos legados (mantidos para compatibilidade, mas não mais usados)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reembolsos_aprovados",
    )
    aprovado_em = models.DateTimeField("Data da decisão", null=True, blank=True)
    motivo_rejeicao = models.CharField(
        "Motivo da rejeição",
        max_length=500,
        blank=True,
    )
    # Campo para marcar como concluído pelo gestor administrativo
    concluido = models.BooleanField(
        "Concluído",
        default=False,
        help_text="Marcado quando o gestor administrativo conclui a solicitação"
    )
    concluido_em = models.DateTimeField("Data da conclusão", null=True, blank=True)
    # Campo para marcar como pago
    pago = models.BooleanField(
        "Pago",
        default=False,
        help_text="Marcado quando o gestor administrativo marca como pago"
    )
    pago_em = models.DateTimeField("Data do pagamento", null=True, blank=True)
    # Campo para data de pagamento programada
    data_pagamento_programada = models.DateField(
        "Data de pagamento programada",
        null=True,
        blank=True,
        help_text="Data programada para o pagamento pelo gestor administrativo"
    )
    # Campos DocuSign
    envelope_id_docusign = models.CharField(
        "ID do Envelope DocuSign",
        max_length=200,
        blank=True,
        null=True,
    )
    status_docusign = models.CharField(
        "Status da Assinatura DocuSign",
        max_length=50,
        blank=True,
        null=True,
    )
    # Campos de forma de pagamento
    FORMA_PAGAMENTO_CHOICES = [
        ("PIX", "PIX"),
        ("TRANSFERENCIA", "Transferência Bancária"),
    ]
    forma_pagamento = models.CharField(
        "Forma de Pagamento",
        max_length=20,
        choices=FORMA_PAGAMENTO_CHOICES,
        blank=True,
        null=True,
    )
    # Campos para PIX
    pix_chave = models.CharField("Chave PIX", max_length=100, blank=True, null=True)
    pix_banco = models.CharField("Banco (PIX)", max_length=100, blank=True, null=True)
    pix_cpf = models.CharField("CPF/CNPJ (PIX)", max_length=18, blank=True, null=True)
    # Campos para Transferência
    transf_banco = models.CharField("Banco (Transferência)", max_length=100, blank=True, null=True)
    transf_agencia = models.CharField("Agência (Transferência)", max_length=20, blank=True, null=True)
    transf_conta_tipo = models.CharField(
        "Tipo de Conta (Transferência)",
        max_length=10,
        choices=PerfilSolicitante.CONTA_TIPO,
        blank=True,
        null=True,
    )
    transf_conta_numero = models.CharField("Conta (Transferência)", max_length=30, blank=True, null=True)
    transf_cpf = models.CharField("CPF/CNPJ (Transferência)", max_length=18, blank=True, null=True)
    # Campo para nome do gestor específico da solicitação
    nome_gestor = models.CharField("Nome do Gestor", max_length=200, blank=True, null=True)

    class Meta:
        verbose_name = "Solicitação de reembolso"
        verbose_name_plural = "Solicitações de reembolso"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"Reembolso {self.pk} - {self.valor_total}"

    def save(self, *args, **kwargs):
        # Evita inconsistência (ex.: status Concluído no admin sem marcar concluido),
        # que fazia o mesmo pedido aparecer em "Em processo" e em "Concluídos".
        if self.status == self.STATUS_CONCLUIDO and not self.concluido:
            self.concluido = True
        # Data de conclusão oficial: se concluído sem data, usa a do pagamento (quando houver), senão agora.
        if self.concluido and self.status == self.STATUS_CONCLUIDO and not self.concluido_em:
            self.concluido_em = self.pago_em or timezone.now()
        super().save(*args, **kwargs)


class RegraUsuario(models.Model):
    """Regra de permissionamento do usuário (role). Atribuído no Admin em cada User."""

    ROLE_GESTOR = "gestor"
    ROLE_GESTOR_ADMINISTRATIVO = "gestor_administrativo"
    ROLE_CHOICES = [
        (ROLE_GESTOR, "Gestor"),
        (ROLE_GESTOR_ADMINISTRATIVO, "Gestor Administrativo"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="regras_usuario",
        verbose_name="Usuário",
    )
    role = models.CharField(
        "Tipo de permissionamento",
        max_length=50,
        choices=ROLE_CHOICES,
    )

    class Meta:
        verbose_name = "Regra de usuário"
        verbose_name_plural = "Regras de usuários"
        unique_together = [("user", "role")]

    def __str__(self):
        return f"{self.user} — {self.get_role_display()}"


class ItemReembolso(models.Model):
    """Item (linha) de uma solicitação de reembolso."""

    solicitacao = models.ForeignKey(
        SolicitacaoReembolso,
        on_delete=models.CASCADE,
        related_name="itens",
    )
    tipo_despesa = models.CharField("Tipo de despesa", max_length=30)
    cod_despesa = models.CharField("Código no orçamento", max_length=50, blank=True)
    data_despesa = models.DateField("Data da despesa", null=True, blank=True)
    descricao = models.TextField(max_length=300)  # ou CharField(max_length=300)
    valor = models.DecimalField("Valor", max_digits=12, decimal_places=2, default=0)
    km = models.DecimalField("KM (deslocamento)", max_digits=10, decimal_places=2, null=True, blank=True)
    anexo = models.FileField("Anexo", upload_to="reembolsos/anexos/", storage=MediaStorage(), blank=True, null=True)

    class Meta:
        verbose_name = "Item de reembolso"
        verbose_name_plural = "Itens de reembolso"

class CentroCusto(models.Model):
    PROGRAMA = models.CharField(max_length=150000, null=True, blank=True)
    CODIGO = models.CharField(max_length=150000, null=True, blank=True)
    DESCRICAO = models.CharField(max_length=150000, null=True, blank=True)

    def __str__(self):
        return f"{self.CODIGO} - {self.DESCRICAO}"


class HistoricoReembolso(models.Model):
    """Histórico de alterações de uma solicitação de reembolso."""
    
    solicitacao = models.ForeignKey(
        SolicitacaoReembolso,
        on_delete=models.CASCADE,
        related_name="historico",
    )
    acao = models.CharField("Ação", max_length=100)
    descricao = models.TextField("Descrição", blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historico_reembolsos",
    )
    criado_em = models.DateTimeField("Data", auto_now_add=True)
    
    class Meta:
        verbose_name = "Histórico de reembolso"
        verbose_name_plural = "Históricos de reembolsos"
        ordering = ["-criado_em"]
    
    def __str__(self):
        return f"{self.solicitacao.pk} - {self.acao} - {self.criado_em}"