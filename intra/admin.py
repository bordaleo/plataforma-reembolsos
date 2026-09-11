from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import PerfilSolicitante, RegraUsuario, SolicitacaoReembolso

User = get_user_model()


class RegraUsuarioInline(admin.TabularInline):
    model = RegraUsuario
    extra = 0
    verbose_name = "Regra de usuário"
    verbose_name_plural = "Regras de usuários"
    fields = ("role",)  # user já é o do formulário


# Estende o User admin para incluir a seção "Regras de usuários"
class UserAdminComRegras(BaseUserAdmin):
    inlines = [RegraUsuarioInline]


# Só registra se o User ainda estiver com o admin padrão (evita duplicar)
if User in admin.site._registry:
    admin.site.unregister(User)
admin.site.register(User, UserAdminComRegras)


@admin.register(RegraUsuario)
class RegraUsuarioAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)
    search_fields = ("user__email",)
    autocomplete_fields = ("user",)


@admin.register(PerfilSolicitante)
class PerfilSolicitanteAdmin(admin.ModelAdmin):
    list_display = ("user", "nome_solicitante", "tipo_pessoa", "cnpj", "banco", "conta_tipo")
    list_filter = ("tipo_pessoa",)
    search_fields = ("nome_solicitante", "cnpj", "user__email")


@admin.register(SolicitacaoReembolso)
class SolicitacaoReembolsoAdmin(admin.ModelAdmin):
    list_display = ("user", "centro_custo", "cod_despesa", "valor_total", "status", "criado_em")
    list_filter = ("status", "criado_em")
    search_fields = ("user__email", "centro_custo", "cod_despesa")
