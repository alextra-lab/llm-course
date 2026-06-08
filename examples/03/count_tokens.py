"""
Section 3 - Tokens: measure them through the server (no local tokenizer).

We don't have tiktoken or a Hugging Face tokenizer in this course. We don't need
them: the server already tokenizes our input and reports the count in
`usage.prompt_tokens`. We use that to see how different text becomes different
numbers of tokens.

    python examples/03/count_tokens.py

Note: prompt_tokens includes a fixed overhead from the chat template (the role
delimiters from Section 1). To isolate the text itself, we measure an empty
message once as a baseline and subtract it.
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


baseline = prompt_tokens("")  # overhead of an empty user message

samples = [
    "hello",
    "  hello",          # leading whitespace is its own token(s)
    "HELLO",            # casing changes the tokenization
    "hello world",
    "antidisestablishmentarianism",   # one long rare word -> several tokens
    " catastrophe",
    "🦜🦜🦜",            # emoji are often multiple tokens each
    "def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)",  # code
]

print(f"(template overhead baseline = {baseline} tokens)\n")
print(f"{'text-only tokens':>16}   text")
print("-" * 60)
for s in samples:
    text_tokens = prompt_tokens(s) - baseline
    print(f"{text_tokens:>16}   {s!r}")

print("\nTakeaway: tokens are sub-word chunks, not words or characters.")
print("Whitespace, casing, rare words, emoji, and code all change the count.")
