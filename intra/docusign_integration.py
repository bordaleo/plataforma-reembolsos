"""
Integração com DocuSign para envio de documentos para assinatura.
Baseado no código int.py fornecido pelo usuário.
"""
import base64
import requests
import jwt
import time
import os
import tempfile
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# Marcadores únicos no PDF (também desenhados em branco na folha de rosto) para o DocuSign
# posicionar as assinaturas sem colidir quando nomes são iguais ou muito parecidos.
DOCUSIGN_ANCHOR_SOLICITANTE = "[[SIG_SOL]]"
DOCUSIGN_ANCHOR_GESTOR = "[[SIG_GEST]]"

# ==============================
# CREDENCIAIS DOCUSIGN
# ==============================

CLIENT_ID = "51e88a4b-559f-4e0f-ab4f-1a77fd475acb"
USER_ID = "dd7ef85a-5ccc-49c6-8b23-7cbca9b76248"
ACCOUNT_ID = "1e600e61-9ddc-42f7-ae9a-7c8adffdfae1"
BASE_URI = "https://demo.docusign.net"

# A chave privada pode ser lida de um arquivo ou variável de ambiente
PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAnw1le5+OHkm/defIaMiTu5C/RXBA43nqBMPNoxzlM6L+3jgv
NO7p0p8EkGPZJc9ola9Aem6RTC29ngjMiOLDRfM2HUgRr2sHj+EFdUjw/+vGYjO5
jkqxCKfgsix+63TF5De124zAwBsMEEKX7hLhxX+2vhVK0LwNnSZpH11qoFcG1fPz
gbaJMwq4CsEVdHbk4AWnwFg9sr2zYaUjKYuJSKH1j/9wNtC7KJUisURQeL81Vs1h
5b2MR7OSVbpp/7etP36g5gf/3o/vrVs9kGonJj6N6YNCymayZ7qlpQ9QxvoXy2/w
TZo12QU1OOxDfnf5R1TxZjiOoIXvNO3ZsKvoDQIDAQABAoIBAA6evx+Bd6FYxb99
9Pc4yhJO3bNxfELMr+e3OJL6nXuaddNnFmPHEE7lghhkV+S4r37kM0jtxUUGMVdd
Aga/0r5T0GYZpqfZbpeu/KmexlRn+lOIOKbCk9+ypXNgprFYXwu5g+nGAukbCLKt
RGTvGboQoXN7O+YlsG9WcAmg3yO/e2v5Ip6qLd6dWGNx0gfY92OstvfmAgE9FgZH
f15LssU3ZyvApa1G/q09qjM2ckfPH9EHq5k9wX5R3Zm+4520mC1OmGOd5CkARAD4
zyKpxS8oBxLnpzwctyzj5fBDmU3sS+48Vynfn7kFJE/is4k9UbIbCR28+pmDWh++
CQm/TZsCgYEA/YV5txWjG/J9GP4cOLhL9aOjVX75gO5A/yGBWX4+/+b7vc1N/206
XeyPSlehDENfytbaRt1qbkj8gRTCdz2pDCjkly+FVyRGerqp1jpA3591SIWTQ+P1
3Qz6a/wknhH3XK4kAC1zOO9glsGLPmRGDa3+Z8bdOOYY16tr6FVnncMCgYEAoJt6
yNX4FFbN/NiMv0Hb036Wb5ucO4OZhq/aOpamgP/ROJGDJtzWbZlxF5TiakCaR/c0
7PidVvpt1jypq5wFlSwoF5JVtVUJl8xkKAw66+jJOAHVg9t40UNq79wHu9e5puY9
QMQRBVBGg0SeFtxiZXGhXNzzMT8FcIhqzJX89e8CgYBGuLdNdYG8yBZRpIFm6TJ3
YaCstvEPIGeNRGF6/5a/eEX9mooJmQTRMq5+RJeufhT41pqpbhbEkSOvNoVREihY
NggejKkbuAjZL700/6cdOrRS+MAuDieF9JrfCMWGOujQN9vfGM6tsUk2hOM9Emfg
ZQs1E+qedsGzWCSP+VMgVQKBgHExQ1c6nk5PY3wZbxD4rKKhbAsa5AB53oEzfR2f
wZfXNDCnNYT1TdcOtssE7pIuF84yp0WAbvu3IiREutws6S5aYaNDSk6zsUAgGFK1
U+2iMfbcLAxzaPIrjrmgHH9CKiE70d3MkaZqDlhDyxuXlW2jqTNWsbt6jC3kp0ir
SQ5XAoGBALn5WBG/q6QFtFawKK4gaf0AuYu2TU4y+fUxFlUfZ6ai6cImpYrdPP1H
17ssL4OUpQ+Qnhex2FQsDDLV9pm0VzJjPfpveYHOx9TtDx2hTY5rarKON0RG20Y3
dRYSGK76qUTHavuLYTFIlQTJ2/gvzAJ0a/UsdCjqAxxnYAftx5zd
-----END RSA PRIVATE KEY-----"""


def _obter_private_key():
    """
    Obtém a chave privada de um arquivo ou da constante.
    Prioridade: arquivo private.key > variável de ambiente > constante.
    """
    # Tentar ler de arquivo primeiro
    private_key_file = os.path.join(settings.BASE_DIR, "private.key")
    if os.path.exists(private_key_file):
        try:
            with open(private_key_file, 'r') as f:
                return f.read()
        except Exception as e:
            logger.warning(f"Erro ao ler arquivo private.key: {e}")
    
    # Tentar variável de ambiente
    env_key = os.environ.get('DOCUSIGN_PRIVATE_KEY')
    if env_key:
        return env_key
    
    # Usar constante como fallback
    return PRIVATE_KEY


# ==============================
# GERAR TOKEN JWT
# ==============================

def gerar_token():
    """Gera token de acesso JWT para autenticação no DocuSign."""
    try:
        private_key = _obter_private_key()

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

        if "access_token" not in resposta:
            logger.error(f"Erro ao gerar token DocuSign: {resposta}")
            raise Exception("Erro ao gerar token: " + str(resposta))

        return resposta["access_token"]
    except Exception as e:
        logger.error(f"Erro ao gerar token DocuSign: {e}")
        raise


# ==============================
# ENVIAR DOCUMENTO
# ==============================

def enviar_documento_para_assinatura(pdf_bytes, email_solicitante, nome_solicitante, email_gestor=None, nome_gestor=None):
    """
    Envia documento PDF para assinatura no DocuSign.
    
    Args:
        pdf_bytes: Bytes do PDF a ser assinado
        email_solicitante: Email do solicitante (primeiro signatário)
        nome_solicitante: Nome do solicitante
        email_gestor: Email do gestor (segundo signatário, opcional)
        nome_gestor: Nome do gestor (opcional)
    
    Returns:
        dict: Resposta da API DocuSign com envelope_id e outros dados
    """
    try:
        token = gerar_token()

        # Converter PDF para base64
        pdf_base64 = base64.b64encode(pdf_bytes).decode()

        url = f"{BASE_URI}/restapi/v2.1/accounts/{ACCOUNT_ID}/envelopes"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        def _tab_anchor(anchor_str):
            return {
                "anchorString": anchor_str,
                "anchorYOffset": "-15",
                "anchorXOffset": "0",
                "anchorUnits": "pixels",
                "optional": "false",
            }

        es = (email_solicitante or "").strip().lower()
        eg = (email_gestor or "").strip().lower() if email_gestor else ""
        segundo_signatario = bool(eg and nome_gestor)

        tab_solicitante = _tab_anchor(DOCUSIGN_ANCHOR_SOLICITANTE)
        tab_gestor = _tab_anchor(DOCUSIGN_ANCHOR_GESTOR)

        if segundo_signatario and eg == es:
            # Mesmo e-mail: um destinatário com duas assinaturas em âncoras distintas (evita duplicar envelope/recipient).
            signers = [
                {
                    "email": email_solicitante,
                    "name": nome_solicitante,
                    "recipientId": "1",
                    "routingOrder": "1",
                    "tabs": {"signHereTabs": [tab_solicitante, tab_gestor]},
                }
            ]
        elif segundo_signatario:
            signers = [
                {
                    "email": email_solicitante,
                    "name": nome_solicitante,
                    "recipientId": "1",
                    "routingOrder": "1",
                    "tabs": {"signHereTabs": [tab_solicitante]},
                },
                {
                    "email": email_gestor,
                    "name": nome_gestor,
                    "recipientId": "2",
                    "routingOrder": "2",
                    "tabs": {"signHereTabs": [tab_gestor]},
                },
            ]
        else:
            signers = [
                {
                    "email": email_solicitante,
                    "name": nome_solicitante,
                    "recipientId": "1",
                    "routingOrder": "1",
                    "tabs": {"signHereTabs": [tab_solicitante]},
                }
            ]

        body = {
            "emailSubject": "Documento para assinatura - Solicitação de Reembolso",
            "documents": [
                {
                    "documentBase64": pdf_base64,
                    "name": "Solicitacao_Reembolso.pdf",
                    "fileExtension": "pdf",
                    "documentId": "1"
                }
            ],
            "recipients": {
                "signers": signers
            },
            "status": "sent"
        }

        response = requests.post(url, headers=headers, json=body)
        resposta = response.json()

        if response.status_code not in [200, 201]:
            logger.error(f"Erro ao enviar documento DocuSign: {resposta}")
            raise Exception(f"Erro ao enviar documento: {resposta}")

        logger.info(f"Documento enviado com sucesso para DocuSign. Envelope ID: {resposta.get('envelopeId')}")
        return resposta

    except Exception as e:
        logger.error(f"Erro ao enviar documento para DocuSign: {e}")
        raise


def consultar_status_envelope(envelope_id):
    """
    Consulta o status de um envelope no DocuSign.
    
    Args:
        envelope_id: ID do envelope no DocuSign
    
    Returns:
        dict: Informações do envelope incluindo status e informações dos signatários
    """
    try:
        token = gerar_token()

        url = f"{BASE_URI}/restapi/v2.1/accounts/{ACCOUNT_ID}/envelopes/{envelope_id}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        response = requests.get(url, headers=headers)
        
        if response.status_code not in [200, 201]:
            logger.error(f"Erro ao consultar envelope DocuSign: {response.json()}")
            raise Exception(f"Erro ao consultar envelope: {response.json()}")

        resposta = response.json()
        logger.info(f"Status do envelope {envelope_id}: {resposta.get('status')}")
        return resposta

    except Exception as e:
        logger.error(f"Erro ao consultar status do envelope DocuSign: {e}")
        raise


def reenviar_envelope_docusign(envelope_id):
    """
    Reenvia notificação de assinatura (resend envelope) aos signatários pendentes.
    Não funciona para envelope concluído, cancelado ou recusado.
    Ver: PUT .../envelopes/{id}?resend_envelope=true
    """
    token = gerar_token()
    url = (
        f"{BASE_URI}/restapi/v2.1/accounts/{ACCOUNT_ID}/envelopes/{envelope_id}"
        "?resend_envelope=true"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.put(url, headers=headers, json={})
    if response.status_code not in (200, 201, 204):
        try:
            err = response.json()
        except Exception:
            err = response.text
        logger.error(f"Erro ao reenviar envelope DocuSign {envelope_id}: {err}")
        raise Exception(f"Erro ao reenviar no DocuSign: {err}")
    logger.info(f"Envelope DocuSign reenviado (notificação): {envelope_id}")
    return response.json() if response.text else {}


def baixar_pdf_assinado(envelope_id):
    """
    Baixa o PDF assinado de um envelope no DocuSign.
    
    Args:
        envelope_id: ID do envelope no DocuSign
    
    Returns:
        bytes: Conteúdo do PDF assinado
    """
    try:
        token = gerar_token()

        base = (
            f"{BASE_URI}/restapi/v2.1/accounts/{ACCOUNT_ID}/envelopes/{envelope_id}"
            "/documents/combined"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/pdf",
        }
        response = requests.get(f"{base}?certificate=true", headers=headers)
        if response.status_code not in [200, 201]:
            response = requests.get(base, headers=headers)
        if response.status_code not in [200, 201]:
            logger.error(f"Erro ao baixar PDF DocuSign: {response.text}")
            raise Exception(f"Erro ao baixar PDF: {response.text}")
        return response.content

    except Exception as e:
        logger.error(f"Erro ao baixar PDF assinado do DocuSign: {e}")
        raise
