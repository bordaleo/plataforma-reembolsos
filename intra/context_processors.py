"""
Context processor para expor is_gestor nas templates.
Gestor = usuário com regra "Gestor" ou "Gestor Administrativo" (Regras de usuários no Admin).
"""
from .models import RegraUsuario, PerfilSolicitante


def intra_context(request):
    if not request.user.is_authenticated:
        return {"is_gestor": False, "is_gestor_simples": False, "is_gestor_admin": False, "user_display_name": ""}
    is_gestor_simples = request.user.regras_usuario.filter(
        role=RegraUsuario.ROLE_GESTOR
    ).exists()
    is_gestor_admin = request.user.regras_usuario.filter(
        role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO
    ).exists()
    is_gestor = is_gestor_simples or is_gestor_admin
    
    # Buscar nome do usuário para exibição
    user_display_name = ""
    try:
        perfil = PerfilSolicitante.objects.get(user=request.user)
        if perfil.nome_solicitante:
            user_display_name = perfil.nome_solicitante
    except PerfilSolicitante.DoesNotExist:
        pass
    
    if not user_display_name:
        user_display_name = request.user.get_full_name() or request.user.email
    
    return {
        "is_gestor": is_gestor,
        "is_gestor_simples": is_gestor_simples,
        "is_gestor_admin": is_gestor_admin,
        "user_display_name": user_display_name,
    }
