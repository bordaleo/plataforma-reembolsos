from django.urls import path
from . import views

app_name = "intra"

urlpatterns = [
    path("", views.home, name="home"),
    path("reembolso/", views.reembolso, name="reembolso"),
    path("meus-reembolsos/", views.meus_reembolsos, name="meus_reembolsos"),
    path("aprovar-reembolsos/", views.aprovar_reembolsos, name="aprovar_reembolsos"),
    path("dashboard/", views.dashboard_gestor, name="dashboard_gestor"),
    path("reembolso/<int:pk>/decidir/", views.reembolso_decidir, name="reembolso_decidir"),
    path("reembolso/<int:pk>/pdf/", views.reembolso_pdf, name="reembolso_pdf"),
    path("reembolso/<int:pk>/detalhe/", views.reembolso_detalhe_json, name="reembolso_detalhe_json"),
    path("reembolso/<int:pk>/detalhe-gestor/", views.reembolso_detalhe_gestor_json, name="reembolso_detalhe_gestor_json"),
    path("reembolso/<int:pk>/anexos/", views.reembolso_anexos_json, name="reembolso_anexos_json"),
    path("meu-perfil/", views.meu_perfil, name="meu_perfil"),
    path("completar-cadastro/", views.completar_cadastro_view, name="completar_cadastro"),
    path("buscar-gestores/", views.buscar_gestores_json, name="buscar_gestores"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("esqueceu-acesso/", views.esqueceu_acesso_view, name="esqueceu_acesso"),
    path(
        "redefinir-senha/<uidb64>/<token>/",
        views.RedefinirSenhaConfirmView.as_view(),
        name="redefinir_senha",
    ),
]
