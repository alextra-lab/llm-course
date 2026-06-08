"""
Section 2 - Anatomy of a Response: look at the whole object.

Make one ordinary call, then print the complete response so you can see every
field the server hands back -- not just the reply text.

    python examples/02/inspect_response.py
"""

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": "You are a concise assistant."},
        {"role": "user", "content": "Name three primary colors."},
    ],
)

# `model_dump()` turns the SDK's typed object back into a plain dict, which is
# the same shape as the raw JSON from Section 1. Great for inspection.
print("=== full response object ===")
print(json.dumps(response.model_dump(), indent=2, default=str))

print("\n=== the three fields that matter most ===")
choice = response.choices[0]
print("content       :", choice.message.content)
print("finish_reason :", choice.finish_reason)
print("usage         :", response.usage)

# gpt-oss is a reasoning model, so the message MIGHT also carry its private
# thinking. We only peek here; Section 5 is all about this.
reasoning = getattr(choice.message, "reasoning_content", None)
if reasoning:
    print("\n(this model also returned reasoning_content -- see Section 5)")
