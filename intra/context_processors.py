"""
Context processor para expor is_gestor nas templates.
Gestor = usuário com regra "Gestor" ou "Gestor Administrativo" (Regras de usuários no Admin).
"""
from .models import RegraUsuario


def intra_context(request):
    if not request.user.is_authenticated:
        return {"is_gestor": False, "is_gestor_simples": False, "is_gestor_admin": False}
    is_gestor_simples = request.user.regras_usuario.filter(
        role=RegraUsuario.ROLE_GESTOR
    ).exists()
    is_gestor_admin = request.user.regras_usuario.filter(
        role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO
    ).exists()
    is_gestor = is_gestor_simples or is_gestor_admin
    return {
        "is_gestor": is_gestor,
        "is_gestor_simples": is_gestor_simples,
        "is_gestor_admin": is_gestor_admin,
    }
