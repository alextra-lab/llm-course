"""
Section 4 - The context window: input + output must fit in one budget.

Two demonstrations:
  1. A small max_tokens ceiling cuts the OUTPUT short (finish_reason="length"). On a
     reasoning model the reply may even be EMPTY -- the few tokens were spent thinking.
  2. Asking for more tokens than the model's window allows is a hard error -- and
     the error message reveals the window size.

    python examples/04/context_budget.py
"""

import sys
from pathlib import Path

from openai import BadRequestError

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

# --- 1. Output ceiling: cap the answer, watch it get cut off -------------------
response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "List the planets of the solar system."}],
    max_tokens=10,
)
print("=== capped output ===")
print("finish_reason:", response.choices[0].finish_reason, "(='length' means not finished)")
print("content:", repr(response.choices[0].message.content),
      "(may be EMPTY on a reasoning model -- the budget went to thinking)")

# --- 2. Blow past the window on purpose to reveal its size ----------------------
print("\n=== exceeding the context window ===")
huge_input = "word " * 200_000  # almost certainly larger than the window
try:
    client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": huge_input}],
        max_tokens=50,
    )
    print("No error -- this endpoint's window is bigger than our test input!")
except BadRequestError as err:
    # The server tells you the maximum context length in the error message.
    print("Server rejected it. Here's why (note the max context length):")
    print(err)
