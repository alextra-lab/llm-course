"""
Section 7 - Streaming: the raw server-sent events (foundations first).

When you add "stream": true, the server doesn't send one JSON response. It holds
the connection open and pushes a sequence of small "events", each a line that
starts with `data: `, each carrying the next little piece (a "delta") of the
answer. The stream ends with a literal `data: [DONE]`.

We read that raw protocol by hand here, with `requests`, so you see what the SDK
is doing under the hood.

    python examples/07/raw_sse.py
"""

import json
import os

import requests

base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
api_key = os.environ["OPENAI_API_KEY"]
model = os.environ.get("MODEL", "openai/gpt-oss-120b")

# TLS verification (see README "Troubleshooting: SSL / certificates"). Point
# OPENAI_CA_BUNDLE at a trusted CA, or set OPENAI_INSECURE=1 to skip (insecure).
verify = False if os.environ.get("OPENAI_INSECURE", "").lower() in ("1", "true", "yes") \
    else (os.environ.get("OPENAI_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE")
          or os.environ.get("SSL_CERT_FILE") or True)

resp = requests.post(
    f"{base_url}/chat/completions",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    json={
        "model": model,
        "messages": [{"role": "user", "content": "Count from 1 to 10 slowly."}],
        "stream": True,  # <-- the switch that changes everything
    },
    stream=True,  # tell requests not to buffer the whole body
    timeout=60,
    verify=verify,
)
resp.raise_for_status()

print("=== streaming raw events ===")
for line in resp.iter_lines():
    if not line:
        continue  # events are separated by blank lines
    line = line.decode("utf-8")
    if not line.startswith("data: "):
        continue
    payload = line[len("data: "):]
    if payload == "[DONE]":
        break
    chunk = json.loads(payload)
    delta = chunk["choices"][0]["delta"]
    piece = delta.get("content")
    if piece:
        print(piece, end="", flush=True)  # print as it arrives, no newline

print("\n\n(done -- the answer arrived in many small pieces)")
