"""
Section 23 - Evaluation: golden tests for things with a known answer.

When a task has a checkable answer, treat it like a unit test: a fixed set of
inputs with expected substrings, run at temperature 0, with a pass rate at the
end. Run this whenever you change a prompt or model to catch regressions.

    python examples/23/golden_test.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

# (question, substring the answer must contain)
CASES = [
    ("What is the capital of France? One word.", "Paris"),
    ("What is 2 + 2? Reply with just the number.", "4"),
    ("Name the largest planet in our solar system. One word.", "Jupiter"),
]


def run(question: str) -> str:
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": question}],
        temperature=0,                # deterministic, so the test is stable
    )
    return r.choices[0].message.content


passed = 0
for question, expected in CASES:
    output = run(question)
    ok = expected.lower() in output.lower()
    passed += ok
    print(f"[{'PASS' if ok else 'FAIL'}] {question}\n        expected {expected!r}, got: {output!r}")

print(f"\n{passed}/{len(CASES)} passed")
