"""
Section 6 - Reasoning: the thinking next to the answer.

gpt-oss-120b works through a problem privately, then gives a final answer. Many
vLLM endpoints surface that private thinking as `reasoning_content`, separate
from the user-facing `content`. The thinking is real generated text, so it shows
up in the token usage too.

    python examples/06/reasoning.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

# A classic trick question -- the intuitive answer ($0.10) is wrong; the right
# answer is $0.05. Reasoning models tend to catch this BECAUSE they think first.
response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {
            "role": "user",
            "content": "A bat and a ball cost $1.10 in total. The bat costs $1.00 "
            "more than the ball. How much does the ball cost?",
        }
    ],
)

message = response.choices[0].message

# reasoning_content may or may not be exposed, depending on the endpoint config.
reasoning = getattr(message, "reasoning_content", None)
if reasoning:
    print("=== reasoning (the model's private thinking) ===")
    print(reasoning)
else:
    print("(this endpoint did not expose reasoning_content separately)")

print("\n=== final answer (what the user sees) ===")
print(message.content)

print("\n=== usage ===")
print(response.usage)

# If the endpoint reports it, reasoning tokens are broken out here -- and they're
# part of completion_tokens, i.e. you pay for them.
details = getattr(response.usage, "completion_tokens_details", None)
reasoning_tokens = getattr(details, "reasoning_tokens", None) if details else None
if reasoning_tokens is not None:
    print(f"\nreasoning_tokens = {reasoning_tokens} (included in completion_tokens)")
