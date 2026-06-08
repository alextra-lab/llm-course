"""
Section 6 - Structured output: schema-constrained + validated.

Two upgrades over plain JSON mode:
  1. We hand the server a JSON SCHEMA, so the output is constrained to match it
     (the server won't emit the wrong shape).
  2. We define that schema with Pydantic, and validate the result with Pydantic
     too -- so by the time our code touches the data, it's typed and checked.

    python examples/06/json_schema_pydantic.py
"""

import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()


# 1. Describe the shape we want, once, as a Pydantic model.
class Person(BaseModel):
    name: str
    age: int
    hobbies: list[str]


# 2. Pydantic generates the JSON schema for us.
schema = Person.model_json_schema()

response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {
            "role": "user",
            "content": "Maria is 34. She enjoys climbing, baking, and chess.",
        }
    ],
    response_format={
        "type": "json_schema",
        "json_schema": {"name": "Person", "schema": schema, "strict": True},
    },
)

raw = response.choices[0].message.content
print("=== raw (already constrained to the schema) ===")
print(raw)

# 3. Validate + parse into a typed object. Raises if anything is off.
person = Person.model_validate_json(raw)
print("\n=== validated Person object ===")
print(person)
print("first hobby:", person.hobbies[0])
