"""
Integração com DocuSign para envio de documentos para assinatura.
Baseado no código int.py fornecido pelo usuário.
"""
import base64
import requests
import jwt
import time
import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# Marcadores únicos no PDF (também desenhados em branco na folha de rosto) para o DocuSign
# posicionar as assinaturas sem colidir quando nomes são iguais ou muito parecidos.
DOCUSIGN_ANCHOR_SOLICITANTE = "[[SIG_SOL]]"
DOCUSIGN_ANCHOR_GESTOR = "[[SIG_GEST]]"

def _docusign_auth_server():
    return getattr(settings, "DOCUSIGN_AUTH_SERVER", "account.docusign.com").strip()


def _docusign_base_uri():
    return getattr(settings, "DOCUSIGN_BASE_URI", "https://na3.docusign.net").rstrip("/")


def _docusign_account_id():
    return getattr(settings, "DOCUSIGN_ACCOUNT_ID", "").strip()


def _obter_private_key():
    """
    Obtém o PEM da chave privada RSA.
    Prioridade: variável de ambiente DOCUSIGN_PRIVATE_KEY > settings.DOCUSIGN_PRIVATE_KEY
    > arquivo private.key na raiz do projeto.
    """
    env_key = os.environ.get("DOCUSIGN_PRIVATE_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    cfg_key = getattr(settings, "DOCUSIGN_PRIVATE_KEY", "") or ""
    if isinstance(cfg_key, str) and cfg_key.strip():
        return cfg_key.strip()

    private_key_file = os.path.join(settings.BASE_DIR, "private.key")
    if os.path.exists(private_key_file):
        try:
            with open(private_key_file, "r", encoding="utf-8") as f:
                pem = f.read().strip()
                if pem:
                    return pem
        except OSError as e:
            logger.warning("Erro ao ler arquivo private.key: %s", e)

    raise RuntimeError(
        "Chave privada DocuSign não configurada. Defina DOCUSIGN_PRIVATE_KEY, "
        "settings.DOCUSIGN_PRIVATE_KEY ou o arquivo private.key na raiz do projeto."
    )


# ==============================
# GERAR TOKEN JWT
# ==============================

def gerar_token():
    """Gera token de acesso JWT para autenticação no DocuSign."""
    try:
        private_key = _obter_private_key()

        auth_server = _docusign_auth_server()
        payload = {
            "iss": settings.DOCUSIGN_INTEGRATION_KEY,
            "sub": settings.DOCUSIGN_USER_ID,
            "aud": auth_server,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "scope": "signature impersonation",
        }

        jwt_token = jwt.encode(payload, private_key, algorithm="RS256")

        url = f"https://{auth_server}/oauth/token"

        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token,
        }

        response = requests.post(
            url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
        )
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

        url = (
            f"{_docusign_base_uri()}/restapi/v2.1/accounts/"
            f"{_docusign_account_id()}/envelopes"
        )

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

        url = (
            f"{_docusign_base_uri()}/restapi/v2.1/accounts/"
            f"{_docusign_account_id()}/envelopes/{envelope_id}"
        )

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
        f"{_docusign_base_uri()}/restapi/v2.1/accounts/"
        f"{_docusign_account_id()}/envelopes/{envelope_id}"
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
            f"{_docusign_base_uri()}/restapi/v2.1/accounts/"
            f"{_docusign_account_id()}/envelopes/{envelope_id}/documents/combined"
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
