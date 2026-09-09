Sistema web para gestão do ciclo completo de solicitações de reembolso corporativo: desde o envio pelo colaborador até a aprovação em dois níveis, assinatura eletrônica e confirmação de pagamento.

Desenvolvido com Django, com fluxo de aprovação configurável, geração de PDF, assinatura digital via DocuSign e armazenamento de anexos em Amazon S3.

✨ Funcionalidades
Solicitação de reembolso — colaborador registra despesas com centro de custo, código orçamentário, valor e anexos (comprovantes).
Fluxo de aprovação em dois níveis — cada solicitação passa pela aprovação do gestor direto e, em seguida, do gestor administrativo, com motivo de rejeição registrado em cada etapa.
Assinatura eletrônica (DocuSign) — envio automático do documento de reembolso para assinatura das partes via integração JWT (RS256) com a API do DocuSign, incluindo reenvio de envelope quando necessário.
Geração de PDF — comprovante de reembolso gerado dinamicamente (ReportLab / pypdf) para cada solicitação.
Dados de pagamento — cadastro de dados bancários do solicitante (PIX ou transferência) usados para agendar e confirmar o pagamento.
Dashboard gerencial — visão consolidada das solicitações para gestores, com exportação de gráficos para PowerPoint (.pptx).
Cadastro e recuperação de acesso — login próprio, primeiro acesso com completar cadastro, e fluxo de redefinição de senha por e-mail.
Anexos em nuvem — upload de comprovantes com armazenamento local em desenvolvimento e Amazon S3 em produção (fallback automático conforme variáveis de ambiente).
🧱 Stack técnica
Camada	Tecnologia
Backend	Django 5.x
Banco de dados	PostgreSQL (produção) / SQLite (desenvolvimento)
Armazenamento de arquivos	Amazon S3 (django-storages + boto3)
Geração de PDF	ReportLab, pypdf
Exportação de relatórios	python-pptx
Assinatura eletrônica	DocuSign REST API (JWT Grant, RS256)
Servidor de aplicação	Gunicorn + WhiteNoise
Deploy	Render (via Procfile)
🗂 Estrutura do projeto
plataforma-reembolsos/
├── base/                  # Configurações do projeto Django (settings, urls raiz, wsgi)
├── intra/                 # App principal: models, views, forms, integração DocuSign
│   ├── docusign_integration.py
│   ├── pdf_reembolso.py
│   ├── models.py
│   ├── views.py
│   └── templates/intra/
├── api_docu.py            # Script utilitário para testar a autenticação JWT com o DocuSign
├── manage.py
├── requirements.txt
├── Procfile
└── .env.example
⚙️ Como rodar localmente
Pré-requisitos
Python 3.11+
PostgreSQL (opcional em dev — o projeto também roda com SQLite)
Passos
bash
# 1. Clonar o repositório
git clone https://github.com/bordaleo/plataforma-reembolsos.git
cd plataforma-reembolsos

# 2. Criar e ativar um ambiente virtual
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Instalar dependências
pip install -r requirements.txt

# 4. Configurar variáveis de ambiente
cp .env.example .env
# edite o .env com suas credenciais (banco de dados, AWS, e-mail, DocuSign)

# 5. Aplicar migrações
python manage.py migrate

# 6. Criar um superusuário (acesso administrativo)
python manage.py createsuperuser

# 7. Rodar o servidor de desenvolvimento
python manage.py runserver

O projeto estará disponível em http://127.0.0.1:8000/.

Variáveis de ambiente

Veja .env.example para a lista completa. Os principais grupos de configuração são:

Django: DJANGO_SECRET_KEY, DJANGO_DEBUG, DJANGO_CSRF_TRUSTED_ORIGINS, DATABASE_URL
AWS S3 (opcional em dev — sem essas variáveis, o storage cai para o sistema de arquivos local): AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME, AWS_S3_REGION_NAME
E-mail (recuperação de senha): EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, DEFAULT_FROM_EMAIL
DocuSign: DOCUSIGN_INTEGRATION_KEY, DOCUSIGN_USER_ID, DOCUSIGN_ACCOUNT_ID, DOCUSIGN_BASE_URI, DOCUSIGN_AUTH_SERVER, DOCUSIGN_PRIVATE_KEY

Nunca commite o arquivo .env com valores reais — apenas .env.example, sem segredos.

🚀 Deploy

O projeto está configurado para rodar em serviços como o Render, usando Gunicorn como servidor WSGI e WhiteNoise para servir arquivos estáticos:

web: gunicorn base.wsgi
🔒 Segurança
Configurações sensíveis ficam fora do código-fonte, via variáveis de ambiente.
Autenticação com DocuSign via JWT assinado (RS256) usando chave privada configurada em ambiente, não versionada.
Recomenda-se nunca versionar o banco de dados local (db.sqlite3) — ele deve conter apenas dados de desenvolvimento e estar listado no .gitignore.
📄 Licença

Projeto pessoal/acadêmico, desenvolvido por Leonardo Borda.
