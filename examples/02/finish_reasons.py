"""
Section 2 - Anatomy of a Response: why did the model stop?

`finish_reason` tells you WHY generation ended. The two you'll meet first:
  - "stop"   : the model finished its answer naturally.
  - "length" : it hit the max_tokens ceiling before finishing.

The habit to build: trust `finish_reason`, NOT the length of the text. With a tight
budget a reasoning model can spend the whole allowance on hidden thinking and return
EMPTY content -- still `finish_reason="length"`. A model that does not think first
returns text that stops mid-sentence. Either way, "length" means "not finished".

    python examples/02/finish_reasons.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

prompt = [{"role": "user", "content": "Write a short paragraph about the ocean."}]


def ask(max_tokens):
    response = client.chat.completions.create(
        model=MODEL, messages=prompt, max_tokens=max_tokens
    )
    choice = response.choices[0]
    details = getattr(response.usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None)
    return choice.finish_reason, choice.message.content or "", reasoning


# Generous ceiling: the model finishes on its own -> "stop".
reason, text, _ = ask(max_tokens=300)
print(f"[max_tokens=300] finish_reason={reason!r} ({len(text)} chars)")
print(text)

print("\n" + "-" * 60 + "\n")

# Tiny ceiling: the model is cut off -> "length". On a reasoning model the reply may
# be EMPTY, because the few tokens were spent on thinking instead of the answer.
reason, text, reasoning = ask(max_tokens=8)
print(f"[max_tokens=8] finish_reason={reason!r} ({len(text)} chars)")
print(f"content: {text!r}")
if reasoning is not None:
    print(f"(those tokens went to hidden thinking: reasoning_tokens={reasoning})")
print("\nKey point: finish_reason='length' means the answer is NOT complete -- whether")
print("the text is cut off mid-sentence or comes back empty. Check the reason, never the")
print("text length. (Why a tiny budget vanishes into thinking is Section 6.)")
