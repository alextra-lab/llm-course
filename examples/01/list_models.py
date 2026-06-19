"""
Section 1 - Hello World: which model is being served?

An OpenAI-compatible server may serve one or more models, each with an id.
If you don't know what to put in MODEL, ask the server:

    GET <base_url>/models

    python examples/01/list_models.py
"""

import os

import requests

base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
api_key = os.environ["OPENAI_API_KEY"]

# TLS verification (see README "Troubleshooting: SSL / certificates"). Point
# OPENAI_CA_BUNDLE at a trusted CA, or set OPENAI_INSECURE=1 to skip (insecure).
verify = False if os.environ.get("OPENAI_INSECURE", "").lower() in ("1", "true", "yes") \
    else (os.environ.get("OPENAI_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE")
          or os.environ.get("SSL_CERT_FILE") or True)

response = requests.get(
    f"{base_url}/models",
    headers={"Authorization": f"Bearer {api_key}"},
    timeout=30,
    verify=verify,
)
response.raise_for_status()

print("Models served by this endpoint:")
for model in response.json()["data"]:
    print(f"  {model['id']}")
