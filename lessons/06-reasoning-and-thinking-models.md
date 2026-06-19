---
title: 'Reasoning / "Thinking" Models'
linkTitle: '6. Reasoning / "Thinking" Models'
weight: 6
---

**Goal:** open up the thing `gpt-oss-120b` has been doing since Section 1 — *thinking
before it answers*. You'll write scripts that reveal the model's private reasoning, count
the **reasoning tokens** you pay for, and turn the `reasoning_effort` dial up and down to
see the trade-off.

**Where this fits:** this ties together the harmony template (Section 3), the `usage`
block (Section 2), and the token budget (Section 4). Reasoning tokens are why your
`completion_tokens` were sometimes bigger than the visible answer.

---

## What a reasoning model does

A normal model maps your prompt straight to an answer. A **reasoning model** first
generates a private chain of thought — working the problem, weighing options, checking
itself — and *then* writes the final answer. You usually see only the answer, but the
thinking happened, token by token.

Recall the **harmony** format from Section 3: that's exactly what its *channels* are for.
`gpt-oss-120b` writes its reasoning into an **analysis** channel and its reply into a
**final** channel. The server separates them, usually surfacing the reasoning as
`response.choices[0].message.reasoning_content` *(str)* alongside the normal
`response.choices[0].message.content` *(str)*.

> **This section needs a reasoning model on a reasoning-aware endpoint.** It's written for
> `gpt-oss-120b` served by vLLM. If you point the course at a plain (non-reasoning) chat
> model, or at an endpoint that doesn't surface the reasoning channel, the scripts still
> run — but `reasoning_content` comes back empty and `reasoning_tokens` reads `0`/`n/a`,
> and `reasoning_effort` may be ignored or rejected. **That's expected, not a bug:** the
> code handles all of these (note the `getattr(...)` and `try/except` below). To *see* the
> behavior this lesson describes, you need an actual reasoning model. Everything else in
> the course works the same on any OpenAI-compatible endpoint.

### See the thinking

Create **`work/reasoning.py`** with a classic trick question (the intuitive answer is
wrong — it's $0.05, not $0.10):

```python
from common import get_client, MODEL

client = get_client()

response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content":
        "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. "
        "How much does the ball cost?"}],
)
message = response.choices[0].message

reasoning = getattr(message, "reasoning_content", None)
print("=== reasoning (private thinking) ===")
print(reasoning or "(this endpoint did not expose reasoning_content)")

print("\n=== final answer ===")
print(message.content)

print("\n=== usage ===", response.usage)
```

Run it:

```bash
python work/reasoning.py
```

You'll see the model talk itself through *"if the ball is x, the bat is x + 1.00, so
2x + 1.00 = 1.10…"* and then give the correct **$0.05**. That self-correction is the
whole point of a reasoning model.

> Whether you can *see* the reasoning depends on the endpoint — some expose
> `reasoning_content`, some return only the final answer. The `getattr(...)` handles
> both. Either way, the thinking still happens, and you still pay for it. *(Reference:
> [`examples/06/reasoning.py`](../examples/06/reasoning.py).)*

---

## Reasoning tokens cost real money and budget

The reasoning is **generated text**, so:

- It counts toward **`response.usage.completion_tokens`** (Section 2). Where the endpoint
  breaks it out, you'll find it as
  `response.usage.completion_tokens_details.reasoning_tokens` *(int)*.
- It spends the **output side of the context budget** (Section 4).
- It is **billed like output** (Section 11).

So a reasoning model is a trade: better answers on hard problems, at the cost of more
tokens, latency, and money. The skill is spending that effort *where it pays off*.

---

## The `reasoning_effort` dial

`gpt-oss` lets you tune how hard it thinks with **`reasoning_effort`** *(str)*: `"low"`,
`"medium"`, or `"high"`. Lower = fewer reasoning tokens (faster, cheaper); higher = more
thorough (better on hard problems). Create **`work/effort.py`**:

```python
from common import get_client, MODEL

client = get_client()
problem = ("Three friends split a bill. Ana pays twice what Ben pays, and Ben pays $4 "
           "less than Cara. If the total is $59, how much did each pay?")

def ask(effort: str):
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": problem}],
        extra_body={"reasoning_effort": effort},   # forward an extra server field
    )
    details = getattr(r.usage, "completion_tokens_details", None)
    rtoks = getattr(details, "reasoning_tokens", "n/a") if details else "n/a"
    return rtoks, r.choices[0].message.content.strip()

for effort in ("low", "high"):
    try:
        rtoks, answer = ask(effort)
        print(f"=== {effort} === reasoning_tokens={rtoks}\n{answer[:160]}\n")
    except Exception as err:
        print(f"reasoning_effort={effort} not supported here: {err}\n")
```

```bash
python work/effort.py
```

Expect `"high"` to spend more reasoning tokens than `"low"`. Match the dial to the task:
**low** for simple lookups, **high** for multi-step math, logic, and tricky code.

> **`extra_body` is a useful escape hatch.** The SDK only has named parameters for fields
> it knows. vLLM accepts extras — `reasoning_effort`, `top_k`, `min_p` — and `extra_body`
> passes them straight through. Unsupported fields cause a server error, which is why the
> example wraps the call in `try/except`. *(Reference:
> [`examples/06/reasoning_effort.py`](../examples/06/reasoning_effort.py).)*

---

## Don't feed the thinking back in

A rule for multi-turn conversations (Section 13): when you continue a conversation, send
back the **final answer**, not the reasoning. The reasoning was scratch work — it's
large, it's not meant to be re-consumed, and resending it just burns context and money.
Keep the `assistant` turn's `content`; drop its `reasoning_content`.

---

> **Security:** Reasoning tokens can contain intermediate guesses, scratch work, even quoted secrets. Don't render raw reasoning to end users — show the final answer.

## Challenges

1. **Catch a self-correction.** Run `work/reasoning.py` on a different trick question and
   find where the reasoning considers and rejects a wrong answer. *Success:* you can
   quote the moment it course-corrects.
2. **Measure the tax.** Print `completion_tokens` next to `len(message.content)` for a
   hard prompt. *Success:* you can state roughly how many tokens were thinking vs answer.
3. **Match effort to task.** Add `"medium"` to `work/effort.py`, and also run a trivial
   prompt ("What's 2+2?") at `"high"`. *Success:* you can argue which effort each task
   *should* use, and what the wrong choice cost.

---

## Recap

- A reasoning model **thinks first** (the harmony *analysis* channel from Section 3), then
  answers; the thinking often surfaces as `response.choices[0].message.reasoning_content`.
- Reasoning is generated text: it's in `completion_tokens`, spends the **context budget**
  (Section 4), and is **billed** (Section 11).
- **`reasoning_effort`** *(str: low/medium/high)* trades answer quality against cost and
  latency.
- In multi-turn chats, send back the final answer, **not** the reasoning.

## Next

**Section 7 — Handling & Validating Responses:** text you can read is not data you can
trust. You'll make the model return structured JSON, constrain it to a schema, and
validate it with Pydantic before your code relies on it.
