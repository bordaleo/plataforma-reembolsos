from django.db import migrations, models


TIPOS_INICIAIS = [
    ("DESLOCAMENTO", "Deslocamento", "DESLOCAMENTO KM", 1, True, False),
    ("LOCOMACAO", "Locomoção", "LOCOMOÇÃO", 2, False, False),
    ("PEDAGIO", "Pedágio", "PEDÁGIO", 3, False, False),
    ("ESTACIONAMENTO", "Estacionamento", "ESTACIONAMENTO", 4, False, False),
    ("REFEICAO", "Refeição", "REFEIÇÃO", 5, False, False),
    ("PASSAGENS", "Passagens de Ônibus", "PASSAGENS DE ÔNIBUS", 6, False, True),
    ("OUTROS_MATERIAIS", "Outros", "OUTROS", 7, False, False),
]


def seed_tipos_despesa(apps, schema_editor):
    TipoDespesa = apps.get_model("intra", "TipoDespesa")
    for codigo, nome, rotulo_pdf, ordem, exige_km, anexo_opcional in TIPOS_INICIAIS:
        TipoDespesa.objects.update_or_create(
            codigo=codigo,
            defaults={
                "nome": nome,
                "rotulo_pdf": rotulo_pdf,
                "ordem": ordem,
                "ativo": True,
                "exige_km": exige_km,
                "anexo_opcional": anexo_opcional,
            },
        )


def unseed_tipos_despesa(apps, schema_editor):
    TipoDespesa = apps.get_model("intra", "TipoDespesa")
    TipoDespesa.objects.filter(codigo__in=[t[0] for t in TIPOS_INICIAIS]).delete()


def seed_programas(apps, schema_editor):
    Programa = apps.get_model("intra", "Programa")
    CentroCusto = apps.get_model("intra", "CentroCusto")
    nomes = (
        CentroCusto.objects.exclude(PROGRAMA__isnull=True)
        .exclude(PROGRAMA__exact="")
        .values_list("PROGRAMA", flat=True)
        .distinct()
    )
    for ordem, nome in enumerate(sorted({n for n in nomes if n}), start=1):
        Programa.objects.get_or_create(
            nome=nome,
            defaults={"ordem": ordem, "ativo": True},
        )


def unseed_programas(apps, schema_editor):
    Programa = apps.get_model("intra", "Programa")
    Programa.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("intra", "0027_perfilsolicitante_tipo_pessoa_cnpj"),
    ]

    operations = [
        migrations.CreateModel(
            name="TipoDespesa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo", models.CharField(max_length=30, unique=True, verbose_name="Código interno")),
                ("nome", models.CharField(max_length=80, verbose_name="Nome")),
                ("rotulo_pdf", models.CharField(blank=True, max_length=80, verbose_name="Rótulo no PDF")),
                ("ordem", models.PositiveSmallIntegerField(default=0, verbose_name="Ordem")),
                ("ativo", models.BooleanField(default=True, verbose_name="Ativo")),
                (
                    "exige_km",
                    models.BooleanField(
                        default=False,
                        help_text="Abre o cálculo de quilometragem (KM × R$ 1,10).",
                        verbose_name="Reembolso por KM",
                    ),
                ),
                (
                    "anexo_opcional",
                    models.BooleanField(
                        default=False,
                        help_text="Não exige comprovante anexado (como Passagens de Ônibus).",
                        verbose_name="Anexo opcional",
                    ),
                ),
            ],
            options={
                "verbose_name": "Tipo de despesa",
                "verbose_name_plural": "Tipos de despesa",
                "ordering": ["ordem", "nome"],
            },
        ),
        migrations.CreateModel(
            name="Programa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=200, unique=True, verbose_name="Nome")),
                ("ordem", models.PositiveSmallIntegerField(default=0, verbose_name="Ordem")),
                ("ativo", models.BooleanField(default=True, verbose_name="Ativo")),
            ],
            options={
                "verbose_name": "Programa",
                "verbose_name_plural": "Programas",
                "ordering": ["ordem", "nome"],
            },
        ),
        migrations.AddField(
            model_name="centrocusto",
            name="ativo",
            field=models.BooleanField(default=True, verbose_name="Ativo"),
        ),
        migrations.AlterModelOptions(
            name="centrocusto",
            options={
                "verbose_name": "Código no orçamento",
                "verbose_name_plural": "Códigos no orçamento",
            },
        ),
        migrations.RunPython(seed_tipos_despesa, unseed_tipos_despesa),
        migrations.RunPython(seed_programas, unseed_programas),
    ]
