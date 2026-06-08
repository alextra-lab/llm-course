"""
Section 4 - Sampling: reproducibility with `seed`.

Randomness is useful, but sometimes you want the SAME output again -- for tests,
for debugging, for caching. A fixed `seed` (with the same inputs) asks the server
to make the same random choices.

    python examples/04/seed_demo.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

prompt = [{"role": "user", "content": "Invent a name for a coffee shop."}]


def generate(seed: int) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=prompt,
        temperature=1.0,   # plenty of randomness...
        seed=seed,         # ...but pinned by the seed
        max_tokens=20,
    )
    return response.choices[0].message.content.strip()


print("same seed (42), twice:")
print("  ", generate(42))
print("  ", generate(42))

print("\ndifferent seed (99):")
print("  ", generate(99))

print(
    "\nThe two seed-42 lines should match; seed-99 should differ.\n"
    "Caveat: seeds are best-effort. Server batching, load, or a config change\n"
    "(watch system_fingerprint) can still cause small differences."
)
