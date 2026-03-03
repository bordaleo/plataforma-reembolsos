"""
Context processor para expor is_gestor nas templates.
Gestor = usuário com regra "Gestor Administrativo" (Regras de usuários no Admin).
"""
from .models import RegraUsuario


def intra_context(request):
    if not request.user.is_authenticated:
        return {"is_gestor": False}
    is_gestor = request.user.regras_usuario.filter(
        role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO
    ).exists()
    return {"is_gestor": is_gestor}
