from django.urls import path
from . import views

app_name = "intra"

urlpatterns = [
    path("", views.home, name="home"),
    path("reembolso/", views.reembolso, name="reembolso"),
    path("meus-reembolsos/", views.meus_reembolsos, name="meus_reembolsos"),
    path("aprovar-reembolsos/", views.aprovar_reembolsos, name="aprovar_reembolsos"),
    path("reembolso/<int:pk>/decidir/", views.reembolso_decidir, name="reembolso_decidir"),
    path("reembolso/<int:pk>/pdf/", views.reembolso_pdf, name="reembolso_pdf"),
    path("reembolso/<int:pk>/detalhe/", views.reembolso_detalhe_json, name="reembolso_detalhe_json"),
    path("meu-perfil/", views.meu_perfil, name="meu_perfil"),
    path("completar-cadastro/", views.completar_cadastro_view, name="completar_cadastro"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("esqueceu-acesso/", views.esqueceu_acesso_view, name="esqueceu_acesso"),
    path(
        "redefinir-senha/<uidb64>/<token>/",
        views.RedefinirSenhaConfirmView.as_view(),
        name="redefinir_senha",
    ),
]
