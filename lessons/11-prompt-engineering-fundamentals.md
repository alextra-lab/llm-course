# Section 11 — Prompt Engineering Fundamentals

**Goal:** learn the handful of prompt techniques that reliably move output quality —
zero/one/few-shot examples, clear instructions, delimiters, and output shaping — and
build them yourself so you can feel the difference. You'll also see how prompt-time
"chain of thought" relates to our model's *native* reasoning (Section 5).

**Where this fits:** this opens the advanced arc. You've controlled the model with
*parameters* (Sections 4–5); now you control it with *words*. Everything here reuses the
roles from Section 1 and the fair-comparison tricks (`temperature=0`, `seed`) from
Section 4.

---

## Zero-shot, one-shot, few-shot

A **"shot"** is a worked example you show the model. The names just count examples:

- **Zero-shot** — instructions only, no examples.
- **One-shot / few-shot** — one or several examples *before* the real question.

The clean way to provide examples is the **`assistant` role** from Section 1: you write
alternating `user` / `assistant` turns showing the desired input→output, then the real
`user` message. The model treats your examples as the pattern to continue.

Build it. Create **`work/shots.py`**:

```python
from common import get_client, MODEL

client = get_client()

SYSTEM = ("Classify the user's message as exactly one of: BILLING, TECHNICAL, OTHER. "
          "Reply with only the single label word.")

SHOTS = [
    ("My invoice charged me twice this month", "BILLING"),
    ("The app crashes every time I log in", "TECHNICAL"),
    ("Do you have a mobile version?", "OTHER"),
]

def classify(message, shots=()):
    messages = [{"role": "system", "content": SYSTEM}]
    for example_in, example_out in shots:
        messages.append({"role": "user", "content": example_in})
        messages.append({"role": "assistant", "content": example_out})   # the "shot"
    messages.append({"role": "user", "content": message})
    r = client.chat.completions.create(
        model=MODEL, messages=messages, temperature=0, max_tokens=5,      # fair compare
    )
    return r.choices[0].message.content.strip()

test = "I think I was billed for a plan I cancelled"
print("zero-shot:", classify(test))
print("few-shot :", classify(test, SHOTS))
```

```bash
python work/shots.py
```

Both probably get the label right, but few-shot pins down the **exact format** (one
uppercase word, no punctuation, no explanation) and teaches **edge cases** by example.
That reliability is why few-shot is the workhorse of prompt engineering. *(Reference:
[`examples/11/zero_vs_few_shot.py`](../examples/11/zero_vs_few_shot.py).)*

> **Why `temperature=0` here?** You're comparing prompts, so you want the *prompt* to be
> the only thing that changes. Pinning temperature (Section 4) keeps the comparison fair.

---

## Instructions, delimiters, and output shaping

Three habits that punch above their weight:

1. **Be specific about the task and the output.** "Summarize" is vague; "summarize as
   exactly 3 bullet points, each under 10 words, output only the bullets" is a
   contract.
2. **Delimit the data.** Wrap user-supplied text in clear markers (`<doc>…</doc>`,
   triple quotes, XML-ish tags) so the model can tell your *instructions* from the
   *data*. This also matters for safety — Section 17.
3. **Show the shape you want.** Ask for JSON (Section 6), a table, or bullets — and say
   so explicitly.

Create **`work/structure.py`**:

```python
from common import get_client, MODEL

client = get_client()
document = ("The library opens at 9am on weekdays and 10am on weekends. Members can "
            "borrow up to 10 books for 3 weeks. Late returns are fined 20c per day.")

prompt = f"""Summarize the document between <doc> tags as exactly 3 bullet points.
Each bullet must be under 10 words. Output only the bullets, no preamble.

<doc>
{document}
</doc>"""

r = client.chat.completions.create(
    model=MODEL, messages=[{"role": "user", "content": prompt}], temperature=0,
)
print(r.choices[0].message.content)
```

```bash
python work/structure.py
```

Tighten or loosen the rules and watch the output obey. *(Reference:
[`examples/11/structure.py`](../examples/11/structure.py).)*

---

## "Think step by step" vs native reasoning

A classic trick is to ask the model to **reason out loud** ("think step by step before
answering"). It works on ordinary models because it forces them to generate intermediate
steps. But `gpt-oss-120b` is a **reasoning model** (Section 5) — it *already* thinks
privately before answering. So:

- You rarely need to *ask* it to think; it does. Adding "think step by step" mostly
  duplicates what `reasoning_effort` controls.
- What you *do* control is **how much** (the `reasoning_effort` dial) and **what to think
  about** (a clear task and constraints).

For non-reasoning models you'd lean on chain-of-thought prompting; for ours, spend your
prompt budget on clear instructions and good examples instead.

---

## Decompose hard tasks

When a task is big, don't ask for everything in one shot. Break it into steps — extract,
then transform, then format — either as separate calls or as explicit numbered steps in
one prompt. Smaller, well-defined steps are easier for the model to get right and easier
for *you* to validate (Section 19).

---

## Challenges

1. **Make few-shot win.** Find an input your zero-shot classifier mislabels but
   few-shot gets right (try ambiguous messages). *Success:* a concrete case where the
   examples change the answer.
2. **Format lock-in.** Rewrite `work/structure.py` to output a strict JSON object
   (combine with Section 6's `response_format`). *Success:* the output parses with
   `json.loads`.
3. **Decompose.** Take "extract all dates from this text and return them sorted as
   ISO-8601" and split it into two calls (extract, then normalize+sort). *Success:* the
   two-step version is more reliable than one shot on messy input.

---

## Recap

- A **shot** is an example; provide few-shot examples as `user`/`assistant` turns to pin
  format and edge cases. Compare prompts at `temperature=0`.
- **Specific instructions, delimiters around data, and explicit output formats** are the
  cheapest quality wins.
- Our model reasons natively, so prefer clear instructions over "think step by step";
  use `reasoning_effort` to control depth.
- **Decompose** hard tasks into smaller, verifiable steps.

## Next

**Section 12 — Conversation State & Memory:** the API is stateless — it remembers
nothing between calls. You'll build a multi-turn chat loop yourself and learn to keep its
history inside the token budget.
