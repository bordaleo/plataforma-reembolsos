from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib.auth.views import PasswordResetConfirmView
from django.core.mail import send_mail
from django.conf import settings
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.utils.crypto import get_random_string
import json

from .forms import LoginForm, EsqueceuAcessoForm, CompletarCadastroForm
from .models import PerfilSolicitante, RegraUsuario, SolicitacaoReembolso, ItemReembolso
from .pdf_reembolso import gerar_pdf

User = get_user_model()


def _is_gestor(user):
    """Usuário com regra Gestor Administrativo (Regras de usuários no Admin)."""
    return (
        user.is_authenticated
        and user.regras_usuario.filter(role=RegraUsuario.ROLE_GESTOR_ADMINISTRATIVO).exists()
    )

# Dados mockados para o formulário de reembolso
CENTROS_CUSTO = [
    ("CC001", "Administrativo"),
    ("CC002", "Comercial"),
    ("CC003", "Marketing"),
    ("CC004", "TI / Tecnologia"),
    ("CC005", "Operações"),
]

CODIGOS_DESPESA = [
    ("DESP001", "Despesas Gerais"),
    ("DESP002", "Viagens"),
    ("DESP003", "Alimentação"),
    ("DESP004", "Transporte"),
    ("DESP005", "Materiais de Escritório"),
]

TIPOS_DESPESA = [
    ("DESLOCAMENTO", "Deslocamento"),
    ("LOCOMACAO", "Locomoção"),
    ("PEDAGIO", "Pedágio"),
    ("REFEICAO", "Refeição"),
    ("PASSAGENS", "Passagens"),
    ("OUTROS_MATERIAIS", "Outros Materiais"),
]


def login_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    form = LoginForm(request.POST or None)
    if form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        password = form.cleaned_data["password"]
        user = authenticate(request, email=email, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get("next") or settings.LOGIN_REDIRECT_URL
            return redirect(next_url)
        messages.error(request, "E-mail ou senha incorretos.")
    return render(request, "intra/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


def _gerar_senha():
    """Senha aleatória legível (12 caracteres, sem ambíguos 0/O, 1/l)."""
    return get_random_string(12, "abcdefghjkmnpqrstuvwxyz23456789ABCDEFGHJKMNPQRSTUVWXYZ")


def esqueceu_acesso_view(request):
    form = EsqueceuAcessoForm(request.POST or None)
    enviado = False
    if form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        nova_senha = _gerar_senha()
        login_url = request.build_absolute_uri(reverse("intra:login"))

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            user = None

        if user and _is_gestor(user):
            messages.info(
                request,
                "Para usuários Gestor Administrativo, a senha é definida exclusivamente no painel administrativo. Entre em contato com o administrador.",
            )
        elif user is None:
            # Primeiro acesso: cria usuário e envia senha
            username = email[:150] if len(email) > 150 else email
            user = User.objects.create_user(
                username=username,
                email=email,
                password=nova_senha,
            )
            PerfilSolicitante.objects.get_or_create(user=user)
            send_mail(
                subject="Acesso à Intranet Parceiros - Senha de acesso",
                message=(
                    f"Olá,\n\n"
                    f"Seu acesso à Intranet Parceiros foi criado.\n\n"
                    f"E-mail: {email}\n"
                    f"Senha: {nova_senha}\n\n"
                    f"Faça login em: {login_url}\n\n"
                    f"Recomendamos alterar sua senha após o primeiro acesso."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            enviado = True
        else:
            # Usuário já existe: altera senha e envia a nova
            user.set_password(nova_senha)
            user.save(update_fields=["password"])
            send_mail(
                subject="Intranet Parceiros - Nova senha de acesso",
                message=(
                    f"Olá,\n\n"
                    f"Você já estava cadastrado na Intranet Parceiros. "
                    f"Sua senha foi alterada conforme solicitado.\n\n"
                    f"E-mail: {email}\n"
                    f"Nova senha: {nova_senha}\n\n"
                    f"Faça login em: {login_url}\n\n"
                    f"Se não foi você quem solicitou, entre em contato com o suporte."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            enviado = True

    return render(
        request,
        "intra/esqueceu_acesso.html",
        {"form": form, "enviado": enviado},
    )


class RedefinirSenhaConfirmView(PasswordResetConfirmView):
    template_name = "intra/redefinir_senha_confirm.html"
    success_url = "/login/"
    post_reset_login = False

    def form_valid(self, form):
        result = super().form_valid(form)
        messages.success(self.request, "Senha redefinida. Faça login com sua nova senha.")
        return result


@login_required
def completar_cadastro_view(request):
    """Primeiro acesso: obriga preenchimento de nome e dados de pagamento."""
    perfil, _ = PerfilSolicitante.objects.get_or_create(user=request.user)
    form = CompletarCadastroForm(request.POST or None, instance=perfil)
    if form.is_valid():
        form.save()
        messages.success(request, "Cadastro concluído. Você já pode usar a intranet.")
        next_url = request.POST.get("next") or request.GET.get("next") or reverse("intra:home")
        return redirect(next_url)
    return render(request, "intra/completar_cadastro.html", {"form": form})


@login_required
def home(request):
    """Página inicial com sidebar."""
    return render(request, "intra/home.html")


@login_required
def meu_perfil(request):
    """Visualização somente leitura das informações do perfil do usuário."""
    perfil, _ = PerfilSolicitante.objects.get_or_create(user=request.user)
    return render(request, "intra/meu_perfil.html", {"perfil": perfil})


@login_required
def meus_reembolsos(request):
    """Lista de solicitações de reembolso do usuário."""
    solicitacoes = SolicitacaoReembolso.objects.filter(user=request.user)
    return render(
        request,
        "intra/meus_reembolsos.html",
        {"solicitacoes": solicitacoes},
    )


@login_required
def reembolso_pdf(request, pk):
    """Gera PDF da solicitação de reembolso (solicitante ou gestor)."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if sol.user_id != request.user.id and not _is_gestor(request.user):
        return HttpResponse("Não autorizado.", status=403)
    pdf_bytes = gerar_pdf(sol)
    nome_arquivo = f"reembolso_{sol.pk}_{sol.criado_em.strftime('%Y%m%d')}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{nome_arquivo}"'
    return response


@login_required
def reembolso_detalhe_json(request, pk):
    """Retorna dados da solicitação de reembolso em JSON (para popup de visualização)."""
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if sol.user_id != request.user.id:
        return JsonResponse({"error": "Não autorizado."}, status=403)
    tipos_labels = dict(TIPOS_DESPESA)
    itens = [
        {
            "tipo_despesa": tipos_labels.get(item.tipo_despesa, item.tipo_despesa),
            "descricao": item.descricao or "",
            "valor": float(item.valor),
            "km": float(item.km) if item.km is not None else None,
        }
        for item in sol.itens.all()
    ]
    return JsonResponse({
        "pk": sol.pk,
        "centro_custo": sol.centro_custo,
        "cod_despesa": sol.cod_despesa,
        "valor_total": float(sol.valor_total),
        "criado_em": sol.criado_em.strftime("%d/%m/%Y %H:%M") if sol.criado_em else "",
        "itens": itens,
    })


@login_required
def aprovar_reembolsos(request):
    """Lista solicitações de reembolso para o gestor aprovar ou rejeitar."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    solicitacoes = SolicitacaoReembolso.objects.select_related("user").all()
    return render(
        request,
        "intra/aprovar_reembolsos.html",
        {"solicitacoes": solicitacoes},
    )


@login_required
def reembolso_decidir(request, pk):
    """Aprova ou rejeita uma solicitação (somente gestor)."""
    if not _is_gestor(request.user):
        return HttpResponse("Acesso restrito a Gestores Administrativos.", status=403)
    sol = get_object_or_404(SolicitacaoReembolso, pk=pk)
    if sol.status != SolicitacaoReembolso.STATUS_PENDENTE:
        messages.warning(request, "Esta solicitação já foi processada.")
        return redirect("intra:aprovar_reembolsos")
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "aprovar":
            sol.status = SolicitacaoReembolso.STATUS_APROVADO
            sol.aprovado_por = request.user
            sol.aprovado_em = timezone.now()
            sol.motivo_rejeicao = ""
            sol.save()
            messages.success(request, "Solicitação aprovada.")
        elif acao == "rejeitar":
            sol.status = SolicitacaoReembolso.STATUS_REJEITADO
            sol.aprovado_por = request.user
            sol.aprovado_em = timezone.now()
            sol.motivo_rejeicao = (request.POST.get("motivo_rejeicao") or "").strip()[:500]
            sol.save()
            messages.success(request, "Solicitação rejeitada.")
        return redirect("intra:aprovar_reembolsos")
    return redirect("intra:aprovar_reembolsos")


@login_required
def reembolso(request):
    """Página de solicitação de reembolso com formulário."""
    context = {
        "centros_custo": CENTROS_CUSTO,
        "codigos_despesa": CODIGOS_DESPESA,
        "tipos_despesa": TIPOS_DESPESA,
        "tipos_despesa_json": json.dumps(TIPOS_DESPESA),
        "success": False,
    }
    if request.method == "POST":
        centro_custo = request.POST.get("centro_custo", "").strip()
        cod_despesa = request.POST.get("cod_despesa", "").strip()
        valor_total = 0
        itens_dados = []
        for key, value in request.POST.items():
            if key.startswith("valor_") and value:
                try:
                    idx = key.replace("valor_", "")
                    v = float(str(value).replace(",", "."))
                    valor_total += v
                    tipo = request.POST.get(f"tipo_despesa_{idx}", "").strip()
                    desc = request.POST.get(f"descricao_{idx}", "").strip()
                    itens_dados.append({"tipo_despesa": tipo, "descricao": desc, "valor": v})
                except (ValueError, TypeError):
                    pass
        if centro_custo and cod_despesa:
            sol = SolicitacaoReembolso.objects.create(
                user=request.user,
                centro_custo=centro_custo,
                cod_despesa=cod_despesa,
                valor_total=valor_total,
            )
            for item in itens_dados:
                if item["tipo_despesa"]:
                    ItemReembolso.objects.create(
                        solicitacao=sol,
                        tipo_despesa=item["tipo_despesa"],
                        descricao=item["descricao"],
                        valor=item["valor"],
                    )
        context["success"] = True
    return render(request, "intra/reembolso.html", context)
