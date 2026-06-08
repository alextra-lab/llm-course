"""
Section 10 - Cost: turn token usage into money.

Pricing is per token, usually quoted per 1,000,000 tokens, with input and output
priced differently (output is typically more expensive). Cached input tokens are
often discounted. Reasoning tokens are part of completion_tokens, so they're
already billed at the output rate.

Set the three prices below to match YOUR endpoint's pricing, then the math is the
same everywhere.

    python examples/10/cost.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

# --- Illustrative prices, USD per 1,000,000 tokens. Replace with yours. --------
PRICE_INPUT = 0.15
PRICE_OUTPUT = 0.60
PRICE_CACHED_INPUT = 0.075  # cached prompt tokens, often ~half price (Section: caching)


def cost_usd(usage) -> float:
    details = getattr(usage, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    fresh_input = usage.prompt_tokens - cached
    return (
        fresh_input / 1_000_000 * PRICE_INPUT
        + cached / 1_000_000 * PRICE_CACHED_INPUT
        + usage.completion_tokens / 1_000_000 * PRICE_OUTPUT
    )


response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "Explain what an API is, in two sentences."}],
)
usage = response.usage

print("usage:", usage)
print(f"\nthis call cost ~${cost_usd(usage):.6f}")
print(f"at this size, 1,000 such calls ~= ${cost_usd(usage) * 1000:.2f}")
