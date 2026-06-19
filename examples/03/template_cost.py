"""
Section 3 - Chat Templates: see the template's cost through the standard API.

Your messages are rendered by the model's chat template into one flat token
string before the model sees them. You can't print that string with only the
standard API (and this course uses no local tokenizer), but you CAN measure its
size: usage.prompt_tokens counts the tokens AFTER templating. An empty message is
not zero -- the difference exposes the template's fixed per-request overhead.

    python examples/03/template_cost.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()


def prompt_tokens(text: str) -> int:
    """Tokens for a one-message prompt, AFTER the chat template is applied."""
    response = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": text}], max_tokens=1)
    return response.usage.prompt_tokens


empty = prompt_tokens("")
hi = prompt_tokens("hi")

print(f"empty message : {empty:>4} tokens  <- the template's fixed overhead")
print(f"'hi'          : {hi:>4} tokens")
print()
print("The empty message is not zero: every request you send is wrapped by the")
print("chat template (role markers, the trailing 'your turn' generation prompt, and")
print(f"more) before the model sees it. That wrapping is the {empty}-token overhead")
print("above -- and it differs from one model to the next. Run scripts/preflight.py")
print("to see the number for your endpoint.")
