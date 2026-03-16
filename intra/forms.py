from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
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
    banco_pix_select = forms.CharField(
        label="Banco",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Selecione ou digite o banco...",
            "autocomplete": "off",
            "class": "form-control"
        }),
    )
    banco_transf_select = forms.CharField(
        label="Banco",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Selecione ou digite o banco...",
            "autocomplete": "off",
            "class": "form-control"
        }),
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Definir valores iniciais dos campos de seleção de banco
        if self.instance:
            if self.instance.banco_pix:
                self.initial['banco_pix_select'] = self.instance.banco_pix
            if self.instance.banco:
                self.initial['banco_transf_select'] = self.instance.banco

    class Meta:
        model = PerfilSolicitante
        fields = [
            "nome_solicitante",
            "forma_pagamento",
            "chave_pix",
            "banco_pix",
            "cpf_pix",
            "banco",
            "agencia",
            "conta_tipo",
            "conta_numero",
            "cpf_transferencia",
        ]
        widgets = {
            "nome_solicitante": forms.TextInput(attrs={"placeholder": "Nome completo"}),
            "forma_pagamento": forms.Select(attrs={"class": "form-control"}),
            "chave_pix": forms.TextInput(attrs={"placeholder": "CPF, e-mail, telefone ou chave aleatória"}),
            "banco_pix": forms.HiddenInput(),
            "cpf_pix": forms.TextInput(attrs={"placeholder": "CPF ou CNPJ"}),
            "banco": forms.HiddenInput(),
            "agencia": forms.TextInput(attrs={"placeholder": "0000", "maxlength": "4"}),
            "conta_numero": forms.TextInput(attrs={"placeholder": "00000-0", "maxlength": "8"}),
            "cpf_transferencia": forms.TextInput(attrs={"placeholder": "CPF ou CNPJ"}),
        }
        labels = {
            "nome_solicitante": "Nome do solicitante",
            "forma_pagamento": "Forma de Pagamento",
            "chave_pix": "Chave PIX",
            "banco_pix": "Banco",
            "cpf_pix": "CPF/CNPJ",
            "banco": "Banco",
            "agencia": "Agência",
            "conta_tipo": "Tipo de conta",
            "conta_numero": "Conta Corrente ou Poupança",
            "cpf_transferencia": "CPF/CNPJ",
        }

    def clean_nome_solicitante(self):
        nome = self.cleaned_data.get("nome_solicitante", "").strip()
        if not nome:
            raise forms.ValidationError("Este campo é obrigatório.")
        return nome

    def clean_forma_pagamento(self):
        forma = self.cleaned_data.get("forma_pagamento", "").strip()
        if not forma:
            raise forms.ValidationError("Este campo é obrigatório.")
        return forma

    def clean_chave_pix(self):
        chave_pix = self.cleaned_data.get("chave_pix", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "PIX" and not chave_pix:
            raise forms.ValidationError("Este campo é obrigatório.")
        return chave_pix

    def clean_banco_pix(self):
        banco_pix = self.cleaned_data.get("banco_pix", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "PIX" and not banco_pix:
            raise forms.ValidationError("Este campo é obrigatório.")
        return banco_pix

    def clean_cpf_pix(self):
        cpf_pix = self.cleaned_data.get("cpf_pix", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "PIX" and not cpf_pix:
            raise forms.ValidationError("Este campo é obrigatório.")
        return cpf_pix

    def clean_banco(self):
        banco = self.cleaned_data.get("banco", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not banco:
            raise forms.ValidationError("Este campo é obrigatório.")
        return banco

    def clean_agencia(self):
        agencia = self.cleaned_data.get("agencia", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not agencia:
            raise forms.ValidationError("Este campo é obrigatório.")
        return agencia

    def clean_conta_tipo(self):
        conta_tipo = self.cleaned_data.get("conta_tipo", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not conta_tipo:
            raise forms.ValidationError("Este campo é obrigatório.")
        return conta_tipo

    def clean_conta_numero(self):
        conta_numero = self.cleaned_data.get("conta_numero", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not conta_numero:
            raise forms.ValidationError("Este campo é obrigatório.")
        return conta_numero

    def clean_cpf_transferencia(self):
        cpf_transferencia = self.cleaned_data.get("cpf_transferencia", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not cpf_transferencia:
            raise forms.ValidationError("Este campo é obrigatório.")
        return cpf_transferencia

    def clean_banco_pix_select(self):
        banco_pix_select = self.cleaned_data.get("banco_pix_select", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "PIX" and not banco_pix_select:
            raise forms.ValidationError("Este campo é obrigatório.")
        return banco_pix_select

    def clean_banco_transf_select(self):
        banco_transf_select = self.cleaned_data.get("banco_transf_select", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not banco_transf_select:
            raise forms.ValidationError("Este campo é obrigatório.")
        return banco_transf_select

    def clean(self):
        cleaned_data = super().clean()
        forma_pagamento = cleaned_data.get("forma_pagamento", "")
        
        # Transferir valores dos campos de seleção para os campos hidden
        if forma_pagamento == "PIX":
            banco_pix_select = cleaned_data.get("banco_pix_select", "").strip()
            if banco_pix_select:
                cleaned_data["banco_pix"] = banco_pix_select
        elif forma_pagamento == "TRANSFERENCIA":
            banco_transf_select = cleaned_data.get("banco_transf_select", "").strip()
            if banco_transf_select:
                cleaned_data["banco"] = banco_transf_select
        
        return cleaned_data


class EditarPagamentoForm(forms.ModelForm):
    """Formulário para editar apenas dados de pagamento no perfil."""
    
    BANCOS_CHOICES = [
        ('', 'Selecione...'),
        ('Banco do Brasil', 'Banco do Brasil'),
        ('Bradesco', 'Bradesco'),
        ('Itaú', 'Itaú'),
        ('Santander', 'Santander'),
        ('Caixa Econômica Federal', 'Caixa Econômica Federal'),
        ('Banco Inter', 'Banco Inter'),
        ('Nubank', 'Nubank'),
        ('Banco Original', 'Banco Original'),
        ('Banrisul', 'Banrisul'),
        ('Banco Safra', 'Banco Safra'),
        ('BTG Pactual', 'BTG Pactual'),
        ('Banco Pan', 'Banco Pan'),
        ('Banco Votorantim', 'Banco Votorantim'),
        ('Banco C6', 'Banco C6'),
        ('Banco Next', 'Banco Next'),
        ('Banco Neon', 'Banco Neon'),
        ('Banco Digio', 'Banco Digio'),
        ('Banco Will', 'Banco Will'),
        ('Banco Sofisa', 'Banco Sofisa'),
        ('Banco Rendimento', 'Banco Rendimento'),
    ]
    
    banco_pix_select = forms.ChoiceField(
        label="Banco",
        choices=BANCOS_CHOICES,
        required=False,
        widget=forms.Select(attrs={
            "class": "form-control"
        }),
    )
    banco_transf_select = forms.ChoiceField(
        label="Banco",
        choices=BANCOS_CHOICES,
        required=False,
        widget=forms.Select(attrs={
            "class": "form-control"
        }),
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Definir valores iniciais dos campos de seleção de banco
        if self.instance:
            if self.instance.banco_pix:
                self.initial['banco_pix_select'] = self.instance.banco_pix
            if self.instance.banco:
                self.initial['banco_transf_select'] = self.instance.banco

    class Meta:
        model = PerfilSolicitante
        fields = [
            "forma_pagamento",
            "chave_pix",
            "banco_pix",
            "cpf_pix",
            "banco",
            "agencia",
            "conta_tipo",
            "conta_numero",
            "cpf_transferencia",
        ]
        widgets = {
            "forma_pagamento": forms.Select(attrs={"class": "form-control"}),
            "chave_pix": forms.TextInput(attrs={"placeholder": "CPF, e-mail, telefone ou chave aleatória"}),
            "banco_pix": forms.HiddenInput(),
            "cpf_pix": forms.TextInput(attrs={"placeholder": "CPF ou CNPJ"}),
            "banco": forms.HiddenInput(),
            "agencia": forms.TextInput(attrs={"placeholder": "0000", "maxlength": "4"}),
            "conta_numero": forms.TextInput(attrs={"placeholder": "00000-0", "maxlength": "8"}),
            "cpf_transferencia": forms.TextInput(attrs={"placeholder": "CPF ou CNPJ"}),
        }
        labels = {
            "forma_pagamento": "Forma de Pagamento",
            "chave_pix": "Chave PIX",
            "banco_pix": "Banco",
            "cpf_pix": "CPF/CNPJ",
            "banco": "Banco",
            "agencia": "Agência",
            "conta_tipo": "Tipo de conta",
            "conta_numero": "Conta Corrente ou Poupança",
            "cpf_transferencia": "CPF/CNPJ",
        }

    def clean_forma_pagamento(self):
        forma = self.cleaned_data.get("forma_pagamento", "").strip()
        if not forma:
            raise forms.ValidationError("Este campo é obrigatório.")
        return forma

    def clean_chave_pix(self):
        chave_pix = self.cleaned_data.get("chave_pix", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "PIX" and not chave_pix:
            raise forms.ValidationError("Este campo é obrigatório.")
        return chave_pix

    def clean_banco_pix(self):
        banco_pix = self.cleaned_data.get("banco_pix", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "PIX" and not banco_pix:
            raise forms.ValidationError("Este campo é obrigatório.")
        return banco_pix

    def clean_cpf_pix(self):
        cpf_pix = self.cleaned_data.get("cpf_pix", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "PIX" and not cpf_pix:
            raise forms.ValidationError("Este campo é obrigatório.")
        return cpf_pix

    def clean_banco(self):
        banco = self.cleaned_data.get("banco", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not banco:
            raise forms.ValidationError("Este campo é obrigatório.")
        return banco

    def clean_agencia(self):
        agencia = self.cleaned_data.get("agencia", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not agencia:
            raise forms.ValidationError("Este campo é obrigatório.")
        return agencia

    def clean_conta_tipo(self):
        conta_tipo = self.cleaned_data.get("conta_tipo", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not conta_tipo:
            raise forms.ValidationError("Este campo é obrigatório.")
        return conta_tipo

    def clean_conta_numero(self):
        conta_numero = self.cleaned_data.get("conta_numero", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not conta_numero:
            raise forms.ValidationError("Este campo é obrigatório.")
        return conta_numero

    def clean_cpf_transferencia(self):
        cpf_transferencia = self.cleaned_data.get("cpf_transferencia", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not cpf_transferencia:
            raise forms.ValidationError("Este campo é obrigatório.")
        return cpf_transferencia

    def clean_banco_pix_select(self):
        banco_pix_select = self.cleaned_data.get("banco_pix_select", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "PIX" and not banco_pix_select:
            raise forms.ValidationError("Este campo é obrigatório.")
        return banco_pix_select

    def clean_banco_transf_select(self):
        banco_transf_select = self.cleaned_data.get("banco_transf_select", "").strip()
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        if forma_pagamento == "TRANSFERENCIA" and not banco_transf_select:
            raise forms.ValidationError("Este campo é obrigatório.")
        return banco_transf_select

    def clean(self):
        cleaned_data = super().clean()
        forma_pagamento = cleaned_data.get("forma_pagamento", "")
        
        # Transferir valores dos campos de seleção para os campos hidden
        if forma_pagamento == "PIX":
            banco_pix_select = cleaned_data.get("banco_pix_select", "").strip()
            if banco_pix_select:
                cleaned_data["banco_pix"] = banco_pix_select
        elif forma_pagamento == "TRANSFERENCIA":
            banco_transf_select = cleaned_data.get("banco_transf_select", "").strip()
            if banco_transf_select:
                cleaned_data["banco"] = banco_transf_select
        
        return cleaned_data


class TrocarSenhaForm(forms.Form):
    """Formulário para trocar a senha do usuário."""
    
    senha_atual = forms.CharField(
        label="Senha Atual",
        widget=forms.PasswordInput(attrs={
            "placeholder": "Digite sua senha atual",
            "class": "form-control"
        }),
        required=True
    )
    
    nova_senha = forms.CharField(
        label="Nova Senha",
        widget=forms.PasswordInput(attrs={
            "placeholder": "Digite sua nova senha",
            "class": "form-control"
        }),
        required=True,
        min_length=8
    )
    
    confirmar_senha = forms.CharField(
        label="Confirmar Nova Senha",
        widget=forms.PasswordInput(attrs={
            "placeholder": "Confirme sua nova senha",
            "class": "form-control"
        }),
        required=True
    )
    
    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
    
    def clean_senha_atual(self):
        senha_atual = self.cleaned_data.get("senha_atual")
        if not self.user.check_password(senha_atual):
            raise forms.ValidationError("A senha atual está incorreta.")
        return senha_atual
    
    def clean_nova_senha(self):
        nova_senha = self.cleaned_data.get("nova_senha")
        if nova_senha:
            try:
                validate_password(nova_senha, self.user)
            except ValidationError as e:
                raise forms.ValidationError(e.messages)
        return nova_senha
    
    def clean(self):
        cleaned_data = super().clean()
        nova_senha = cleaned_data.get("nova_senha")
        confirmar_senha = cleaned_data.get("confirmar_senha")
        
        if nova_senha and confirmar_senha:
            if nova_senha != confirmar_senha:
                raise forms.ValidationError("As senhas não coincidem.")
        
        return cleaned_data
