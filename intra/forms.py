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
            "conta_numero": forms.TextInput(attrs={"placeholder": "000000", "maxlength": "8"}),
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
        if forma_pagamento == "PIX":
            if not cpf_pix:
                raise forms.ValidationError("Este campo é obrigatório.")
            numeros = ''.join(filter(str.isdigit, cpf_pix))
            if len(numeros) != 11 and len(numeros) != 14:
                raise forms.ValidationError("CPF deve ter 11 dígitos ou CNPJ deve ter 14 dígitos.")
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
            numeros = ''.join(filter(str.isdigit, cpf_transferencia))
            if len(numeros) != 11 and len(numeros) != 14:
                raise forms.ValidationError("CPF deve ter 11 dígitos ou CNPJ deve ter 14 dígitos.")
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
            # Remover caracteres não numéricos para validação
            numeros = ''.join(filter(str.isdigit, cpf_pix))
            if len(numeros) != 11 and len(numeros) != 14:
                raise forms.ValidationError("CPF deve ter 11 dígitos ou CNPJ deve ter 14 dígitos.")
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
            # Remover caracteres não numéricos para validação
            numeros = ''.join(filter(str.isdigit, cpf_transferencia))
            if len(numeros) != 11 and len(numeros) != 14:
                raise forms.ValidationError("CPF deve ter 11 dígitos ou CNPJ deve ter 14 dígitos.")
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
            # Se for "Outro", pegar o valor do campo hidden (que foi atualizado pelo JavaScript)
            if banco_pix_select == "Outro":
                banco_pix_value = self.data.get("banco_pix", "").strip()
                if banco_pix_value:
                    cleaned_data["banco_pix"] = banco_pix_value
                else:
                    cleaned_data["banco_pix"] = banco_pix_select
            else:
                cleaned_data["banco_pix"] = banco_pix_select
            # Limpar campos de transferência quando PIX é selecionado
            cleaned_data["banco"] = ""
            cleaned_data["agencia"] = ""
            cleaned_data["conta_tipo"] = ""
            cleaned_data["conta_numero"] = ""
            cleaned_data["cpf_transferencia"] = ""
        elif forma_pagamento == "TRANSFERENCIA":
            banco_transf_select = cleaned_data.get("banco_transf_select", "").strip()
            # Se for "Outro", pegar o valor do campo hidden (que foi atualizado pelo JavaScript)
            if banco_transf_select == "Outro":
                banco_value = self.data.get("banco", "").strip()
                if banco_value:
                    cleaned_data["banco"] = banco_value
                else:
                    cleaned_data["banco"] = banco_transf_select
            else:
                cleaned_data["banco"] = banco_transf_select
            # Limpar campos de PIX quando TRANSFERENCIA é selecionado
            cleaned_data["banco_pix"] = ""
            cleaned_data["chave_pix"] = ""
            cleaned_data["cpf_pix"] = ""
        
        return cleaned_data
    
    def save(self, commit=True):
        """Salvar o formulário garantindo que os campos sejam salvos corretamente."""
        instance = super().save(commit=False)
        
        # Garantir que os campos hidden sejam atualizados com os valores do cleaned_data
        forma_pagamento = self.cleaned_data.get("forma_pagamento", "")
        
        # Garantir que forma_pagamento seja salvo explicitamente
        if forma_pagamento:
            instance.forma_pagamento = forma_pagamento
        
        if forma_pagamento == "PIX":
            # Atualizar campos de PIX
            instance.banco_pix = self.cleaned_data.get("banco_pix", "").strip()
            instance.chave_pix = self.cleaned_data.get("chave_pix", "").strip()
            instance.cpf_pix = self.cleaned_data.get("cpf_pix", "").strip()
            # Limpar campos de transferência
            instance.banco = ""
            instance.agencia = ""
            instance.conta_tipo = ""
            instance.conta_numero = ""
            instance.cpf_transferencia = ""
        elif forma_pagamento == "TRANSFERENCIA":
            # Atualizar campos de transferência
            instance.banco = self.cleaned_data.get("banco", "").strip()
            banco_selecionado = instance.banco
            
            # Se for Carteira Digital, usar campo carteira_digital se existir no POST
            if banco_selecionado == "Carteira Digital":
                # Para Carteira Digital, usar conta_numero para armazenar a chave
                carteira_digital = self.data.get("carteira_digital", "").strip()
                if carteira_digital:
                    instance.conta_numero = carteira_digital
                instance.agencia = ""
                instance.conta_tipo = ""
            else:
                # Para banco tradicional, usar campos normais
                instance.agencia = self.cleaned_data.get("agencia", "").strip()
                instance.conta_tipo = self.cleaned_data.get("conta_tipo", "").strip()
                instance.conta_numero = self.cleaned_data.get("conta_numero", "").strip()
            
            instance.cpf_transferencia = self.cleaned_data.get("cpf_transferencia", "").strip()
            # Limpar campos de PIX
            instance.banco_pix = ""
            instance.chave_pix = ""
            instance.cpf_pix = ""
        
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
        
        # Verificar se a nova senha é igual à senha atual
        if nova_senha and senha_atual:
            if self.user.check_password(nova_senha):
                raise forms.ValidationError("A nova senha deve ser diferente da senha atual.")
        
        return cleaned_data
