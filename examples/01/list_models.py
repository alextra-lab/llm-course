"""
Section 1 - Hello World: which model is being served?

A vLLM server usually serves ONE model, named by its Hugging Face repo id.
If you don't know what to put in MODEL, ask the server:

    GET <base_url>/models

    python examples/01/list_models.py
"""

import os

import requests

base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
api_key = os.environ["OPENAI_API_KEY"]

response = requests.get(
    f"{base_url}/models",
    headers={"Authorization": f"Bearer {api_key}"},
    timeout=30,
)
response.raise_for_status()

print("Models served by this endpoint:")
for model in response.json()["data"]:
    print(f"  {model['id']}")
