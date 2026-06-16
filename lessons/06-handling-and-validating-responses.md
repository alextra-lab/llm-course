# Section 6 — Handling & Validating Responses (Structured Output)

**Goal:** stop treating model output as text you eyeball and start treating it as **data
your code can rely on**. You'll write scripts that go from "the model returned
something JSON-ish" to "the model returned valid JSON, constrained to a schema, validated
into a typed object." The tools: JSON mode, schema-constrained output, and **Pydantic**.

**Where this fits:** so far you've printed `content` and read it yourself. The moment a
*program* consumes the output, free-form text is a liability. This is also a prerequisite
for tool calling (Section 13) and agents (Section 22).

---

## Why free text is a bug waiting to happen

Ask for a name and age, planning to parse it, and the model might return
`Sure! Maria is 34.`, or `{"name": "Maria", "age": "thirty-four"}`, or perfect JSON
wrapped in ```` ```json ```` fences. Your `json.loads` works Monday and crashes Tuesday.
The fix removes ambiguity at three levels:

1. **Make it valid JSON** — JSON mode.
2. **Make it the right *shape*** — a JSON schema.
3. **Verify before you trust it** — Pydantic validation.

---

## Level 1: JSON mode

`response_format={"type": "json_object"}` tells the server to emit syntactically valid
JSON — no prose, no fences. Create **`work/json_mode.py`**:

```python
import json
from common import get_client, MODEL

client = get_client()

response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content":
        "Extract the person as JSON with keys name (string) and age (integer): "
        "'Maria is 34 years old.'"}],
    response_format={"type": "json_object"},
)

raw = response.choices[0].message.content
print("raw:", raw)
data = json.loads(raw)                 # this parses, reliably
print("name:", data.get("name"), "| age:", data.get("age"))
```

```bash
python work/json_mode.py
```

This guarantees the text **parses**. It does *not* guarantee the keys or types you wanted
— notice you still describe them in the prompt and still call `.get(...)` and hope. Good,
but not enough. *(Reference: [`examples/06/json_mode.py`](../examples/06/json_mode.py).)*

---

## Levels 2 & 3: schema-constrained, validated with Pydantic

The real upgrade is handing the server a **JSON schema** so it constrains generation to
match — right keys, right types. And the cleanest way to author a schema *and* check the
result is **Pydantic**, the standard data-validation library in modern Python (it's in
your `requirements.txt`).

Create **`work/schema.py`**:

```python
from pydantic import BaseModel, ConfigDict
from common import get_client, MODEL

client = get_client()

# 1. Describe the shape ONCE, as types.
class Person(BaseModel):
    # extra="forbid" -> schema gets "additionalProperties": false, which strict
    # mode requires (some servers, e.g. OpenAI, reject the schema without it).
    model_config = ConfigDict(extra="forbid")

    name: str
    age: int
    hobbies: list[str]

# 2. Pydantic generates the JSON schema for us.
schema = Person.model_json_schema()

response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content":
        "Maria is 34. She enjoys climbing, baking, and chess."}],
    response_format={
        "type": "json_schema",
        "json_schema": {"name": "Person", "schema": schema, "strict": True},
    },
)

raw = response.choices[0].message.content
print("raw:", raw)

# 3. Validate + parse into a typed object. Raises if anything is off.
person = Person.model_validate_json(raw)
print(person)
print("first hobby:", person.hobbies[0])
```

```bash
python work/schema.py
```

Two things are happening, both important:

- **Constrained decoding** (server side): the endpoint uses the schema to *prevent*
  non-conforming tokens. `age` *(int)* will be a number; `hobbies` *(list[str])* a list.
  Far more reliable than asking nicely in the prompt.
- **Validation** (client side): `Person.model_validate_json(...)` parses into a typed
  `Person` and **raises** if anything is wrong. Defense in depth — you never hand
  unverified data to the rest of your program.

Notice the symmetry with Section 2: there you *read* `str`/`int` types off a response;
here you *declare* them and force the output to comply.

> **Prefer a dataclass?** You can describe the same shape with `@dataclass` and validate
> by hand. Pydantic earns its place by giving schema generation *and* validation *and*
> typed objects from one definition. We'll reuse this to define tools in Section 13.
> *(Reference: [`examples/06/json_schema_pydantic.py`](../examples/06/json_schema_pydantic.py).)*

---

## Still check `finish_reason`

If the response is truncated (`finish_reason == "length"`), the JSON is **cut off and
invalid**, and your parse will raise. When you depend on structured output, leave enough
`max_tokens` and treat a parse failure as a real error path (Section 8), not a surprise.

> **Reasoning + structured output.** With `gpt-oss-120b` the model still reasons
> privately; only the **final** channel is constrained to your schema. You get the
> benefit of thinking *and* a clean JSON answer.

---

> **Security:** Validate *before* you act. The model chose those fields, so parse into a schema (Pydantic) and reject anything malformed — never feed unchecked model output straight into code, a query, or a database.

## Challenges

1. **Expose JSON mode's blind spot.** In `work/json_mode.py`, remove the key names from
   the prompt. *Success:* you get valid JSON that *doesn't* have your `name`/`age` shape —
   proving valid JSON ≠ the right shape.
2. **Add a constraint.** Give `Person` an `age: int = Field(ge=0, le=130)` (import
   `Field` from pydantic), regenerate the schema, and feed it an impossible age.
   *Success:* validation rejects the bad value.
3. **Force a failure.** Add `max_tokens=5` to the schema call. *Success:* the truncated
   JSON raises on `model_validate_json`, and `finish_reason` is `"length"`.
4. **Nest it.** Add `address: Address` where `Address` is another `BaseModel`. *Success:*
   the nested object comes back correctly typed (`person.address.city`).

---

## Recap

- Free-form text is unsafe for programs to consume — constrain and validate it.
- **JSON mode** (`json_object`) guarantees valid JSON, not the right shape.
- **Schema-constrained output** (`json_schema`) forces the shape; author the schema with
  **Pydantic** via `model_json_schema()`.
- **Validate** with `model_validate_json` for a typed object and a real error when
  something's wrong — and keep checking `finish_reason`.

## Next

**Section 7 — Blocking vs Streaming:** every call so far waited for the complete answer.
You'll build the other mode — streaming tokens as they're generated — first as raw
server-sent events, then via the SDK.
