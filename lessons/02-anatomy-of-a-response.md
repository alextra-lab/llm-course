---
title: 'Anatomy of a Response'
linkTitle: '2. Anatomy of a Response'
weight: 2
---

**Goal:** before turning any knobs, get comfortable with what the server *returns*.
You'll write a small script that prints a whole response and pulls it apart field by
field, so you know exactly where every value lives — and you'll see why `finish_reason`
and `usage` matter for everything that follows.

**Where this fits:** in Section 1 you made a call and grabbed one field
(`response.choices[0].message.content`). Now you'll read the whole envelope it came in.

---

## First, write a helper you'll reuse

You built the client by hand twice in Section 1. Let's factor that into one small file so
you stop repeating it. Create **`work/common.py`**:

```python
import os
from openai import OpenAI

MODEL = os.environ["MODEL"]

def get_client() -> OpenAI:
    return OpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )
```

Now any script you put in `work/` can use it with a plain import — because Python adds
the script's own folder (`work/`) to its search path, and `common.py` is right there:

```python
from common import get_client, MODEL
```

> **Reference:** [`examples/common.py`](../examples/common.py) is the same helper. The
> only difference is two extra lines of `sys.path` setup — the reference scripts live in
> numbered folders (`examples/02/`, …), so they need help finding `common.py`. Your flat
> `work/` folder doesn't.

---

## Write a script that shows the whole response

Create **`work/inspect_response.py`** (we use this longer name on purpose: a file called
`inspect.py` would shadow Python's built-in `inspect` module — which the `openai` library
imports — and your script would crash on import before it ran):

```python
import json
from common import get_client, MODEL

client = get_client()

response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": "You are a concise assistant."},
        {"role": "user", "content": "Name three primary colors."},
    ],
)

# model_dump() turns the SDK's typed object back into a plain dict -- the same
# shape as the raw JSON from Section 1. Great for inspection.
print(json.dumps(response.model_dump(), indent=2, default=str))
```

Run it:

```bash
python work/inspect_response.py
```

You'll get the full object. Here's a trimmed version of what you're looking at:

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1731000000,
  "model": "openai/gpt-oss-120b",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "Red, blue, and yellow." },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 31, "completion_tokens": 7, "total_tokens": 38 },
  "system_fingerprint": "fp_..."
}
```

---

## The shape, field by field

The thing that trips people up is the **nesting**: `choices` is a *list*, and the parts
you actually want — `message`, `finish_reason` — live *inside each item of that list*,
not at the top level. Here's the same object as a tree, labelled with the exact dot-path
you'd use to reach each value, and its **type** (note the types — you'll declare these
exact ones in Section 7):

```
response                                              object
├─ response.id                          str     "chatcmpl-abc123"
├─ response.created                     int     1731000000   (Unix timestamp)
├─ response.model                       str     "openai/gpt-oss-120b"
├─ response.system_fingerprint          str     "fp_..."
├─ response.choices                     list    [ ... ]  (a list of objects; usually 1)
│   └─ response.choices[0]              object  the first choice
│       ├─ response.choices[0].index            int     0
│       ├─ response.choices[0].finish_reason    str     "stop"
│       └─ response.choices[0].message          object  the reply
│           ├─ response.choices[0].message.role     str   "assistant"
│           └─ response.choices[0].message.content  str   "Red, blue, and yellow."
└─ response.usage                       object
    ├─ response.usage.prompt_tokens       int   31
    ├─ response.usage.completion_tokens   int   7
    └─ response.usage.total_tokens        int   38
```

Read it level by level (type in parentheses).

**Top level** — metadata about the whole call:

- **`response.id`** *(str)* — a unique id for this completion. Worth logging (Section 10);
  it's what you quote when something looks wrong.
- **`response.created`** *(int)* — a Unix timestamp (seconds since 1970).
- **`response.model`** *(str)* — the model that actually answered.
- **`response.system_fingerprint`** *(str)* — an opaque id for the exact server/model
  config; when it changes, outputs can change even with identical inputs (Section 10).
- **`response.usage`** *(object)* — token accounting (its own section below).
- **`response.choices`** *(list of objects)* — the list of answers. One item by default;
  ask for several with the `n` parameter and you get several items. Everything you read
  lives *inside* an item — which is why you write `response.choices[0]`, "the first
  choice."

**Inside a choice** — `response.choices[0]` *(object)*:

- **`response.choices[0].index`** *(int)* — its position in the list (`0`, `1`, …).
- **`response.choices[0].finish_reason`** *(str)* — why *this* choice stopped. See below.
- **`response.choices[0].message`** *(object)* — the reply object itself.

**Inside the message** — `response.choices[0].message` *(object)*:

- **`response.choices[0].message.role`** *(str)* — `"assistant"` for a reply.
- **`response.choices[0].message.content`** *(str)* — the text you printed in Section 1.
- **`response.choices[0].message.reasoning_content`** *(str, or absent)* — present for
  our reasoning model when the endpoint exposes it (Section 6).

Now add these three lines to the end of `work/inspect_response.py` and rerun — confirm you can
reach each one:

```python
print("content       :", response.choices[0].message.content)
print("finish_reason :", response.choices[0].finish_reason)
print("usage         :", response.usage)
```

> With the SDK these dot-paths *are* the code (typed attribute access). With the raw
> `requests` version from Section 1 you'd use dict keys instead:
> `response["choices"][0]["message"]["content"]`.

---

## `finish_reason`: always check it

Reading `response.choices[0].message.content` and stopping is a bug waiting to happen,
because the content can be **incomplete** and nothing in the text will tell you. Its
sibling field, `response.choices[0].finish_reason` *(str)*, is how the server tells you
*why* it stopped:

| Value | Meaning |
|---|---|
| `"stop"` | The model finished naturally. The normal, happy case. |
| `"length"` | It hit your `max_tokens` (or the context limit) and was **cut off**. |
| `"tool_calls"` | It wants to call a tool instead of replying (Section 14). |
| `"content_filter"` | Output was blocked by a safety filter, if configured. |

Prove the dangerous one to yourself. Create **`work/finish.py`**:

```python
from common import get_client, MODEL

client = get_client()
prompt = [{"role": "user", "content": "Write a short paragraph about the ocean."}]

def ask(max_tokens):
    response = client.chat.completions.create(
        model=MODEL, messages=prompt, max_tokens=max_tokens
    )
    choice = response.choices[0]
    return choice.finish_reason, choice.message.content or ""

reason, text = ask(300)          # generous ceiling -> finishes naturally
print(f"[300] {reason!r} ({len(text)} chars): {text}\n")

reason, text = ask(8)            # tiny ceiling -> not finished
print(f"[8] {reason!r} ({len(text)} chars): {text!r}")
```

```bash
python work/finish.py
```

The first call finishes on its own (`finish_reason="stop"`). The second hits the ceiling
and returns `finish_reason="length"` — **but look closely at the text it returns.** If
`gpt-oss-120b` (a reasoning model) is behind your endpoint, those 8 tokens are spent
*thinking*, so `content` comes back **empty**: `finish_reason="length"` with nothing to
show. Behind a model that doesn't think first, you'd instead see a sentence chopped off in
the middle. Either way the answer is **not complete**.

That is the real lesson: **`finish_reason` is the truth, not the text.** Never decide an
answer is done by looking at whether `content` is non-empty — a `"length"` result is
incomplete whether it is truncated *or* empty. **Rule of thumb:** if you need a complete
answer, check `finish_reason == "stop"` (or handle `"length"` deliberately). Why a tiny
budget can vanish into thinking is Section 6; for now, just trust the reason. We'll rely on
this in Sections 7 and 9.

> **Reference:** [`examples/02/inspect_response.py`](../examples/02/inspect_response.py)
> and [`examples/02/finish_reasons.py`](../examples/02/finish_reasons.py).

---

## `usage`: the number that pays the bills

```json
"usage": { "prompt_tokens": 31, "completion_tokens": 7, "total_tokens": 38 }
```

- **`response.usage.prompt_tokens`** *(int)* — how many tokens your input became *after
  templating* (Section 1).
- **`response.usage.completion_tokens`** *(int)* — how many tokens the model generated.
- **`response.usage.total_tokens`** *(int)* — the sum.

This is the most reused telemetry in the API. It's how you'll measure size (Section 4),
avoid the context limit (Section 4), log activity (Section 10), and compute **cost**
(Section 11).

> **Our reasoning model adds detail here.** Because `gpt-oss-120b` thinks before it
> answers, on many endpoints `usage` carries a `completion_tokens_details` object with a
> `reasoning_tokens` *(int)* count, and the message may include `reasoning_content`.
> Those thinking tokens are part of `completion_tokens` — you pay for them. That's
> Section 6; for now just notice if `completion_tokens` looks bigger than the visible
> answer would suggest.

> **Why the types matter.** Right now you're reading types *off* a response (so you know
> `prompt_tokens` is an `int` you can add up, and `content` is a `str`). In Section 7
> you'll flip this around and *declare* the types you expect (`name: str`, `age: int`,
> `hobbies: list[str]`) with Pydantic, forcing the model's output to conform. Same idea,
> applied to output instead of input.

---

> **Security:** Don't trust a response blindly. Check `finish_reason` first — a `length` cut-off means the answer (or JSON) is truncated, not complete — before you parse or act on `content`.

## Challenges

Write these in `work/` (extend or copy `work/inspect_response.py`).

1. **Ask for two answers.** Add `n=2` to the `create(...)` call and print
   `len(response.choices)` and both `choices[i].message.content`. *Success:* you get two
   distinct replies in the list.
2. **Force a cut-off and detect it.** Write a script that asks for a long answer with
   `max_tokens=12` and prints `"TRUNCATED"` only when `finish_reason == "length"`.
   *Success:* it prints `TRUNCATED`.
3. **Spot the thinking tax.** Print `completion_tokens` for a trivial question ("What is
   2+2?") and for a hard one ("Prove there are infinitely many primes."). *Success:* the
   hard one's `completion_tokens` is much larger — that gap is reasoning (Section 6).

---

## Recap

- A response is an envelope: `response.id`, `response.model`, a **list**
  `response.choices`, and `response.usage`.
- The reply is at `response.choices[0].message.content`; `model_dump()` gives the whole
  thing as a plain dict.
- **Always check `response.choices[0].finish_reason`.** `"stop"` is good; `"length"`
  means truncated.
- `response.usage` (prompt / completion / total tokens, all `int`) is the foundation for
  tokens, context, observability, and cost — and it hints at reasoning tokens.

## Next

**Section 3 — Chat Templates & Harmony:** before we count tokens, we'll look at the step
that turns your `messages` into the string the model actually reads — the chat template —
and see why even an empty message costs tokens.
