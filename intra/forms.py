from django import forms
from django.contrib.auth import get_user_model
from .models import PerfilSolicitante, RegraUsuario

User = get_user_model()


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
    gestor_busca = forms.CharField(
        label="Gestor",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Digite o nome ou e-mail do gestor...",
            "autocomplete": "off",
            "class": "form-control"
        }),
    )
    nome_gestor = forms.CharField(
        max_length=200,
        required=True,
        widget=forms.HiddenInput(),
    )
    email_gestor = forms.EmailField(
        max_length=200,
        required=True,
        widget=forms.HiddenInput(),
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Se já existe um gestor selecionado, definir o valor inicial do campo de busca
        if self.instance and self.instance.nome_gestor and self.instance.email_gestor:
            self.initial['gestor_busca'] = f"{self.instance.nome_gestor} ({self.instance.email_gestor})"

    class Meta:
        model = PerfilSolicitante
        fields = [
            "nome_solicitante",
            "banco",
            "agencia",
            "conta_tipo",
            "conta_numero",
            "chave_pix",
            "nome_gestor",
            "email_gestor",
        ]
        widgets = {
            "nome_solicitante": forms.TextInput(attrs={"placeholder": "Nome completo"}),
            "banco": forms.TextInput(attrs={
                "placeholder": "Digite o nome do banco...",
                "autocomplete": "off",
                "class": "form-control"
            }),
            "agencia": forms.TextInput(attrs={"placeholder": "0000", "maxlength": "4"}),
            "conta_numero": forms.TextInput(attrs={"placeholder": "00000-0", "maxlength": "8"}),
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

    DOMINIO_PERMITIDO = "@parceirosedu.org.br"

    def clean_email_gestor(self):
        email = self.cleaned_data.get("email_gestor", "").strip().lower()
        if email and not email.endswith(self.DOMINIO_PERMITIDO.lower()):
            raise forms.ValidationError(
                f"O e-mail deve ser do domínio {self.DOMINIO_PERMITIDO}"
            )
        return email

    def clean_nome_solicitante(self):
        nome = self.cleaned_data.get("nome_solicitante", "").strip()
        if not nome:
            raise forms.ValidationError("Este campo é obrigatório.")
        return nome

    def clean_banco(self):
        banco = self.cleaned_data.get("banco", "").strip()
        if not banco:
            raise forms.ValidationError("Este campo é obrigatório.")
        return banco

    def clean_agencia(self):
        agencia = self.cleaned_data.get("agencia", "").strip()
        if not agencia:
            raise forms.ValidationError("Este campo é obrigatório.")
        return agencia

    def clean_conta_tipo(self):
        conta_tipo = self.cleaned_data.get("conta_tipo", "").strip()
        if not conta_tipo:
            raise forms.ValidationError("Este campo é obrigatório.")
        return conta_tipo

    def clean_conta_numero(self):
        conta_numero = self.cleaned_data.get("conta_numero", "").strip()
        if not conta_numero:
            raise forms.ValidationError("Este campo é obrigatório.")
        return conta_numero

    def clean_chave_pix(self):
        chave_pix = self.cleaned_data.get("chave_pix", "").strip()
        if not chave_pix:
            raise forms.ValidationError("Este campo é obrigatório.")
        return chave_pix

    def clean(self):
        cleaned_data = super().clean()
        nome_gestor = cleaned_data.get("nome_gestor", "").strip()
        email_gestor = cleaned_data.get("email_gestor", "").strip()
        gestor_busca = cleaned_data.get("gestor_busca", "").strip()
        
        if not nome_gestor or not email_gestor:
            if gestor_busca:
                raise forms.ValidationError("Por favor, selecione um gestor da lista suspensa.")
            else:
                raise forms.ValidationError("Por favor, selecione um gestor da lista.")
        
        return cleaned_data
