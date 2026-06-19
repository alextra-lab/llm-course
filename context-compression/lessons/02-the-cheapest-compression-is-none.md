---
title: 'The Cheapest Compression Is None'
linkTitle: '2. The Cheapest Compression Is None'
weight: 2
---

**Goal:** learn when *not* to compress. Now that the meter (Unit 1) tells you how full the
window is, the first question is not "how do I compress?" but "should I compress at all?" The
answer, most of the time, is no. Compressing early costs you twice — it throws away answer
quality you did not need to spend, and it throws away your prompt cache — to solve a problem
you do not have yet. This unit sets the opening rule of the whole course: **under budget, do
nothing**, and it puts numbers on what you lose when you ignore that rule.

**Where this fits:** Unit 0 made "do nothing" the first branch of the decision tree; Unit 1
gave you the meter that reads the budget. This unit explains *why* that first branch is there.
It leans on §11 (prompt caching — the cost you cannot see) and §13 (the growing `messages`
list). It points forward to Unit 7 (the trigger thresholds that decide the moment to act) and
Unit 9 (cache-aware compaction in full). Think of this as the unit that earns the right to be
lazy.

---

## The opening rule: under budget, do nothing

A long-running agent spends most of its life comfortably inside the window. Unit 1's meter
showed a real session sitting at a third of its budget; that is the normal state, not the
exception. The hard ceiling is far away, and — this is the part that is easy to forget — the
*soft* ceiling from Unit 0 only bites when the context is genuinely large. A model is not
degraded at 11% of its window. So compressing now buys you nothing on either ceiling. It only
costs.

One caution, because this course tries to let measurements do the arguing rather than the author:
do not over-claim here. A production agent harness ships a three-phase budget drop as a
*last-resort* net, but the sessions used to evaluate it never came close to filling the window,
so that telemetry cannot tell you how often the heavy drop actually fires in a genuinely long
run — that is simply *not yet measured*. The defensible claim is the narrow one: the heaviest
compaction exists for a window that is truly full; the lighter thresholds you build in Units 3–7
trip well before that; and turn to turn, the default state of a healthy agent is do-nothing. The work of this unit is to make that the
*deliberate*, measured default — not an accident of never crossing the limit.

## Compression is lossy, and you cannot undo it

Start with the cost everyone underestimates: a summary is a **lossy copy**, and the moment you
replace the original turns with it, the original is gone. There is no decompression. If turn 14
held an identifier — a file path, a port number, a ticket ID — and your summary did not happen
to keep it, that fact is not "compressed," it is *deleted*. You will discover this only when a
later turn needs it.

That risk is worth taking when the window is under real pressure: a lossy copy in budget beats
an exact copy that does not fit. But under budget there is no trade — you take the loss and get
nothing back. You were not going to overflow, and the model was not degraded, so every fact the
summary drops is pure cost. **The only compression with no downside is the one you do not do.**

## The cost you cannot see: the prompt cache

The quality cost is at least visible eventually. The second cost is invisible until you read
your bill, and it is usually the larger one. It is the **prompt cache** (§11).

Recall how caching works. The server caches the key/value tensors for a prefix of your prompt;
on the next call, if that prefix is **byte-for-byte identical**, it reuses the cached work
instead of recomputing it. A cache read is cheap — on the order of a tenth of the price of
fresh input — and a normal agent turn is built to exploit this: you **append** the new
user/tool message to the end, leaving the entire prior prefix untouched. Turn after turn, the
growing history is almost all a cache hit.

Compaction breaks exactly this. When you summarize the middle of the conversation, you *rewrite*
messages that were previously stable. From the first byte you changed, the cached prefix no
longer matches, so the server throws it away and re-prefills everything from the edit point at
full price — and pays the cache-*write* surcharge again to re-cache the new version. Anthropic's
caching docs state it plainly: a modification "invalidates the cache from that point onward."
The Manus team, writing about building their agent, call the KV-cache hit rate the single most
important production metric, and put the gap between cached and uncached tokens at roughly **10×**.

| | Append a turn (normal) | Compact the middle |
|---|---|---|
| **Prefix bytes** | Unchanged | Rewritten from the edit point |
| **Cache effect** | Hit — prefix reused at ~0.1× | Miss — re-prefill from the edit point at full price |
| **Extra cost** | None | Re-prefill + cache-write surcharge + the summarizer's own call |
| **Worth it when** | Always | The window is genuinely tight |

So a needless compaction is not one cost but three stacked together: you re-prefill the
invalidated prefix, you pay a compressor model to produce the summary, and you accept the
quality loss — all to shrink a window that had plenty of room. The meter from Unit 1 can put a
number on the first of these. Collapsing the middle of even a tiny six-message session
invalidates the cached prefix from the edit point onward; on a real session that is thousands of
tokens that were costing you a tenth of the price and now cost full freight.

## Headroom, not the ceiling

"Do nothing under budget" needs a definition of *under budget*, and it is not "below 100%." You
never spend to the last token, for two reasons: the model still has to fit its **response** in
the window, and your token estimate is a heuristic that drifts (Unit 1). So you carve out
**headroom** — a reserve you do not touch — and you set a **soft threshold** below the ceiling
as the line where compression even becomes a question. A real harness reserves a few thousand
tokens for the reply and only *considers* compressing once usage crosses about **0.65** of the
budget; the moment to actually act, and the difference between a soft async trigger and a hard
blocking one, is Unit 7's subject. For now the shape is what matters:

```python
def decide(messages, budget, soft=0.65, reserved=1000):
    used = estimate_tokens(messages)
    fraction = used / budget
    if used < soft * budget and used < budget - reserved:
        return "skip", f"under soft threshold ({fraction:.0%} < {soft:.0%})"
    return "compress", f"crossed soft threshold ({fraction:.0%} >= {soft:.0%})"
```

The threshold is a dial, not a constant. A cheap, append-heavy workload can run the soft line
higher and lean on the cache longer; a workload with expensive, irreplaceable middle turns sets
it lower so it never gets close to the hard ceiling. Either way the principle holds: the budget
has a reserve, and below the soft line the answer is *skip*.

## Instrument the decision — including the skip

A decision you did not record is indistinguishable from one you forgot to make. So the
observability move in this unit is to log the choice **every time**, including — especially —
when the choice is to do nothing:

```python
log_event(session_id, trace_id, 0, "compaction_decision",
          decision="skip", budget=BUDGET, used=used, fraction=round(fraction, 3),
          soft=SOFT, cache_tokens_at_risk=cache_loss)
```

Across a run, those lines answer a question you cannot eyeball: *how often did we compress while
under budget?* Every such line is waste you can now see and cut — a compaction that spent cache
and quality to fit a window that was 11% full. The do-nothing default is only trustworthy if you
can prove, from the telemetry, that the agent actually took it.

> **Security:** the safe default cuts both ways. "Don't compress under budget" keeps your system
> prompt and the early, load-bearing turns verbatim in the window — good. But it also means an
> attacker who pads the context with a long tool result or pasted document can *drive* you toward
> the threshold and trigger a compaction on their schedule, hoping the summarizer drops a safety
> rule or keeps their injected instruction. Treat the decision to compress as security-relevant:
> the meter spike that crosses your soft line is also the signal that someone may be steering it.

> **Observe:** this unit emits a `compaction_decision` record — `decision` (skip/compress),
> `fraction` of budget, the `soft` threshold, and `cache_tokens_at_risk` — on every turn, using
> the foundations §10 joining tuple. The loop it closes is the discipline of the whole course:
> with these lines you can measure how often the agent compressed while under budget (pure waste)
> and how much cache each compaction put at risk, and tune the threshold from data instead of
> from a guess. A do-nothing you logged is a decision; a do-nothing you did not is a blind spot.

## Challenges

1. **Make it skip, then make it fire.** Run the decision on a small session and confirm it
   returns `skip`. Now append turns (or a big tool output) until it crosses the soft line.
   *Success:* you can state the exact token count at which the decision flipped, and it matches
   `soft × budget`.
2. **Price a needless compaction.** For a session well under budget, use the Unit 1 meter to
   estimate the cached prefix tokens a mid-conversation compaction would invalidate. *Success:* a
   single number — "compacting here throws away ~N cached tokens" — and a one-sentence argument
   for why that turn should be a `skip`.
3. **Audit the do-nothing.** Capture the `compaction_decision` lines from a multi-turn run with
   `2>> run.jsonl` and count how many were `compress` while `fraction` was below your soft
   threshold. *Success:* the count is zero — and you can show it from the log, not just claim it.

## Recap

- The opening rule of the course: **under budget, do nothing.** A healthy agent spends most of
  its life there; the heaviest last-resort drop is for a genuinely full window, and how often it
  fires in long runs is *not yet measured* (the lighter thresholds trip first).
- Compression is **lossy and irreversible**: under pressure that trade is worth it, but under
  budget you take the loss and get nothing back.
- The invisible cost is the **prompt cache** (§11): a normal turn appends and stays a cache hit,
  while compaction rewrites the prefix and forces a full re-prefill from the edit point —
  Anthropic: a modification "invalidates the cache from that point onward."
- Define "under budget" with **headroom**: reserve tokens for the response and set a **soft
  threshold** (~0.65) below the ceiling as the line where compressing even becomes a question.
- **Instrument the decision, including the skip**, so the do-nothing default is one you can prove
  from telemetry rather than hope for.

## Next

**Unit 3 — Drop & Window: The Safe Baseline:** when you *do* cross the threshold, the cheapest
real action is also the oldest one — drop or window the stale turns first. You will build the
safe baseline (drop-oldest, a sliding window, and a trim priority of history → memory → tool
defs) that every smarter mechanism later in the course has to beat.
