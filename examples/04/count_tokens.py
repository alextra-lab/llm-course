"""
Section 4 - Tokens: measure them through the server (no local tokenizer).

We don't have tiktoken or a Hugging Face tokenizer in this course. We don't need
them: the server already tokenizes our input and reports the count in
`usage.prompt_tokens`. We use that to see how different text becomes different
numbers of tokens.

    python examples/04/count_tokens.py

Note: prompt_tokens includes a fixed overhead from the chat template (the role
delimiters from Section 3). We deliberately do NOT subtract an empty message to
"isolate" the text: the template wraps an empty message differently from a real one,
so that subtraction is misleading on some models (the harmony template used by
gpt-oss is one). Instead we print the RAW counts and read them by comparing rows --
two non-empty messages share the same fixed overhead, so the difference is the text.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()


def prompt_tokens(text: str) -> int:
    """Tokens for a single user message containing `text` (incl. template overhead)."""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": text}],
        max_tokens=1,  # we only care about the INPUT count, so generate almost nothing
    )
    return response.usage.prompt_tokens


samples = [
    "",                 # the fixed template overhead, paid on every request
    "hello",
    "HELLO",            # casing CAN change the split -- compare with 'hello'
    "  hello",          # leading whitespace is its own token(s)
    "hello world",      # one more word than 'hello' -> the jump is its cost
    "antidisestablishmentarianism",   # one long rare word -> several tokens
    "🦜🦜🦜",            # emoji are often multiple tokens each
    "def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)",  # code
]

print(f"{'prompt_tokens':>13}   text")
print("-" * 60)
for s in samples:
    print(f"{prompt_tokens(s):>13}   {s!r}")

print("\nRead by COMPARING rows, not in isolation:")
print("  - '' is not zero: that is the chat template's fixed overhead.")
print("  - 'hello' vs 'HELLO': casing can shift the count (model-specific).")
print("  - 'hello' vs 'hello world': the difference is the added word.")
print("Tokens are sub-word chunks; the split itself depends on the model.")
