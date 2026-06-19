---
title: 'Tokens & the Context Window'
linkTitle: '4. Tokens & the Context Window'
weight: 4
---

**Goal:** make the word "token" concrete by *measuring* it yourself — through the server,
no local tokenizer — then turn it into the most important practical constraint you work
within: the **context window**. You'll write two small experiments and discover a model's
limit from the inside.

**Where this fits:** Section 2 showed you `response.usage.prompt_tokens`, and Section 3
showed *why* it has a floor (the chat template's overhead). Here you put it to work. This
lesson quietly underpins reasoning cost (Section 6) and dollar cost (Section 11) — both are
counted in tokens.

---

## What is a token, really?

Models don't read characters or words. They read **tokens**: sub-word chunks produced by
the model's tokenizer. Common words are usually one token; rare or long words split into
several; spaces, capitals, punctuation, emoji, and code all change the split.

A rough rule for English is **~4 characters per token** (~¾ of a word) — but it's only a
rule of thumb. The honest way to know is to *measure*, and you can do that with no local
tokenizer by reading `response.usage.prompt_tokens` *(int)* straight from the server.

### Write the measurement

Create **`work/count.py`**. We read `usage.prompt_tokens` for a few strings and compare
them. Notice we **don't** subtract an empty message to "isolate the text": the chat
template wraps an empty message differently from a real one, so that subtraction is
misleading on some models. Instead we read the raw counts and compare strings *to each
other* — the fixed template cost is the same in both, so the **difference** is the text:

```python
from common import get_client, MODEL

client = get_client()

def prompt_tokens(text: str) -> int:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": text}],
        max_tokens=1,                 # we only care about the INPUT count
    )
    return response.usage.prompt_tokens

samples = ["", "hello", "HELLO", "  hello", "hello world",
           "antidisestablishmentarianism", "🦜🦜🦜", "def f(n): return n*n"]

for s in samples:
    print(f"{prompt_tokens(s):>4}   {s!r}")
```

Run it:

```bash
python work/count.py
```

Read the numbers by **comparing rows**, not in isolation:

- The empty string `""` is not zero — that is the chat template's **fixed overhead**, paid
  on every request. It can be small, or surprisingly large, depending on the model.
- `"hello"` vs `"HELLO"` — casing *can* change the split. On many models it adds a token;
  on some it makes no difference for a given word. Measure, don't assume.
- `"hello"` vs `"  hello"` — leading whitespace is itself tokenized.
- `"hello"` vs `"hello world"` — the jump is the cost of the added word.
- `"antidisestablishmentarianism"` is one *word* but several *tokens*; emoji and code
  fragment into many.

> **Why measure through the server?** Tokenization is **model-specific** — `gpt-oss-120b`
> splits text differently than another model would. The server hosting the model has the
> *exact* right tokenizer, so its `usage` counts are ground truth.
> [`examples/04/count_tokens.py`](../examples/04/count_tokens.py) is the reference.

### The same code, different endpoints

The numbers you just measured are **specific to your model and server** — and they vary far
more than people expect. Here is the *same* code run against four different endpoints:

| Model (server) | Template overhead (`""`) | `max_tokens=16` reply | reasoning tokens? | reasoning text? | max temperature |
|---|---|---|---|---|---|
| `gpt-oss-120b` | 63 | empty (spent thinking) | reported | no | ≥ 3 |
| a reasoning Qwen | 10 | empty (spent thinking) | no | yes | ≥ 3 |
| an instruct Qwen | 12 | text, cut off | no | no | ≥ 3 |
| OpenAI `gpt-4o-mini` | 7 | text, cut off | reported | no | 2.0 |

One request, four endpoints, almost nothing in common: the fixed overhead ranges from 7 to
63 tokens; a tiny `max_tokens` returns **empty** text on the reasoning models but **partial**
text on the others (Section 2); the reasoning fields (Section 6) appear on some and not
others; and the highest accepted `temperature` (Section 5) differs. The takeaway for the
whole course: **don't assume — measure.** `python scripts/preflight.py` runs exactly these
checks against *your* endpoint and prints your own row.

---

## The context window

Every model has a **context window**: the maximum number of tokens it can handle in one
request. Crucially, that budget covers **both** the input *and* the output:

```
prompt_tokens  +  completion_tokens   ≤   context window
  (your input)     (the model's reply)        (a fixed limit)
```

`gpt-oss-120b`'s window is large (on the order of **128k tokens**), but "large" isn't
"infinite," and two things push against it:

- **Long inputs** — big documents, long chat histories (Section 13), retrieved context
  (Section 20). The more you put in, the less room for the answer.
- **`max_tokens`** — your cap on the *output*. If the model needs more room than you
  allow, it gets **cut off** and `response.choices[0].finish_reason` *(str)* becomes
  `"length"` (Section 2).

> **Reasoning tokens spend this budget too.** `gpt-oss-120b` thinks before it answers,
> and that thinking is generated tokens — it counts against the output side of the budget
> (and your bill). A hard question can burn a lot of window on thinking alone. That's the
> bridge to Section 6.

### Write the budget experiment

Create **`work/budget.py`**. It shows both edges of the budget — truncation, and blowing
past the window:

```python
from openai import BadRequestError
from common import get_client, MODEL

client = get_client()

# 1. Cap the output. On a reasoning model the few tokens are spent thinking, so the
#    reply may come back EMPTY -- but finish_reason still tells you it was cut off.
r = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "List the planets of the solar system."}],
    max_tokens=10,
)
print("finish_reason:", r.choices[0].finish_reason,
      "-> content:", repr(r.choices[0].message.content))

# 2. Exceed the window on purpose; the error reveals the limit.
huge = "word " * 200_000
try:
    client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": huge}], max_tokens=50
    )
    print("No error -- the window is bigger than our test input!")
except BadRequestError as err:
    print("Rejected. Note the maximum context length in this message:\n", err)
```

```bash
python work/budget.py
```

The first call is cut off (`finish_reason="length"`) — and on a reasoning model the
content comes back **empty**, because those 10 tokens were spent thinking rather than
answering (Section 2). The second call is rejected, and the error states the exact window
size. That error is a feature: it's the most reliable way to learn a model's limit.
*(Reference: [`examples/04/context_budget.py`](../examples/04/context_budget.py).)*

---

## How to think about it in practice

- **Budget before you build.** Estimate input tokens (instructions + context + history)
  and leave headroom for the output you need.
- **Set `max_tokens` deliberately.** Too low truncates; absurdly high risks the window.
- **Watch `finish_reason`.** `"length"` means the budget was too tight.
- **Long histories cost every time.** Each past turn you resend is paid for again
  (Section 13).

---

> **Security:** Untrusted text costs tokens *and* carries intent: a long pasted document can crowd out your instructions or smuggle its own. Budget the window, and never assume pasted-in text is just data.

## Challenges

1. **Find a surprising split.** In `work/count.py`, add your name, a URL, and a sentence
   in another language. *Success:* you find at least one string that uses far more tokens
   than its character count would suggest.
2. **Estimate then verify.** Guess a paragraph's token count, then measure it. *Success:*
   you can state how far off the ~¾-word rule was.
3. **Report the window.** Write a script that triggers the over-limit error and prints
   *just* the maximum context length (parse it out of the error message). *Success:* it
   prints a number near 128000.

---

## Recap

- Models read **tokens** (sub-word chunks); the split is model-specific and best
  **measured** via `response.usage.prompt_tokens` — no local tokenizer needed.
- The **context window** is a fixed budget shared by input and output:
  *prompt + completion ≤ window*.
- `max_tokens` caps output; too small → `finish_reason == "length"`.
- `gpt-oss-120b`'s window is large (~128k), but reasoning tokens and long histories spend
  it — and you pay for everything in it.

## Next

**Section 5 — Sampling Parameters:** now that you can measure what goes in and out, you'll
turn the knobs that control *how the model chooses its words* — and run experiments that
let you watch the output change.
