"""
Redireciona usuário logado que ainda não completou nome e dados de pagamento
para /completar-cadastro/. Usa request.path para evitar loop (resolver_match
pode não estar definido quando o middleware executa).
"""
from django.shortcuts import redirect
from django.urls import reverse
from .models import PerfilSolicitante, RegraUsuario

# URLs que não exigem cadastro completo (prefixos)
_PATH_PODE_NAO_TER_CADASTRO = (
    "/login",
    "/logout",
    "/esqueceu-acesso",
    "/completar-cadastro",
    "/redefinir-senha",
    "/admin",
    "/aprovar-reembolsos",
    "/buscar-gestores",
)


def _is_gestor(user):
    """Usuário com regra Gestor ou Gestor Administrativo não precisa completar cadastro."""
    return (
        user.is_authenticated
        and (
            user.regras_usuario.filter(role=RegraUsuario.ROLE_GESTOR).exists()
            or user.regras_usuario.filter(role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO).exists()
        )
    )


class ExigeCadastroCompletoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Proteção contra request sem user (caso raro, mas pode acontecer)
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return self.get_response(request)
        # Gestor não precisa preencher dados no primeiro login
        if _is_gestor(request.user):
            return self.get_response(request)
        path = request.path
        if any(path.startswith(p) for p in _PATH_PODE_NAO_TER_CADASTRO):
            return self.get_response(request)
        try:
            perfil, _ = PerfilSolicitante.objects.get_or_create(user=request.user)
            if not perfil.dados_completos:
                return redirect(reverse("intra:completar_cadastro") + "?next=" + path)
        except Exception:
            # Em caso de erro, permitir continuar para não bloquear o acesso
            pass
        return self.get_response(request)
