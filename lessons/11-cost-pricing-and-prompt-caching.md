---
title: 'Cost, Pricing & Prompt Caching'
linkTitle: '11. Cost, Pricing & Prompt Caching'
weight: 11
---

**Goal:** turn the token counts you've been logging into **money**, then write a
demonstration of **prompt caching** — the single biggest lever for making repeated work
cheaper and faster. This is the capstone of the foundations arc: it ties together `usage`
(Section 2), reasoning tokens (Section 6), and your logs (Section 10).

**Where this fits:** you can now measure everything that costs money. This lesson does the
arithmetic and shows the most effective way to reduce it.

---

## How LLM pricing works

The model is almost always billed **per token**, with two key rates:

- **Input (prompt) tokens** — what you send. Cheaper.
- **Output (completion) tokens** — what the model generates. **More expensive**, often
  several times the input rate.

Prices are usually quoted **per 1,000,000 tokens**. Two details for our setup:

- **Reasoning tokens are output tokens.** `gpt-oss-120b`'s thinking (Section 6) is part of
  `completion_tokens`, billed at the output rate. A model that thinks a lot costs more
  than its short visible answer suggests.
- **Cached input is discounted.** Many providers charge less for prompt tokens served from
  a cache (often ~half) — more below.

### Write the cost calculator

It's just multiplication. Create **`work/cost.py`** (set the prices to match your
endpoint):

```python
from common import get_client, MODEL

client = get_client()

# Illustrative prices, USD per 1,000,000 tokens. Replace with yours.
PRICE_INPUT = 0.15
PRICE_OUTPUT = 0.60
PRICE_CACHED_INPUT = 0.075          # cached prompt tokens, often ~half price

def cost_usd(usage) -> float:
    details = getattr(usage, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    fresh_input = usage.prompt_tokens - cached
    return (fresh_input / 1e6 * PRICE_INPUT
            + cached / 1e6 * PRICE_CACHED_INPUT
            + usage.completion_tokens / 1e6 * PRICE_OUTPUT)

r = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "Explain what an API is, in two sentences."}],
)
print("usage:", r.usage)
print(f"this call: ${cost_usd(r.usage):.6f}  |  x1000 calls: ${cost_usd(r.usage)*1000:.2f}")
```

```bash
python work/cost.py
```

Combine this with Section 10's logging and you can sum cost per request, per user, per day
— exactly. *(Reference: [`examples/11/cost.py`](../examples/11/cost.py).)*

> **Self-hosted ≠ free.** If *you* run the vLLM server there's no per-token invoice, but
> there's GPU time — the same idea in different units. Cost per token still tells you
> whether a feature is affordable at scale.

---

## Prompt caching: the big lever

Here's the highest-leverage optimization in the whole arc. Many requests share a long,
**identical prefix** — a big system prompt, few-shot examples, a reference document —
followed by a small part that changes (the user's question). Processing that long prefix
is most of the input work. **Prompt caching** lets the server reuse the computation it
already did for a prefix it has seen. vLLM does this automatically ("automatic prefix
caching"). The payoff: **faster** (less to reprocess) and **cheaper** (cached input is
discounted — and you can *see* it).

### Watch the cache warm up

Create **`work/cache.py`**. It sends the same long prefix twice with different questions:

```python
import time
from common import get_client, MODEL

client = get_client()

big_prefix = "You are a helpful assistant. Reference notes:\n" + (
    "The Apollo program ran from 1961 to 1972. " * 400)      # long, STABLE prefix

def ask(question):
    start = time.perf_counter()
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": big_prefix},   # shared prefix
                  {"role": "user", "content": question}],       # the part that varies
        max_tokens=20,
    )
    ms = round((time.perf_counter() - start) * 1000)
    details = getattr(r.usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details else None
    return cached, r.usage.prompt_tokens, ms

print("cold:", ask("When did Apollo end?"))
print("warm:", ask("When did Apollo begin?"))
```

```bash
python work/cache.py
```

The second call should report a large `cached_tokens` *(int)* and usually a lower latency,
even though `prompt_tokens` is the same — most of it was free-ish the second time.
*(Reference: [`examples/11/prompt_cache.py`](../examples/11/prompt_cache.py).)*

---

## How to actually get cache hits

Caching keys on the **prefix matching exactly**, from the start. Structure prompts for it:

- **Stable stuff first, variable stuff last.** System prompt, instructions, few-shot
  examples, and big documents go at the **front**; the user's changing question goes at
  the **end**. A cache hit covers everything up to the first byte that differs — so
  anything variable near the front ruins it.
- **Keep the prefix byte-for-byte identical** across calls. A timestamp or random id at
  the top breaks the match.
- **Reuse, don't regenerate.** If you build the prefix fresh each time, make sure it
  serializes identically.

> **Caveats.** Caches are evicted over time and under memory pressure, so an unused prefix
> goes cold. And whether `cached_tokens` is *reported* depends on the endpoint — if it's
> `None`, caching may still be happening server-side; you just can't observe it in
> `usage`. Monitor it (Section 10) where you can.

---

> **Security:** A prompt cache is shared state. Don't let one user's cached context be served to another — key caches per trust boundary, or you'll leak data across users.

## Challenges

1. **Price your real workload.** Plug your endpoint's actual prices into `work/cost.py`.
   *Success:* you can state the cost of a 500-in / 300-out call, and of 10,000/day.
2. **Cost the reasoning tax.** Run a hard prompt at `reasoning_effort="high"` vs `"low"`
   (Section 6) and cost each. *Success:* you can put a dollar figure on the extra thinking.
3. **Break the cache.** In `work/cache.py`, prepend a per-call counter to `big_prefix`.
   *Success:* `cached_tokens` collapses to ~0 — proving exact-prefix matching.
4. **Fix a bad layout.** Put the variable question at the *front* of the prefix, confirm
   the cache stops helping, then move it back. *Success:* you can explain why ordering
   matters.

---

## Recap

- Billing is **per token**: output (incl. **reasoning** tokens) costs more than input;
  cached input is discounted. Prices are per 1M tokens.
- Compute cost straight from `response.usage`; combine with Section 10 logs to track spend.
- **Prompt caching** reuses work on a shared prefix → faster and cheaper; watch it via
  `response.usage.prompt_tokens_details.cached_tokens` *(int)*.
- Maximize hits by putting **stable content first, variable content last**, and keeping
  the prefix **identical**.

## Next — end of the Foundations arc

That completes Sections 1–11. You can now talk to the server, understand and control its
output, structure and stream responses, survive failures, observe what's happening, and
account for cost — and you *wrote every bit of it yourself*. The **[Advanced arc
(Sections 12–25)](12-prompt-engineering-fundamentals.md)** builds on all of it: prompt engineering, conversation state, tool
calling, retrieval (embeddings + RAG), security, agents, evaluation, and a capstone.
