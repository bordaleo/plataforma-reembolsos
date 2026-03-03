# Generated manually - cria o grupo Gestor Administrativo

from django.db import migrations


def criar_grupo_gestor(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="Gestor Administrativo")


def reverter(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="Gestor Administrativo").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("intra", "0004_status_aprovacao_reembolso"),
    ]

    operations = [
        migrations.RunPython(criar_grupo_gestor, reverter),
    ]
