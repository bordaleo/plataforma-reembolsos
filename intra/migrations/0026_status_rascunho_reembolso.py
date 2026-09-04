from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intra", "0025_alter_itemreembolso_cod_despesa_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="solicitacaoreembolso",
            name="centro_custo",
            field=models.CharField(blank=True, max_length=50, verbose_name="Centro de custo"),
        ),
        migrations.AlterField(
            model_name="solicitacaoreembolso",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDENTE", "Pendente"),
                    ("RASCUNHO", "Rascunho"),
                    ("APROVADO", "Aprovado"),
                    ("REJEITADO", "Rejeitado"),
                    ("AGUARDANDO_PAGAMENTO", "Aprovado - Aguardando pagamento"),
                    ("PAGAMENTO_AGENDADO", "Solicitação aprovada - Pagamento agendado"),
                    ("PAGO_AGUARDANDO_ASSINATURAS", "Pago - Aguardando assinaturas"),
                    ("ASSINADO_TODAS_PARTES", "Assinado por todas as partes"),
                    ("CONCLUIDO", "Concluído"),
                ],
                default="PENDENTE",
                max_length=50,
                verbose_name="Status",
            ),
        ),
        migrations.AlterField(
            model_name="solicitacaoreembolso",
            name="status_gestor",
            field=models.CharField(
                choices=[
                    ("PENDENTE", "Pendente"),
                    ("RASCUNHO", "Rascunho"),
                    ("APROVADO", "Aprovado"),
                    ("REJEITADO", "Rejeitado"),
                    ("AGUARDANDO_PAGAMENTO", "Aprovado - Aguardando pagamento"),
                    ("PAGAMENTO_AGENDADO", "Solicitação aprovada - Pagamento agendado"),
                    ("PAGO_AGUARDANDO_ASSINATURAS", "Pago - Aguardando assinaturas"),
                    ("ASSINADO_TODAS_PARTES", "Assinado por todas as partes"),
                    ("CONCLUIDO", "Concluído"),
                ],
                default="PENDENTE",
                max_length=50,
                verbose_name="Status do Gestor",
            ),
        ),
        migrations.AlterField(
            model_name="solicitacaoreembolso",
            name="status_gestor_admin",
            field=models.CharField(
                choices=[
                    ("PENDENTE", "Pendente"),
                    ("RASCUNHO", "Rascunho"),
                    ("APROVADO", "Aprovado"),
                    ("REJEITADO", "Rejeitado"),
                    ("AGUARDANDO_PAGAMENTO", "Aprovado - Aguardando pagamento"),
                    ("PAGAMENTO_AGENDADO", "Solicitação aprovada - Pagamento agendado"),
                    ("PAGO_AGUARDANDO_ASSINATURAS", "Pago - Aguardando assinaturas"),
                    ("ASSINADO_TODAS_PARTES", "Assinado por todas as partes"),
                    ("CONCLUIDO", "Concluído"),
                ],
                default="PENDENTE",
                max_length=50,
                verbose_name="Status do Gestor Administrativo",
            ),
        ),
    ]
