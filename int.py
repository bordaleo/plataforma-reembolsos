import base64
import requests
import jwt
import time

# ==============================
# CREDENCIAIS DOCUSIGN
# ==============================

CLIENT_ID = "51e88a4b-559f-4e0f-ab4f-1a77fd475acb"
USER_ID = "dd7ef85a-5ccc-49c6-8b23-7cbca9b76248"
ACCOUNT_ID = "1e600e61-9ddc-42f7-ae9a-7c8adffdfae1"
BASE_URI = "https://demo.docusign.net"

PRIVATE_KEY_FILE = "private.key"

# ==============================
# GERAR TOKEN JWT
# ==============================

def gerar_token():

    with open(PRIVATE_KEY_FILE) as f:
        private_key = f.read()

    payload = {
        "iss": CLIENT_ID,
        "sub": USER_ID,
        "aud": "account-d.docusign.com",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "scope": "signature impersonation"
    }

    jwt_token = jwt.encode(payload, private_key, algorithm="RS256")

    url = "https://account-d.docusign.com/oauth/token"

    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt_token
    }

    response = requests.post(url, data=data)

    resposta = response.json()
    print("Resposta OAuth:", resposta)

    if "access_token" not in resposta:
        raise Exception("Erro ao gerar token: " + str(resposta))

    return resposta["access_token"]

# ==============================
# ENVIAR DOCUMENTO
# ==============================

def enviar_documento(caminho_pdf, email, nome):

    token = gerar_token()

    with open(caminho_pdf, "rb") as f:
        pdf_base64 = base64.b64encode(f.read()).decode()

    url = f"{BASE_URI}/restapi/v2.1/accounts/{ACCOUNT_ID}/envelopes"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    body = {
    "emailSubject": "Documento para assinatura",
    "documents": [
        {
            "documentBase64": pdf_base64,
            "name": "Contrato.pdf",
            "fileExtension": "pdf",
            "documentId": "1"
        }
    ],
    "recipients": {
        "signers": [
            {
                "email": email,
                "name": nome,
                "recipientId": "1",
                "routingOrder": "1",
                "tabs": {
                    "signHereTabs": [
                        {
                            "anchorString": "Assinatura - Solicitante",
                            "anchorYOffset": "-25",
                            "anchorUnits": "pixels"
                        }
                    ]
                }
            },
            {
                "email": "leonardo.borda@parceirosedu.org.br",
                "name": "Leonardo Borda",
                "recipientId": "2",
                "routingOrder": "2",
                "tabs": {
                    "signHereTabs": [
                        {
                            "anchorString": "Assinatura - Gestor",
                            "anchorYOffset": "-25",
                            "anchorUnits": "pixels"
                        }
                    ]
                }
            }
        ]
    },
    "status": "sent"
}

    response = requests.post(url, headers=headers, json=body)

    print("Resposta DocuSign:", response.json())

# ==============================
# EXECUÇÃO
# ==============================

enviar_documento(
    "contrato.pdf",
    "joao.cardoso@parceirosedu.org.br",
    "Joao Bidon"
)