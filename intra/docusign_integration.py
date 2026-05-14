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

CLIENT_ID = "5d4ad290-183c-4562-b70d-c4d9b2e74d5b"
USER_ID = "50e2ce4d-7c27-49bd-a0d2-3850ebb47a02"
ACCOUNT_ID = "778b8142-54f1-49e1-a1ae-2f0542b95192"
BASE_URI = "https://na2.docusign.net"

# A chave privada pode ser lida de um arquivo ou variável de ambiente
PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAyXF/syTWni8byG1rURaLd3QJJOIfHf+e8aNW0yDKayL6VUxt
OamzZiXsOxVbwEeqGd+8ASzSehjm6NZIARwySQrKwy8qMi/TEc84ytUjHUsD3vRZ
wyqKUZMaDTLcsZRCNYjQmz6fd6o8/Fqg5j9iJ1snBL5j+EQNfucSDcXqHPQcfBvI
UXFv4Je/QDTRvVSFNdwOHZZcvo1x4Qk40qdrNA0f7lpBS7sFTA0SG7GN+pXSBN1W
FsgYIF2EUhKlhU9uMGbwoA6DJFGEStv/uwmn9ClMDmSWDB0S0StE/z4bdTb+CXT9
HcujV6BqDxb1iVxaIYaBvqDa+SzPAnFM6Xdz+QIDAQABAoIBAE16ySonNiEbb200
oL1MlZH5YHb+Pge0xPad44xLJW/1wSFDxxMRsX3NgkHrYiHfro5LHq25Bq+Nmmrd
2E4NAU5Ux04xeuJYwK8t6+Mf/WSL8M41X70QRKlBkhiXgokOxDSBDfNYL8+/+7r4
RMCqil8m0Sgi7qKT0jkIOUpw4C4ICu4aA0KIQIL8m1HRVIZ+IAEs3W6NwUcBtGLn
7ly8zjWei+8exRKK0p6Rw4fME+v0wjRhMIRv7dpGJtpwHBB4MF87Zw+21tbmxvkw
r76D30nQD2dWTMcSn8k21s3aN73BSFvr5yv/G8R7lQhEwIWhkm3HsFzc0n8BM2i5
nf82OD0CgYEA+MRggC1vpG4N+7njjOsBkM2LYjOThSiliZhbat1Zd+fs+8HqzH19
YFNEeUQT2Ysmo1Jp+EmzAc24DFfNsZ0/d3HESQ214hSp/FKoRlENf322v3BzFNEj
JB1OqC5ZPSbpUJjnq/BVprQH1SWnDiaGfvbiaRG7VhbtfRb20NncqLMCgYEAz0zh
v6uQGU9ywe0HGMRc+9QSFaxp4r060PMXg8CakS1DpAeaV7x6q21O/9PfWwCDxE1n
z15G4PD6GLgnng1+lWf6L3Tf8NvXxeFazgx7wkbx2fXYhSWS3yfQynhNxdxiZPcL
R6cBA3UzRXGCGF/RcAxyP+65rCDCkh24L0DozqMCgYAZi8EFKKVQU2ToNryhWfi9
L/5iRT2e7P+i05x/qt9nKs/xQoakHTbkz2g2s8D+FAYRu4LaVmclhkSiL9oVpTpB
P9OSVPAamVijarGRFv212+kKW7fVqWxcZw4Ow0Oyve4zsqAHzhRdnBs5zjYLg/VH
0H6Ln6CHRK96qwMJi3XXdQKBgBXjgFLEwsppYSyo4n7y/P56Pg6bzfJrGLLHeEwp
IikCJopDY0CwXiOLvzO0I3lwbHll0vhKdCF8UGwbxdMiiaMs/3XTWXINRJNYYEYx
ez/gTdk95Ebq2L9HbPx0B4JE6v7ONxqxv6Gl1mwWuC3qsCqspcOqaWCLdQAIs1IK
AIsRAoGBANpLIxx1JzfN+kRaQeFYBKWtrl+uFVk+KNZxDvidTzHD5sjtLAjGf3B5
58lOzLazQZt0kdKSCOvMVmF+TaPZdrmr8NxoJl2a4ZeuRn3W7X7ZQ4Ros7qTvsSM
xY5ocdUJAVkaoiDF+oHsq3jZBp1xTqqi0+zPDdbjHcYBkYc6S8mI
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
            "aud": "account.docusign.com",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "scope": "signature impersonation"
        }

        jwt_token = jwt.encode(payload, private_key, algorithm="RS256")

        url = "https://account.docusign.com/oauth/token"

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
