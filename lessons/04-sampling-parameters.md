---
title: 'Sampling Parameters (Seeing the Effect)'
linkTitle: '4. Sampling Parameters (Seeing the Effect)'
weight: 4
---

**Goal:** understand the knobs that control *how the model chooses each word* — and
**watch them work** by writing the experiments yourself. By the end you'll know what
`temperature`, `top_p`, and `seed` do, when to use each, and why cranking temperature up
invites hallucination.

**Where this fits:** Sections 1–3 were about *what* you send and receive. This is the
first lesson where you shape the model's *behavior*.

---

## How a model picks the next token

Here's the idea everything hangs on. At each step the model doesn't output a word — it
outputs a **probability distribution** over all possible next tokens: maybe `"blue"` at
60%, `"dark"` at 12%, `"quiet"` at 8%, and a long tail of everything else.

**Sampling** turns that distribution into an actual choice. The parameters below all
change *how adventurous* the choice is — from "always take the most likely token" to
"happily pick from the unlikely tail."

---

## The knobs

### `temperature` (the main one)

Temperature (`float`, usually 0.0–2.0) rescales the distribution before sampling:

- **`0.0`** — greedy. Always take the most likely token. **Deterministic.** Best for
  facts, extraction, classification, code.
- **`~0.7`** — balanced. Some variety, still coherent. Good for chat and general writing.
- **`>1.0`** — flattens the distribution, making unlikely tokens more probable. More
  creative — and, far enough, **incoherent**. This is where the model wanders and makes
  things up.

### The rest, briefly

- **`top_p`** (`float`, 0.0–1.0) — *nucleus sampling*: keep the smallest set of tokens
  whose probabilities sum to `p`, then sample from those. An alternative lever to
  temperature — **change one or the other, not both.**
- **`top_k`** *(int)* — keep only the `k` most likely tokens.
- **`min_p`** *(float)* — keep tokens above a minimum probability relative to the top one.
- **`frequency_penalty` / `presence_penalty`** *(float)* — push the model away from
  repeating tokens it already used.
- **`seed`** *(int)* — pin the random choices so a run is reproducible (below).

**Practical default:** start at `temperature=0` whenever correctness matters; raise it
only when you *want* variety. Change one knob, observe, repeat.

---

## Watch temperature work

Reading about it isn't the same as seeing it. Create **`work/temperature.py`**:

```python
from common import get_client, MODEL

client = get_client()
prompt = [{"role": "user", "content": "In one sentence, describe a city at night."}]

def generate(temperature: float) -> str:
    r = client.chat.completions.create(
        model=MODEL, messages=prompt, temperature=temperature, max_tokens=60,
    )
    return r.choices[0].message.content.strip()

for temp in (0.0, 0.7, 1.3):
    print(f"\n=== temperature = {temp} ===")
    print("1:", generate(temp))     # two samples each, so you can compare
    print("2:", generate(temp))
```

Run it (a few times):

```bash
python work/temperature.py
```

What to look for:

- At **0.0**, the two samples are **identical** — greedy decoding is deterministic.
- At **0.7**, they differ but both read well.
- At **1.3**, they differ a lot, and you may see odd word choices or the sentence losing
  the thread. **That drift is the same mechanism behind hallucination:** higher
  temperature means more willingness to pick a low-probability (often wrong) token. When
  people say "lower the temperature to reduce hallucinations," this is what they mean.

> **Reasoning-model note.** Temperature affects both `gpt-oss-120b`'s thinking and its
> final text. For factual tasks, low temperature plus the model's own reasoning
> (Section 5) is a strong combination. *(Reference:
> [`examples/04/temperature_sweep.py`](../examples/04/temperature_sweep.py).)*

---

## Reproducibility with `seed`

Sometimes you want randomness *and* the ability to reproduce a specific run — for a test,
a bug report, or a cache key. Create **`work/seed.py`**:

```python
from common import get_client, MODEL

client = get_client()
prompt = [{"role": "user", "content": "Invent a name for a coffee shop."}]

def generate(seed: int) -> str:
    r = client.chat.completions.create(
        model=MODEL, messages=prompt, temperature=1.0, seed=seed, max_tokens=20,
    )
    return r.choices[0].message.content.strip()

print("seed 42:", generate(42))
print("seed 42:", generate(42))     # should match the line above
print("seed 99:", generate(99))     # should differ
```

```bash
python work/seed.py
```

Same `seed` *(int)* + same inputs → same output. Change the seed → different output.

> **Caveat — seeds are best-effort, not a guarantee.** Server batching, load, or a config
> change can still nudge results. Your clue that the underlying setup changed is
> `response.system_fingerprint` (Section 2): if it changes, identical inputs may diverge.
> *(Reference: [`examples/04/seed_demo.py`](../examples/04/seed_demo.py).)*

---

> **Security:** A fixed `seed` and low temperature make behavior reproducible — which is what lets a safety test reliably catch a regression. Non-determinism hides bugs.

## Challenges

1. **Find the breaking point.** In `work/temperature.py`, add `1.8` and `2.0` to the
   sweep. *Success:* you can name the temperature at which output becomes nonsense.
2. **Swap the lever.** Write a version that sweeps `top_p` over `0.3, 0.7, 1.0` at fixed
   `temperature=1.0`. *Success:* you can describe how `top_p` feels different from
   `temperature`.
3. **Determinism for facts.** Ask "What is the capital of Australia?" five times at
   `temperature=0` and five times at `1.5`. *Success:* the `temperature=0` answers are
   identical and correct; the hot ones vary.

---

## Recap

- The model emits a **probability distribution**; sampling parameters decide how boldly
  you pick from it.
- **`temperature`** *(float)*: `0` = deterministic/factual; `~0.7` = balanced; `>1` =
  creative and, eventually, incoherent — the dial most tied to hallucination.
- **`top_p`/`top_k`/`min_p`** are alternative truncation levers — change one, not several.
- **`seed`** *(int)* makes a run reproducible (best-effort; watch `system_fingerprint`).

## Next

**Section 5 — Reasoning / "Thinking" Models:** we open up what `gpt-oss-120b` has been
doing all along — thinking before it answers — and look at reasoning tokens, the
`reasoning_effort` dial, and what it costs.
