# Plataforma de Reembolsos

Sistema web desenvolvido para digitalizar o processo de solicitação, aprovação e acompanhamento de reembolsos corporativos.

A solução centraliza o fluxo de despesas, permitindo solicitações, aprovações em dois níveis, assinatura eletrônica, geração de documentos e acompanhamento dos pagamentos.

## Sobre o projeto

O projeto surgiu a partir da necessidade de substituir um processo que dependia de controles manuais e troca de documentos por uma solução centralizada e rastreável.

Atuei na construção técnica da plataforma, desde o entendimento dos requisitos e regras do processo até o desenvolvimento, integrações e disponibilização da aplicação.

## Funcionalidades

* Solicitação de reembolsos com despesas, centro de custo, código orçamentário e comprovantes
* Fluxo de aprovação em dois níveis
* Registro de motivos de rejeição
* Assinatura eletrônica integrada ao DocuSign
* Geração automática de documentos em PDF
* Cadastro de dados para pagamento via PIX ou transferência
* Dashboard gerencial para acompanhamento das solicitações
* Exportação de relatórios
* Upload e armazenamento de anexos em Amazon S3
* Autenticação, recuperação de senha e controle de acesso

## Stack

**Backend**

* Python
* Django 5
* PostgreSQL
* Gunicorn

**Integrações e serviços**

* DocuSign REST API
* Amazon S3
* Boto3

**Documentos e relatórios**

* ReportLab
* pypdf
* python-pptx

**Deploy**

* Render
* WhiteNoise

## Arquitetura

A aplicação foi estruturada em Django, separando as configurações do projeto da aplicação principal e mantendo integrações e responsabilidades específicas organizadas em módulos.

```text
plataforma-reembolsos/
├── base/                       # Configurações e URLs do projeto
├── intra/                      # Aplicação principal
│   ├── models.py               # Modelos e dados
│   ├── views.py                # Regras e fluxos da aplicação
│   ├── forms.py                # Formulários e validações
│   ├── docusign_integration.py # Integração com DocuSign
│   ├── pdf_reembolso.py        # Geração de documentos
│   └── templates/              # Interface
├── api_docu.py                 # Utilitário de autenticação DocuSign
├── requirements.txt
├── Procfile
└── .env.example
```

## Decisões técnicas

### Integração com DocuSign

A assinatura eletrônica foi implementada utilizando a API do DocuSign com autenticação JWT e assinatura RS256.

As credenciais e a chave privada são mantidas em variáveis de ambiente, evitando o armazenamento de informações sensíveis no código.

### Armazenamento de arquivos

Os comprovantes utilizam armazenamento local durante o desenvolvimento e Amazon S3 em produção, com configuração baseada em variáveis de ambiente.

### Geração de documentos

Os documentos de reembolso são gerados dinamicamente em PDF, permitindo padronizar os registros utilizados durante o processo de aprovação e assinatura.

## Principais desafios

* Transformar um processo manual em um fluxo digital estruturado
* Implementar um processo de aprovação com diferentes níveis de acesso
* Integrar assinatura eletrônica ao fluxo da aplicação
* Gerenciar documentos e anexos em diferentes ambientes
* Manter informações sensíveis fora do código-fonte
* Disponibilizar a aplicação em ambiente de produção

## Segurança

* Configurações sensíveis armazenadas em variáveis de ambiente
* Credenciais da integração com DocuSign não versionadas
* Chaves privadas mantidas fora do código-fonte
* Separação das configurações de desenvolvimento e produção

## Execução local

### Pré-requisitos

* Python 3.11+
* PostgreSQL (opcional para desenvolvimento)
* Credenciais das integrações necessárias

### Instalação

```bash
git clone https://github.com/bordaleo/plataforma-reembolsos.git
cd plataforma-reembolsos

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
```

Configure o arquivo `.env` a partir do `.env.example` e execute:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

A aplicação estará disponível em:

```text
http://127.0.0.1:8000/
```

## Resultado

A plataforma transforma o processo de reembolso em um fluxo digital centralizado, com maior organização, rastreabilidade e controle das etapas de solicitação, aprovação, assinatura e pagamento.

---

**Projeto desenvolvido por Leonardo Borda**
Python • Django • PostgreSQL • APIs • Integrações • Desenvolvimento Web
