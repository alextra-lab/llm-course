"""
Section 3 - Chat Templates: messages vs. the model's real input.

You write a list of {role, content} messages. The model never sees that list --
it sees ONE flat string of tokens with special delimiter tokens. The bridge
between the two is the model's "chat template".

OPTIONAL BONUS: some servers expose a NON-STANDARD `/tokenize` endpoint (it lives
at the server ROOT, not under /v1) that can show you that rendered string. It is
not part of the OpenAI API, so most endpoints don't have it -- this script falls
back to counting tokens via the standard chat endpoint's `usage` when /tokenize
isn't reachable (the normal case).

    python examples/03/show_template.py
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
    data = resp.json()
    # Some OpenAI-compatible servers answer 200 with an error body instead of a 404,
    # so raise_for_status() won't catch them. Only treat this as a real tokenize
    # response if the expected fields (count / tokens) are actually present.
    if isinstance(data, dict) and ("count" in data or "tokens" in data):
        body = data
    else:
        print("/tokenize did not return the expected token fields on this endpoint "
              f"(got keys: {list(data) if isinstance(data, dict) else type(data).__name__}).\n")
except (requests.RequestException, ValueError) as err:
    # ValueError covers a non-JSON 200 body (resp.json() failing).
    print(f"/tokenize not available on this endpoint ({err}).\n")

if body is not None:
    print("=== token count (after templating) ===")
    print(body.get("count"))

    token_strs = body.get("token_strs")
    if token_strs:
        # Joining the token pieces reconstructs the EXACT string the model sees,
        # including the special role delimiters and the trailing generation prompt.
        # Everything here that ISN'T your two sentences -- the role markers, the
        # channel/format tokens, the trailing generation prompt -- is the template's
        # fixed overhead. That is why even an empty message costs tokens (Section 4).
        print("\n=== rendered template (reconstructed from tokens) ===")
        print("".join(token_strs))
    else:
        print("\n=== token ids ===")
        print(body.get("tokens"))
else:
    # Fallback: without /tokenize we can't reconstruct the rendered string, but
    # `usage.prompt_tokens` still tells us how many tokens these messages became
    # AFTER the template was applied -- text plus the template's fixed overhead.
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages, "max_tokens": 1},
        timeout=30,
        verify=verify,
    )
    resp.raise_for_status()
    print("=== tokens after templating (from usage.prompt_tokens) ===")
    print(resp.json()["usage"]["prompt_tokens"])
    print("(the rendered template string itself needs the /tokenize endpoint, "
          "which this server doesn't expose.)")
