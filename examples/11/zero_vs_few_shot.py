"""
Section 11 - Prompt engineering: zero-shot vs few-shot via the assistant role.

A "shot" is a worked example you show the model. You provide them as alternating
user/assistant messages BEFORE the real question -- the same `assistant` role you
met in Section 1, now used to teach by example. We classify a support message
with no examples (zero-shot) and with three (few-shot) and compare.

    python examples/11/zero_vs_few_shot.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

SYSTEM = (
    "Classify the user's message as exactly one of: BILLING, TECHNICAL, OTHER. "
    "Reply with only the single label word."
)

# Each shot is (example_user_message, ideal_assistant_label).
SHOTS = [
    ("My invoice charged me twice this month", "BILLING"),
    ("The app crashes every time I log in", "TECHNICAL"),
    ("Do you have a mobile version?", "OTHER"),
]


def classify(message: str, shots=()) -> str:
    messages = [{"role": "system", "content": SYSTEM}]
    for example_in, example_out in shots:
        messages.append({"role": "user", "content": example_in})
        messages.append({"role": "assistant", "content": example_out})  # the "shot"
    messages.append({"role": "user", "content": message})
    response = client.chat.completions.create(
        model=MODEL, messages=messages, temperature=0, max_tokens=5  # temp 0 = fair compare
    )
    return response.choices[0].message.content.strip()


test = "I think I was billed for a plan I cancelled"
print("zero-shot:", classify(test))
print("few-shot :", classify(test, SHOTS))
print("\nFew-shot examples pin down the EXACT label format and edge cases.")
