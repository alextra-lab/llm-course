"""
Section 1 - Hello World: the raw HTTP version.

This talks to an OpenAI-compatible server using nothing but `requests`.
The point is to SEE the literal HTTP request and JSON response that every
fancier SDK ultimately sends.

    python examples/01/raw_http.py

Required environment variables (see .env.example):
    OPENAI_BASE_URL   e.g. https://your-host/v1   (note the trailing /v1)
    OPENAI_API_KEY    your auth token
    MODEL             the model id the server is serving
"""

import json
import os

import requests


def require_env(name: str) -> str:
    """Read an environment variable, or fail with a clear message."""
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


base_url = require_env("OPENAI_BASE_URL").rstrip("/")
api_key = require_env("OPENAI_API_KEY")
model = require_env("MODEL")

# The chat endpoint lives at <base_url>/chat/completions.
url = f"{base_url}/chat/completions"

# This is the ENTIRE request body. `messages` is a list of role/content turns.
payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": "You are a concise, friendly assistant."},
        {"role": "user", "content": "Say hello in one short sentence."},
    ],
}

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

response = requests.post(url, headers=headers, json=payload, timeout=30)
response.raise_for_status()  # turn an HTTP 4xx/5xx into a Python exception
data = response.json()

# Show the whole response once, so you can see its shape...
print("=== raw JSON response ===")
print(json.dumps(data, indent=2))

# ...then pull out the single most important field: the assistant's reply.
print("\n=== the reply ===")
print(data["choices"][0]["message"]["content"])

# `usage` tells you how many tokens went in and came back. We'll lean on this a lot.
print("\n=== usage ===")
print(data["usage"])
