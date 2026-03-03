from django import forms
from .models import PerfilSolicitante


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={"placeholder": "seu@email.com", "autofocus": True}),
    )
    password = forms.CharField(
        label="Senha",
        widget=forms.PasswordInput(attrs={"placeholder": "••••••••"}),
    )


class EsqueceuAcessoForm(forms.Form):
    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={"placeholder": "seu@email.com", "autofocus": True}),
    )

    DOMINIO_PERMITIDO = "@parceirosedu.org.br"

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and not email.endswith(self.DOMINIO_PERMITIDO.lower()):
            raise forms.ValidationError(
                "Sistema de uso interno da Parceiros da Educação."
            )
        return email


class CompletarCadastroForm(forms.ModelForm):
    class Meta:
        model = PerfilSolicitante
        fields = [
            "nome_solicitante",
            "banco",
            "agencia",
            "conta_tipo",
            "conta_numero",
            "chave_pix",
        ]
        widgets = {
            "nome_solicitante": forms.TextInput(attrs={"placeholder": "Nome completo"}),
            "banco": forms.TextInput(attrs={"placeholder": "Ex: Banco do Brasil"}),
            "agencia": forms.TextInput(attrs={"placeholder": "Número da agência"}),
            "conta_numero": forms.TextInput(attrs={"placeholder": "Conta corrente ou poupança"}),
            "chave_pix": forms.TextInput(attrs={"placeholder": "CPF, e-mail, telefone ou chave aleatória"}),
        }
        labels = {
            "nome_solicitante": "Nome do solicitante",
            "banco": "Banco",
            "agencia": "Agência",
            "conta_tipo": "Tipo de conta",
            "conta_numero": "Conta Corrente ou Poupança",
            "chave_pix": "Chave PIX",
        }
