from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import PerfilSolicitante, RegraUsuario

User = get_user_model()


def _validar_documento_por_tipo(valor, tipo_pessoa):
    """Valida CPF (PF) ou CNPJ (PJ) conforme o tipo de pessoa."""
    numeros = "".join(filter(str.isdigit, valor or ""))
    if tipo_pessoa == "PF":
        if len(numeros) != 11:
            raise forms.ValidationError("CPF deve ter 11 dígitos.")
    elif tipo_pessoa == "PJ":
        if len(numeros) != 14:
            raise forms.ValidationError("CNPJ deve ter 14 dígitos.")
    else:
        if len(numeros) != 11 and len(numeros) != 14:
            raise forms.ValidationError("CPF deve ter 11 dígitos ou CNPJ deve ter 14 dígitos.")
    return valor


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


class CompletarCadastroForm(forms.ModelForm):
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
        ('Carteira Digital', 'Carteira Digital'),
        ('Outro', 'Outro'),
    ]

    banco_pix_select = forms.ChoiceField(
        label="Banco",
        choices=BANCOS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    banco_transf_select = forms.ChoiceField(
        label="Banco",
        choices=BANCOS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Definir valores iniciais dos campos de seleção de banco
        if self.instance:
            if self.instance.banco_pix:
                banco_pix_value = self.instance.banco_pix
                if banco_pix_value and banco_pix_value in [choice[0] for choice in self.BANCOS_CHOICES]:
                    self.initial['banco_pix_select'] = banco_pix_value
                elif banco_pix_value:
                    self.initial['banco_pix_select'] = 'Outro'
            if self.instance.banco:
                banco_value = self.instance.banco
                if banco_value and banco_value in [choice[0] for choice in self.BANCOS_CHOICES]:
                    self.initial['banco_transf_select'] = banco_value
                elif banco_value:
                    self.initial['banco_transf_select'] = 'Outro'

    class Meta:
        model = PerfilSolicitante
        fields = [
            "nome_solicitante",
            "tipo_pessoa",
            "cnpj",
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
            "nome_solicitante": forms.HiddenInput(),
            "tipo_pessoa": forms.Select(attrs={"class": "form-control"}),
            "cnpj": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "00.000.000/0000-00",
            }),
            "forma_pagamento": forms.Select(attrs={"class": "form-control"}),
            "chave_pix": forms.TextInput(attrs={"placeholder": "CPF, e-mail, telefone ou chave aleatória"}),
            "banco_pix": forms.HiddenInput(),
            "cpf_pix": forms.TextInput(attrs={"placeholder": "CPF ou CNPJ"}),
            "banco": forms.HiddenInput(),
            "agencia": forms.TextInput(attrs={"placeholder": "0000", "maxlength": "4"}),
            "conta_numero": forms.TextInput(attrs={"placeholder": "000000", "maxlength": "8"}),
            "cpf_transferencia": forms.TextInput(attrs={"placeholder": "CPF ou CNPJ"}),
        }
        labels = {
            "nome_solicitante": "Nome / Razão social",
            "tipo_pessoa": "Tipo de pessoa",
            "cnpj": "CNPJ",
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
        tipo_pessoa = self.data.get("tipo_pessoa", "").strip()
        if not nome:
            if tipo_pessoa == "PJ":
                raise forms.ValidationError("Informe a razão social.")
            raise forms.ValidationError("Este campo é obrigatório.")
        return nome

    def clean_tipo_pessoa(self):
        tipo = self.cleaned_data.get("tipo_pessoa", "").strip()
        if not tipo:
            raise forms.ValidationError("Este campo é obrigatório.")
        return tipo

    def clean_cnpj(self):
        cnpj = self.cleaned_data.get("cnpj", "").strip()
        tipo_pessoa = self.data.get("tipo_pessoa", "").strip()
        if tipo_pessoa == "PJ":
            if not cnpj:
                raise forms.ValidationError("Informe o CNPJ da empresa.")
            _validar_documento_por_tipo(cnpj, "PJ")
        elif tipo_pessoa == "PF":
            cnpj = ""
        return cnpj

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
        if forma_pagamento == "PIX":
            if not cpf_pix:
                raise forms.ValidationError("Este campo é obrigatório.")
            # Pagamento aceita CPF ou CNPJ, independente do tipo de pessoa
            _validar_documento_por_tipo(cpf_pix, "")
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
        if forma_pagamento == "TRANSFERENCIA":
            if not cpf_transferencia:
                raise forms.ValidationError("Este campo é obrigatório.")
            # Pagamento aceita CPF ou CNPJ, independente do tipo de pessoa
            _validar_documento_por_tipo(cpf_transferencia, "")
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
            if banco_pix_select == "Outro":
                banco_pix_value = self.data.get("banco_pix", "").strip()
                if banco_pix_value:
                    cleaned_data["banco_pix"] = banco_pix_value
                else:
                    cleaned_data["banco_pix"] = banco_pix_select
            else:
                cleaned_data["banco_pix"] = banco_pix_select
            cleaned_data["banco"] = ""
            cleaned_data["agencia"] = ""
            cleaned_data["conta_tipo"] = ""
            cleaned_data["conta_numero"] = ""
            cleaned_data["cpf_transferencia"] = ""
        elif forma_pagamento == "TRANSFERENCIA":
            banco_transf_select = cleaned_data.get("banco_transf_select", "").strip()
            if banco_transf_select == "Outro":
                banco_value = self.data.get("banco", "").strip()
                if banco_value:
                    cleaned_data["banco"] = banco_value
                else:
                    cleaned_data["banco"] = banco_transf_select
            else:
                cleaned_data["banco"] = banco_transf_select
            cleaned_data["banco_pix"] = ""
            cleaned_data["chave_pix"] = ""
            cleaned_data["cpf_pix"] = ""
        
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        tipo_pessoa = self.cleaned_data.get("tipo_pessoa", "")

        if tipo_pessoa:
            instance.tipo_pessoa = tipo_pessoa

        if tipo_pessoa == "PJ":
            instance.cnpj = self.cleaned_data.get("cnpj", "").strip()
        else:
            instance.cnpj = ""

        if forma_pagamento:
            instance.forma_pagamento = forma_pagamento

        if forma_pagamento == "PIX":
            instance.banco_pix = self.cleaned_data.get("banco_pix", "").strip()
            instance.chave_pix = self.cleaned_data.get("chave_pix", "").strip()
            instance.cpf_pix = self.cleaned_data.get("cpf_pix", "").strip()
            instance.banco = ""
            instance.agencia = ""
            instance.conta_tipo = ""
            instance.conta_numero = ""
            instance.cpf_transferencia = ""
        elif forma_pagamento == "TRANSFERENCIA":
            instance.banco = self.cleaned_data.get("banco", "").strip()
            banco_selecionado = instance.banco

            if banco_selecionado == "Carteira Digital":
                carteira_digital = self.data.get("carteira_digital", "").strip()
                if carteira_digital:
                    instance.conta_numero = carteira_digital
                instance.agencia = ""
                instance.conta_tipo = ""
            else:
                instance.agencia = self.cleaned_data.get("agencia", "").strip()
                instance.conta_tipo = self.cleaned_data.get("conta_tipo", "").strip()
                instance.conta_numero = self.cleaned_data.get("conta_numero", "").strip()

            instance.cpf_transferencia = self.cleaned_data.get("cpf_transferencia", "").strip()
            instance.banco_pix = ""
            instance.chave_pix = ""
            instance.cpf_pix = ""

        if commit:
            instance.save()
        return instance


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
        ('Carteira Digital', 'Carteira Digital'),
        ('Outro', 'Outro'),
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
                banco_pix_value = self.instance.banco_pix
                # Verificar se o valor está nas choices
                if banco_pix_value and banco_pix_value in [choice[0] for choice in self.BANCOS_CHOICES]:
                    self.initial['banco_pix_select'] = banco_pix_value
                elif banco_pix_value:
                    # Se não estiver nas choices, usar "Outro"
                    self.initial['banco_pix_select'] = 'Outro'
            if self.instance.banco:
                banco_value = self.instance.banco
                # Verificar se o valor está nas choices
                if banco_value and banco_value in [choice[0] for choice in self.BANCOS_CHOICES]:
                    self.initial['banco_transf_select'] = banco_value
                elif banco_value:
                    # Se não estiver nas choices, usar "Outro"
                    self.initial['banco_transf_select'] = 'Outro'

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
            "conta_numero": forms.TextInput(attrs={"placeholder": "000000", "maxlength": "8"}),
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
        if forma_pagamento == "PIX":
            if not cpf_pix:
                raise forms.ValidationError("Este campo é obrigatório.")
            # Pagamento aceita CPF ou CNPJ, independente do tipo de pessoa
            _validar_documento_por_tipo(cpf_pix, "")
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
        if forma_pagamento == "TRANSFERENCIA":
            if not cpf_transferencia:
                raise forms.ValidationError("Este campo é obrigatório.")
            # Pagamento aceita CPF ou CNPJ, independente do tipo de pessoa
            _validar_documento_por_tipo(cpf_transferencia, "")
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

        if forma_pagamento == "PIX":
            banco_pix_select = cleaned_data.get("banco_pix_select", "").strip()
            if banco_pix_select == "Outro":
                banco_pix_value = self.data.get("banco_pix", "").strip()
                if banco_pix_value:
                    cleaned_data["banco_pix"] = banco_pix_value
                else:
                    cleaned_data["banco_pix"] = banco_pix_select
            else:
                cleaned_data["banco_pix"] = banco_pix_select
            cleaned_data["banco"] = ""
            cleaned_data["agencia"] = ""
            cleaned_data["conta_tipo"] = ""
            cleaned_data["conta_numero"] = ""
            cleaned_data["cpf_transferencia"] = ""
        elif forma_pagamento == "TRANSFERENCIA":
            banco_transf_select = cleaned_data.get("banco_transf_select", "").strip()
            if banco_transf_select == "Outro":
                banco_value = self.data.get("banco", "").strip()
                if banco_value:
                    cleaned_data["banco"] = banco_value
                else:
                    cleaned_data["banco"] = banco_transf_select
            else:
                cleaned_data["banco"] = banco_transf_select
            cleaned_data["banco_pix"] = ""
            cleaned_data["chave_pix"] = ""
            cleaned_data["cpf_pix"] = ""

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")

        if forma_pagamento:
            instance.forma_pagamento = forma_pagamento

        if forma_pagamento == "PIX":
            instance.banco_pix = self.cleaned_data.get("banco_pix", "").strip()
            instance.chave_pix = self.cleaned_data.get("chave_pix", "").strip()
            instance.cpf_pix = self.cleaned_data.get("cpf_pix", "").strip()
            instance.banco = ""
            instance.agencia = ""
            instance.conta_tipo = ""
            instance.conta_numero = ""
            instance.cpf_transferencia = ""
        elif forma_pagamento == "TRANSFERENCIA":
            instance.banco = self.cleaned_data.get("banco", "").strip()
            banco_selecionado = instance.banco

            if banco_selecionado == "Carteira Digital":
                carteira_digital = self.data.get("carteira_digital", "").strip()
                if carteira_digital:
                    instance.conta_numero = carteira_digital
                instance.agencia = ""
                instance.conta_tipo = ""
            else:
                instance.agencia = self.cleaned_data.get("agencia", "").strip()
                instance.conta_tipo = self.cleaned_data.get("conta_tipo", "").strip()
                instance.conta_numero = self.cleaned_data.get("conta_numero", "").strip()

            instance.cpf_transferencia = self.cleaned_data.get("cpf_transferencia", "").strip()
            instance.banco_pix = ""
            instance.chave_pix = ""
            instance.cpf_pix = ""

        if commit:
            instance.save()
        return instance


class EditarDadosAcessoForm(forms.ModelForm):
    """Formulário para editar tipo de pessoa, nome/razão social e CNPJ no perfil."""

    class Meta:
        model = PerfilSolicitante
        fields = ["tipo_pessoa", "nome_solicitante", "cnpj"]
        widgets = {
            "tipo_pessoa": forms.Select(attrs={"class": "form-control"}),
            "nome_solicitante": forms.HiddenInput(),
            "cnpj": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "00.000.000/0000-00",
            }),
        }
        labels = {
            "tipo_pessoa": "Tipo de pessoa",
            "nome_solicitante": "Nome / Razão social",
            "cnpj": "CNPJ",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Cadastros antigos sem tipo são Pessoa Física
        if self.instance and not (getattr(self.instance, "tipo_pessoa", "") or "").strip():
            self.initial["tipo_pessoa"] = "PF"
            self.fields["tipo_pessoa"].initial = "PF"

    def clean_tipo_pessoa(self):
        tipo = self.cleaned_data.get("tipo_pessoa", "").strip()
        if not tipo:
            raise forms.ValidationError("Este campo é obrigatório.")
        return tipo

    def clean_nome_solicitante(self):
        nome = self.cleaned_data.get("nome_solicitante", "").strip()
        tipo_pessoa = self.data.get("tipo_pessoa", "").strip()
        if not nome:
            if tipo_pessoa == "PJ":
                raise forms.ValidationError("Informe a razão social.")
            raise forms.ValidationError("Informe o nome completo.")
        return nome

    def clean_cnpj(self):
        cnpj = self.cleaned_data.get("cnpj", "").strip()
        tipo_pessoa = self.data.get("tipo_pessoa", "").strip()
        if tipo_pessoa == "PJ":
            if not cnpj:
                raise forms.ValidationError("Informe o CNPJ da empresa.")
            _validar_documento_por_tipo(cnpj, "PJ")
        else:
            cnpj = ""
        return cnpj

    def save(self, commit=True):
        instance = super().save(commit=False)
        tipo_pessoa = self.cleaned_data.get("tipo_pessoa", "")
        instance.tipo_pessoa = tipo_pessoa
        instance.nome_solicitante = self.cleaned_data.get("nome_solicitante", "").strip()
        if tipo_pessoa == "PJ":
            instance.cnpj = self.cleaned_data.get("cnpj", "").strip()
        else:
            instance.cnpj = ""
        if commit:
            instance.save()
        return instance


class AdminAlterarSenhaForm(forms.Form):
    """Gestor administrativo redefine a senha de um usuário (sem senha atual)."""

    nova_senha = forms.CharField(
        label="Nova Senha",
        widget=forms.PasswordInput(attrs={
            "placeholder": "Digite a nova senha",
            "class": "form-control",
            "autocomplete": "new-password",
        }),
        required=True,
        min_length=8,
        error_messages={
            "required": "Informe a nova senha.",
            "min_length": "A senha deve ter pelo menos 8 caracteres.",
        },
    )
    confirmar_senha = forms.CharField(
        label="Confirmar Nova Senha",
        widget=forms.PasswordInput(attrs={
            "placeholder": "Confirme a nova senha",
            "class": "form-control",
            "autocomplete": "new-password",
        }),
        required=True,
        error_messages={"required": "Confirme a nova senha."},
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_nova_senha(self):
        nova_senha = self.cleaned_data.get("nova_senha")
        if nova_senha:
            try:
                validate_password(nova_senha, self.user)
            except ValidationError as e:
                raise forms.ValidationError(list(e.messages))
        return nova_senha

    def clean(self):
        cleaned_data = super().clean()
        nova_senha = cleaned_data.get("nova_senha")
        confirmar_senha = cleaned_data.get("confirmar_senha")
        if nova_senha and confirmar_senha and nova_senha != confirmar_senha:
            self.add_error("confirmar_senha", "As senhas não coincidem.")
        return cleaned_data


class AdminEditarAcessoForm(EditarDadosAcessoForm):
    """Edição de acesso pelo gestor administrativo, incluindo e-mail."""

    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "seu@email.com",
        }),
        required=True,
    )

    def __init__(self, *args, **kwargs):
        self.usuario = kwargs.pop("usuario", None)
        super().__init__(*args, **kwargs)
        if self.usuario and not self.is_bound:
            self.fields["email"].initial = self.usuario.email

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if not email:
            raise forms.ValidationError("Informe o e-mail.")
        qs = User.objects.filter(email__iexact=email)
        if self.usuario:
            qs = qs.exclude(pk=self.usuario.pk)
        if qs.exists():
            raise forms.ValidationError("Já existe um usuário com este e-mail.")
        return email

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.usuario:
            email = self.cleaned_data.get("email", "").strip().lower()
            self.usuario.email = email
            # Mantém username alinhado ao e-mail (padrão do sistema)
            self.usuario.username = email[:150]
            if commit:
                self.usuario.save(update_fields=["email", "username"])
        if commit:
            instance.save()
        return instance


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
        senha_atual = cleaned_data.get("senha_atual")

        if nova_senha and confirmar_senha:
            if nova_senha != confirmar_senha:
                raise forms.ValidationError("As senhas não coincidem.")

        if nova_senha and senha_atual:
            if self.user.check_password(nova_senha):
                raise forms.ValidationError("A nova senha deve ser diferente da senha atual.")

        return cleaned_data
