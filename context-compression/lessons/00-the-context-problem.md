---
title: 'The Context Problem'
linkTitle: '0. The Context Problem'
weight: 0
---

**Goal:** understand the problem this course solves, and why it is harder than "the window
is full." A long-running agent keeps adding to the message list it resends every turn
(§12), and eventually that list will not fit in the model's context window. That is the
*obvious* ceiling. The harder truth is a second, softer ceiling: a model uses a full window
*worse* than a short one. So you compress not only to fit, but to keep the model accurate —
and every choice about what to drop, summarize, or keep has a cost you must measure. This
course is about managing that budget without losing the things the agent still needs.

**Where this fits:** this is the start of the **third** course, a sibling of the
[Agent Memory](../../agent-memory/README.md) course — read them in either order. It assumes
the foundations course: §3 (tokens and the context window), §10 (prompt caching), §12
(conversation state and history), and §22 (agents). The Agent Memory course began by setting
this whole topic aside — its Unit 0 calls context management "the thing you already built"
and goes off to study memory *across* sessions. This course goes back and develops the part
that was set aside: keeping *one* long session inside the window.

---

## Two ceilings, not one

In §12 you made a stateless API hold a conversation by resending the whole `messages` list
every turn. An agent (§22) does the same, and adds tool calls and their results to the list
as it works. The list only grows. Two different limits are waiting for it.

The **hard ceiling** is the context window: a fixed number of tokens the model can accept.
Cross it and the call fails, or the server silently truncates your input — which is worse,
because you do not see it happen. A few dozen turns of an agent reading files and running
tools can reach a 100,000-token window faster than you expect.

The **soft ceiling** is quieter and more surprising: well before the hard limit, the model
starts using the context *badly*. Accuracy falls as the input grows, even on simple tasks.
You do not get an error — you get worse answers. This is the ceiling most people do not know
is there.

| | Hard ceiling | Soft ceiling |
|---|---|---|
| **What it is** | The token limit of the window | Degrading accuracy as input grows |
| **How you notice** | A rejected call, or silent truncation | Quietly worse answers, no error |
| **Where it bites** | Long agent runs, big tool outputs | Long context *before* the limit |
| **The fix** | Make the input smaller | Keep only what helps; cut the noise |

Both ceilings point the same way: a leaner window is not just cheaper, it is often *better*.
That is the idea the whole course is built on.

## Where the budget goes

It helps to see what fills the window in the first place. Every turn, the prompt is roughly:

- the **system prompt** — fixed instructions, usually small;
- the **tool definitions** — schemas for every tool the agent can call (§22);
- **retrieved context** — memory or RAG passages injected for this turn (§19);
- the **conversation history** — every prior user, assistant, and tool message (§12);
- the **tool outputs** — and these are the dangerous ones. A single file read, a stack
  trace, or a command's output can be larger than the entire conversation around it.

History and tool outputs grow without limit; the rest is mostly fixed. A long session is
just that list getting longer:

```python
# A long session is a messages list that keeps growing (foundations §12).
messages = [{"role": "system", "content": SYSTEM_PROMPT}]
budget = 8000   # the model's working window, in tokens (ask the server for the real number)

for turn in long_conversation:
    messages.append(turn)
    used = estimate_tokens(messages)        # a heuristic count; Unit 1 makes this precise
    print(f"{len(messages):3d} messages  ~{used:6d} tokens  {used / budget:5.0%} of budget")
    if used > budget:
        print("over budget -- the next call is rejected or silently truncated")
        break
```

The list grows, the count climbs, and at some point you are over budget. This course is about
what to do at that point — and, just as important, what to do *before* it, so the answer stays
good.

## More context is not better

The soft ceiling deserves evidence, because it is counter-intuitive — surely a bigger window
can only help? The research says otherwise, and the course treats each finding as *"this
paper reports,"* not settled fact:

- **Position matters.** Liu et al. (*Lost in the Middle*, TACL 2024; arXiv:2307.03172) report
  a **U-shaped** bias: a model uses information at the very start or the very end of its input
  far better than information in the middle. Bury a key fact in the middle of a long context
  and the model often misses it.
- **Effective length is shorter than advertised.** The RULER benchmark (Hsieh et al., COLM
  2024; arXiv:2404.06654) reports that a model's *usable* context is well below its stated
  window — many "32K" models hold up on only about half of that.
- **Length alone hurts reasoning.** Levy et al. (ACL 2024; arXiv:2402.14848) padded the *same*
  question to different lengths and found accuracy fell as the input grew, with nothing else
  changed. The popular name for this effect is **"context rot."**

Put together: past a point, filling the window makes the model slower, more expensive, *and*
less accurate. Compression is how you push back on all three.

## Compression is not memory

One distinction up front, because the two get confused. The Agent Memory course is about what
an agent knows **across** sessions — facts stored in a database and retrieved later. This
course is about keeping **one** session inside its window. They both decide "what does the
model see in the prompt?", but the mechanisms are unrelated.

| | Compression (this course) | Memory (the sibling course) |
|---|---|---|
| **Scope** | One session, right now | Across sessions, indefinitely |
| **Core operation** | Trim / summarize / offload to fit | Store, then retrieve what's relevant |
| **Lost when** | You drop it to save space | You delete it (or let it decay) |
| **Failure mode** | Drops something still needed | Forgets, or recalls the wrong thing |

One naming caution, picked up properly in Unit 9: this course means **context compression** —
shrinking the *input tokens* the model reads. That is different from *KV-cache compression* (a
runtime-memory trick that frees no space in your window) and from *prompt caching* (§10, which
reuses an unchanged prefix — and which, as you will see, compaction breaks).

## The thesis

This course is **measured, not authoritative**. I am a student of this material, not an
expert in it, so rather than assert a right answer the course instruments every compression
it makes and watches what happens to the model's output. It still argues toward a default —
do the cheapest thing that works, and compress only what you must — but it walks there down a
decision tree, and it will tell you to do nothing when nothing is the right move:

1. **Under budget?** Do nothing. The cheapest compression is none.
2. **Approaching the budget?** Drop or window the oldest turns first (§12) — cheap, and
   usually safe on old turns.
3. **Losing content from the middle that still matters?** Keep the **head** and **tail**
   verbatim; compress only the middle.
4. **Compressing the middle?** Run a cheap deterministic pass before you pay an LLM summarizer.
5. **A single tool output is enormous?** Offload it and page it back on demand.
6. **Latency and cost matter?** Be cache-aware — compaction breaks the prompt cache.
7. **Whatever branch you took:** measure the effect, and make every compaction observable.

And the move under all of them: **the cheapest tokens are the ones you never generate.**
Sometimes the answer is not to compress a giant turn, but to *decompose the task* so the
giant turn never happens. You will build every branch, and the course converges on a single,
defensible default in Unit 12.

> **Security:** the window is an attack surface. An attacker who can add a lot of text — a
> long tool result, a pasted document, a hostile web page — can push your system prompt and
> safety rules *out* of a naive drop-oldest window, or bury them in the middle where the
> model attends to them least. Compression decides what survives; an attacker would love to
> decide that for you. Every later unit has a security note, because every way of dropping
> content is also a way to drop the wrong content.

> **Observe:** this course makes observability a through-line, the same way it makes security
> one. Every later unit carries an `Observe` note: it instruments the compaction it builds —
> a token meter, a compaction record, a before/after trace of what the model saw and whether
> its output changed — using the joinable `session_id`/`trace_id`/`step` line from foundations
> §9. A compression you cannot see is one you cannot trust, so you start measuring in Unit 1,
> not at the end. This is the repo's [Observability Standard](../../OBSERVABILITY.md).

## Challenges

These are thinking-and-experiment tasks; the building starts in Unit 1.

1. **Feel the budget run out.** Take any §12 or §22 script, keep appending turns (or let an
   agent run for many steps), and print the running token count each turn. *Success:* you can
   say which part of the prompt — history or tool output — crossed the budget first.
2. **Find where your budget goes.** For one real prompt, estimate the token share of the
   system prompt, tool definitions, retrieved context, history, and tool outputs. *Success:*
   a rough percentage breakdown, and a one-sentence answer to "what would I cut first?"
3. **Meet the soft ceiling.** Read the abstract of *Lost in the Middle* (or reason it through)
   and place a key instruction at the start, middle, and end of a long prompt. *Success:* you
   can explain, in one sentence, why "just put it all in the window" is not a strategy.

## Recap

- A long session faces **two ceilings**: the hard token limit of the window, and a soft
  ceiling where the model uses a full window *worse* than a short one.
- The window is a **budget** spent on system prompt, tools, retrieved context, history, and —
  most dangerously — tool outputs. History and tool outputs grow without limit.
- Research reports that **more context is not better**: position bias (lost in the middle),
  effective length below the advertised window, and accuracy falling with length ("context
  rot"). Compress to stay accurate, not only to fit.
- **Compression is not memory**: it keeps one session in budget (lossy, on purpose), while
  memory stores and retrieves across sessions. And keep compression, KV-cache compression,
  and prompt caching distinct.
- The course is **measured, not authoritative**: a decision tree from "do nothing" to a
  cache-aware default, with observability welded to every step.

## Next

**Unit 1 — Measuring the Window:** before you can manage a budget you have to read it. You
will build a token meter — without `tiktoken`, asking the server for the truth — that shows
where every token goes, and emit the first joinable telemetry line this course will build on.
