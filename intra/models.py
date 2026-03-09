from django.db import models
from django.conf import settings
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

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_solicitante",
    )
    nome_solicitante = models.CharField("Nome do solicitante", max_length=200, blank=True)
    banco = models.CharField("Banco", max_length=100, blank=True)
    agencia = models.CharField("Agência", max_length=20, blank=True)
    conta_tipo = models.CharField(
        "Tipo de conta",
        max_length=10,
        choices=CONTA_TIPO,
        blank=True,
    )
    conta_numero = models.CharField("Conta Corrente ou Poupança", max_length=30, blank=True)
    chave_pix = models.CharField("Chave PIX", max_length=100, blank=True)
    nome_gestor = models.CharField("Nome do gestor", max_length=200, blank=True)
    email_gestor = models.EmailField("E-mail do gestor", max_length=200, blank=True)

    class Meta:
        verbose_name = "Perfil do solicitante"
        verbose_name_plural = "Perfis dos solicitantes"

    @property
    def dados_completos(self):
        """True se todos os campos obrigatórios estão preenchidos."""
        return bool(
            self.nome_solicitante.strip()
            and self.banco.strip()
            and self.agencia.strip()
            and self.conta_tipo
            and self.conta_numero.strip()
            and self.chave_pix.strip()
            and self.nome_gestor.strip()
            and self.email_gestor.strip()
        )

    def __str__(self):
        return self.nome_solicitante or str(self.user)


class SolicitacaoReembolso(models.Model):
    """Solicitação de reembolso enviada pelo usuário."""

    STATUS_PENDENTE = "PENDENTE"
    STATUS_APROVADO = "APROVADO"
    STATUS_REJEITADO = "REJEITADO"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_APROVADO, "Aprovado"),
        (STATUS_REJEITADO, "Rejeitado"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="solicitacoes_reembolso",
    )
    centro_custo = models.CharField("Centro de custo", max_length=50)
    cod_despesa = models.CharField("Código de despesa", max_length=50)
    valor_total = models.DecimalField(
        "Valor total",
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    criado_em = models.DateTimeField("Data da solicitação", auto_now_add=True)
    status = models.CharField(
        "Status",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
    )
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

    class Meta:
        verbose_name = "Solicitação de reembolso"
        verbose_name_plural = "Solicitações de reembolso"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"Reembolso {self.pk} - {self.valor_total}"


class RegraUsuario(models.Model):
    """Regra de permissionamento do usuário (role). Atribuído no Admin em cada User."""

    ROLE_GESTOR_ADMINISTRATIVO = "gestor_administrativo"
    ROLE_CHOICES = [
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
    cod_despesa = models.CharField("Código de despesa", max_length=50, blank=True)
    data_despesa = models.DateField("Data da despesa", null=True, blank=True)
    descricao = models.CharField("Descrição", max_length=300, blank=True)
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