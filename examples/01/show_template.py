"""
Section 1 - Hello World: messages vs. the model's real input.

You write a list of {role, content} messages. The model never sees that list --
it sees ONE flat string of tokens with special delimiter tokens. The bridge
between the two is the model's "chat template".

vLLM can show you that rendered string via its /tokenize endpoint -- a vLLM
extension that lives at the server ROOT, not under /v1. Not every hosted
endpoint exposes it, so this script falls back to counting tokens via the
normal chat endpoint's `usage` when /tokenize isn't reachable.

    python examples/01/show_template.py
"""

import os

import requests

base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
api_key = os.environ["OPENAI_API_KEY"]
model = os.environ["MODEL"]
headers = {"Authorization": f"Bearer {api_key}"}

# TLS verification (see README "Troubleshooting: SSL / certificates"). Point
# OPENAI_CA_BUNDLE at a trusted CA, or set OPENAI_INSECURE=1 to skip (insecure).
verify = False if os.environ.get("OPENAI_INSECURE", "").lower() in ("1", "true", "yes") \
    else (os.environ.get("OPENAI_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE")
          or os.environ.get("SSL_CERT_FILE") or True)

messages = [
    {"role": "system", "content": "You are a concise, friendly assistant."},
    {"role": "user", "content": "Say hello in one short sentence."},
]

# /tokenize lives at the server root, so drop a trailing /v1 if present.
server_root = base_url[:-3].rstrip("/") if base_url.endswith("/v1") else base_url

body = None
try:
    resp = requests.post(
        f"{server_root}/tokenize",
        headers=headers,
        json={
            "model": model,
            "messages": messages,
            "add_generation_prompt": True,  # append the "your turn" cue for the assistant
            "return_token_strs": True,      # also give us the text of each token
        },
        timeout=30,
        verify=verify,
    )
    resp.raise_for_status()
    body = resp.json()
except requests.RequestException as err:
    print(f"/tokenize not available on this endpoint ({err}).\n")

if body is not None:
    print("=== token count (after templating) ===")
    print(body.get("count"))

    token_strs = body.get("token_strs")
    if token_strs:
        # Joining the token pieces reconstructs the EXACT string the model sees,
        # including the special role delimiters and the trailing generation prompt.
        print("\n=== rendered template (reconstructed from tokens) ===")
        print("".join(token_strs))
    else:
        print("\n=== token ids ===")
        print(body.get("tokens"))
else:
    # Fallback: we can't see the rendered string, but `usage.prompt_tokens` still
    # tells us how many tokens these messages became after the template was applied.
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages, "max_tokens": 1},
        timeout=30,
        verify=verify,
    )
    resp.raise_for_status()
    print("=== prompt token count (from usage) ===")
    print(resp.json()["usage"]["prompt_tokens"])
