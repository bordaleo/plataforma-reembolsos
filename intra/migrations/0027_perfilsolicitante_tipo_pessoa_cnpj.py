from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intra", "0026_status_rascunho_reembolso"),
    ]

    operations = [
        migrations.AddField(
            model_name="perfilsolicitante",
            name="tipo_pessoa",
            field=models.CharField(
                blank=True,
                choices=[("PF", "Pessoa Física"), ("PJ", "Pessoa Jurídica")],
                max_length=2,
                verbose_name="Tipo de pessoa",
            ),
        ),
        migrations.AddField(
            model_name="perfilsolicitante",
            name="cnpj",
            field=models.CharField(blank=True, max_length=18, verbose_name="CNPJ"),
        ),
    ]
