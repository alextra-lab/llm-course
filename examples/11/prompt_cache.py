"""
Section 11 - Prompt caching: pay full price for a long prefix once.

When many requests share the same long prefix (a big system prompt, a document,
few-shot examples), the server can reuse the work it already did for that prefix
instead of reprocessing it every time. vLLM does this automatically ("automatic
prefix caching").

We send the SAME long prefix twice with different questions and watch
`usage.prompt_tokens_details.cached_tokens` go from 0 (cold) to most of the
prompt (warm) -- and latency usually drops too.

    python examples/11/prompt_cache.py
"""

import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

# A long, STABLE prefix. In real apps this is your system prompt / reference doc.
big_prefix = "You are a helpful assistant. Reference notes:\n" + (
    "The Apollo program ran from 1961 to 1972. " * 400
)


def ask(question: str):
    start = time.perf_counter()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": big_prefix},   # the shared prefix
            {"role": "user", "content": question},        # the part that varies
        ],
        max_tokens=20,
    )
    ms = round((time.perf_counter() - start) * 1000)
    details = getattr(response.usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details else None
    return cached, response.usage.prompt_tokens, ms


print("first call (cold cache):")
cached, total, ms = ask("In one sentence: when did Apollo end?")
print(f"  cached_tokens={cached}  prompt_tokens={total}  latency_ms={ms}")

print("second call, same prefix (warm cache):")
cached, total, ms = ask("In one sentence: when did Apollo begin?")
print(f"  cached_tokens={cached}  prompt_tokens={total}  latency_ms={ms}")

print(
    "\nExpect the second call's cached_tokens to jump (most of the prefix reused).\n"
    "If cached_tokens is None, this endpoint doesn't report it -- the caching may\n"
    "still happen server-side; you just can't see it in usage."
)
