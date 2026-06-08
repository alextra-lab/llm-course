"""
Section 6 - Structured output: JSON mode.

`response_format={"type": "json_object"}` tells the server to emit valid JSON.
It guarantees the output PARSES -- but not that it has the fields you wanted, so
you still have to ask for those in the prompt and check them yourself.

    python examples/06/json_mode.py
"""

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {
            "role": "user",
            # In json_object mode you must still describe the shape you want.
            "content": "Extract the person as JSON with keys name (string) and "
            "age (integer): 'Maria is 34 years old.'",
        }
    ],
    response_format={"type": "json_object"},
)

raw = response.choices[0].message.content
print("=== raw text the model returned ===")
print(raw)

# It parses because of json_object mode -- but WE are responsible for the shape.
data = json.loads(raw)
print("\n=== parsed ===")
print("name:", data.get("name"))
print("age :", data.get("age"))
