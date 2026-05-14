"""
Script de teste DocuSign (token JWT + chamada REST).
Credenciais: base.settings (DOCUSIGN_*) e chave PEM (env / settings / private.key).
"""
import os
import sys
import time

import django
import jwt
import requests

# Raiz do projeto no path (para `import base.settings` ao rodar este arquivo solto)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "base.settings")
django.setup()

from django.conf import settings  # noqa: E402

from intra.docusign_integration import _obter_private_key  # noqa: E402

INTEGRATION_KEY = settings.DOCUSIGN_INTEGRATION_KEY
USER_ID = settings.DOCUSIGN_USER_ID
ACCOUNT_ID = settings.DOCUSIGN_ACCOUNT_ID
BASE_URI = settings.DOCUSIGN_BASE_URI.rstrip("/")
AUTH_SERVER = settings.DOCUSIGN_AUTH_SERVER

now = int(time.time())
payload = {
    "iss": INTEGRATION_KEY,
    "sub": USER_ID,
    "aud": AUTH_SERVER,
    "iat": now,
    "exp": now + 3600,
    "scope": "signature impersonation",
}

private_key = _obter_private_key()
jwt_token = jwt.encode(payload, private_key, algorithm="RS256")

url = f"https://{AUTH_SERVER}/oauth/token"
headers = {"Content-Type": "application/x-www-form-urlencoded"}
data = {
    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
    "assertion": jwt_token,
}

response = requests.post(url, headers=headers, data=data)

print("TOKEN STATUS:", response.status_code)
print(response.text)

if response.status_code == 200:
    access_token = response.json()["access_token"]
    api_url = f"{BASE_URI}/restapi/v2.1/accounts/{ACCOUNT_ID}"
    api_headers = {"Authorization": f"Bearer {access_token}"}
    api_response = requests.get(api_url, headers=api_headers)
    print("\nAPI STATUS:", api_response.status_code)
    print(api_response.text)
