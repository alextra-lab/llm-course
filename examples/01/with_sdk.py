"""
Section 1 - Hello World: the SDK version.

Exactly the same request as raw_http.py, but using the official `openai`
client. There is no new magic here: the SDK builds the same JSON body and
POSTs it to the same /chat/completions endpoint. It just spares you from
writing the HTTP plumbing by hand.

    python examples/01/with_sdk.py
"""

import os

from openai import OpenAI

# The client just needs to know WHERE to send requests and HOW to authenticate.
# (If you omit these, the SDK falls back to the OPENAI_BASE_URL / OPENAI_API_KEY
# environment variables anyway -- we pass them explicitly here to be obvious.)
client = OpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
)

response = client.chat.completions.create(
    model=os.environ["MODEL"],
    messages=[
        {"role": "system", "content": "You are a concise, friendly assistant."},
        {"role": "user", "content": "Say hello in one short sentence."},
    ],
)

# The SDK returns typed objects instead of raw dicts, but the fields line up
# one-to-one with the JSON you saw in raw_http.py.
print("=== the reply ===")
print(response.choices[0].message.content)

print("\n=== usage ===")
print(response.usage)
