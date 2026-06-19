"""
Section 6 - Reasoning: the `reasoning_effort` dial.

gpt-oss supports a reasoning effort level (low / medium / high). Higher effort
means the model thinks longer -- usually better on hard problems, but more
reasoning tokens (slower and more expensive). We pass it via `extra_body`, which
forwards extra fields to the server without the SDK needing to know about them.

    python examples/06/reasoning_effort.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

problem = (
    "Three friends split a restaurant bill. Ana pays twice what Ben pays, and "
    "Ben pays $4 less than Cara. If the total is $59, how much did each pay?"
)


def ask(effort: str):
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": problem}],
        extra_body={"reasoning_effort": effort},
    )
    usage = response.usage
    details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = getattr(details, "reasoning_tokens", None) if details else "n/a"
    return reasoning_tokens, usage.completion_tokens, response.choices[0].message.content


for effort in ("low", "high"):
    try:
        rtoks, ctoks, answer = ask(effort)
        print(f"=== reasoning_effort = {effort} ===")
        print(f"reasoning_tokens={rtoks}  completion_tokens={ctoks}")
        print("answer:", answer.strip()[:200])
        print()
    except Exception as err:  # endpoint may not accept this field
        print(f"reasoning_effort={effort} not supported here: {err}\n")

print("Expect 'high' to use more reasoning tokens than 'low'.")
