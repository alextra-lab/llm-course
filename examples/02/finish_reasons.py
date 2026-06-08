"""
Section 2 - Anatomy of a Response: why did the model stop?

`finish_reason` tells you WHY generation ended. The two you'll meet first:
  - "stop"   : the model finished its answer naturally.
  - "length" : it hit the max_tokens ceiling and was cut off mid-thought.

We force each one and print the result.

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
    return choice.finish_reason, choice.message.content


# Generous ceiling: the model finishes on its own -> "stop".
reason, text = ask(max_tokens=200)
print(f"[max_tokens=200] finish_reason={reason!r}")
print(text)

print("\n" + "-" * 60 + "\n")

# Tiny ceiling: the model gets chopped off -> "length".
reason, text = ask(max_tokens=8)
print(f"[max_tokens=8] finish_reason={reason!r}")
print(text)
print("\nNotice the text is cut off, and the reason is 'length', not 'stop'.")
