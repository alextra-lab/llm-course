"""
Section 4 - Sampling: SEE temperature change the output.

Same prompt, three temperatures. Low temperature is focused and repeatable;
high temperature is varied and, pushed far enough, incoherent. Run it a few
times and watch how the high-temperature line keeps changing while the
temperature-0 line stays put.

    python examples/04/temperature_sweep.py
"""

import sys
from pathlib import Path

from openai import BadRequestError

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

prompt = [{"role": "user", "content": "In one sentence, describe a city at night."}]


def generate(temperature: float) -> str:
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=prompt,
            temperature=temperature,
            # 120 tokens of room so a reasoning model still has space for a visible
            # sentence after it finishes thinking (see Sections 2-3).
            max_tokens=120,
        )
    except BadRequestError:
        # The maximum temperature is server-specific: some accept up to 2.0, some
        # higher, some reject anything above 1.0. Report it instead of crashing.
        return f"(this server rejected temperature={temperature})"
    # `content` can be None/empty on a reasoning model that spent the budget thinking.
    return (response.choices[0].message.content or "").strip()


for temp in (0.0, 0.7, 1.3):
    print(f"\n=== temperature = {temp} ===")
    # Two samples each, so you can see repeatability (low) vs variety (high).
    print("1:", generate(temp))
    print("2:", generate(temp))

print("\nAt 0.0 the two samples should match (greedy, deterministic).")
print("At 1.3 they should differ a lot -- and may start to wander.")
print("(If your server rejects a temperature, the run reports it and keeps going.)")
