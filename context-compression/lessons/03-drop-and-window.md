---
title: 'Drop & Window: The Safe Baseline'
linkTitle: '3. Drop & Window: The Safe Baseline'
weight: 3
---

**Goal:** build the cheapest compaction that actually frees tokens. Unit 2 said do nothing while
you are under budget; this unit is what to do the moment you cross the line. The answer is the
oldest trick in the book and still the right first move: **drop the oldest turns**. You will
build a sliding window that anchors the parts you must never lose, drops the stale middle until
the prompt fits again, and records every drop — the safe baseline that every smarter mechanism
later in the course has to beat.

**Where this fits:** this is the second branch of the decision tree (Unit 0). It builds directly
on §12 (the sliding-window history you already wrote) and on Unit 1's meter and Unit 2's
threshold — you drop *because* the meter crossed the line. It points forward to Unit 4 (summarize
what you are about to drop, instead of losing it) and Unit 5 (head/middle/tail, which fixes the
one case this baseline handles badly). Think of this as the floor: simple, fast, lossy, and the
thing to reach for before anything cleverer.

---

## The cheapest action that frees tokens

Compression that does not remove tokens is not compression. Once Unit 2's threshold trips, you
need an action that genuinely shrinks the prompt, and the cheapest one is to **delete the oldest
messages**. It costs no model call, no extra latency, and — by the recency logic from Unit 0 — it
removes the turns least likely to matter to the next answer. This is the sliding window from §12,
now used on purpose as a compaction step rather than a passive cap.

Why oldest-first, and not (say) the largest message? Because order carries meaning. The model
attends most to the start and end of the context (Unit 0's U-shape), and the *end* is where the
live work is. Dropping from the front removes the coldest content and leaves the recent tail —
the turns the next response actually depends on — untouched.

## Anchor the head, or you will drop the task

Naive drop-oldest has a sharp failure mode: the very first messages are usually the **system
prompt** and the **first user message** — the rules and the task. Those are the oldest things in
the list, so a blind sliding window deletes them first, and the agent forgets what it was asked
to do while dutifully remembering the last six tool calls. The fix is to treat the head as
**anchored**: leading system messages plus the first user message are never eligible for
eviction.

```python
def _head_end(messages):
    """Head = leading system messages + the first user message (the task). Never evicted."""
    i = 0
    while i < len(messages) and messages[i]["role"] == "system":
        i += 1
    if i < len(messages) and messages[i]["role"] == "user":
        i += 1
    return i

def sliding_window(messages, budget):
    head_end = _head_end(messages)
    head, middle = messages[:head_end], list(messages[head_end:])
    dropped = 0
    while middle and estimate_tokens(head + [MARKER] + middle) > budget:
        middle.pop(0)          # evict the OLDEST middle turn -- least likely to matter now
        dropped += 1
    return (head + [MARKER] + middle if dropped else head + middle), dropped
```

The `MARKER` — a short `[Earlier messages truncated]` system line — is not decoration. It tells
the model that a gap exists, so it can ask instead of confidently inventing what was there. A
silent deletion invites confident wrong answers; a marked one invites a question.

Run it on a session that has gone over budget — a system prompt, the task, an early big file
read, and several recent turns *(Reference:
[`examples/03/drop_and_window.py`](../examples/03/drop_and_window.py))*:

```
before: 16 messages, 9656 tokens, 121% of budget (OVER)
after sliding window: 15 messages, 203 tokens, 3% of budget -- dropped 2 oldest middle turn(s)
head preserved? system + task still present: True
```

The window dropped two old turns — including the large early file read — and the prompt fits
again, with the task still anchored at the front. Notice how far it fell: one giant old tool
output was almost the entire budget, so removing it recovered nearly everything. That is the
baseline working — and also a preview of its bluntness, which the next two sections sharpen.

## When you must shed whole components: a priority order

Sometimes windowing the history is not enough, or the pressure is not in the history at all —
it is in the memory passages or the tool schemas you resend every turn (Unit 1's meter shows
where). Then you shed **whole components**, and the question is which goes first. A shipped
agent gateway uses a fixed priority: **history → memory → tool definitions.**

```python
def trim_priority(components, budget):
    total = sum(components.values())
    dropped = []
    for name in ("history", "memory", "tool_defs"):
        if total <= budget:
            break
        total -= components.get(name, 0)
        dropped.append(name)
    return dropped, total
```

The order encodes a judgment about what is recoverable. Old **history** is the most replaceable —
it is in the past and often summarized elsewhere. **Memory** passages can be re-retrieved next
turn if needed (that is the sibling course's whole job). **Tool definitions** go last, because an
agent that loses its tool schemas mid-task cannot act at all. Drop the cheapest-to-lose first;
keep the thing whose loss is fatal.

## The honest part: this baseline is coarse, and in production it barely fires

Two truths keep this unit measured rather than triumphant.

First, the real thing is blunter than the tidy loop above. The production gateway's history
trim is **all-or-nothing**: when it fires, it collapses the history to the system messages plus
the single most recent user message in one step — not a gentle turn-by-turn slide. It is a
guillotine, not a dial. The course teaches the gentler sliding window as the better *primary*
design and treats the all-or-nothing collapse as the blunt last resort it is.

Second, that last resort almost never runs. As Unit 2 noted, the same gateway's drop was
measured peaking at about **2.5%** of its token ceiling — but that figure came from short
evaluation sessions that never filled the window, so it says the net is *rarely reached*, not
that it is *unnecessary*. Whether it fires in genuinely long runs is an open, testable
question; until someone drives a real long session and watches the occupancy curve, "it never
fires" is a statement about the test, not the mechanism.

And the deeper limit, the one that powers the rest of the course: dropping is **lossy and
irreversible**, exactly like Unit 2's warning. If the giant old tool output you just evicted was
the file the model still needs to edit, you did not compress it — you deleted it. Worse, if the
giant is *recent*, the sliding window cannot touch it without eating the live tail. That single
gap is the reason the next units exist: **summarize** the turns before you drop them (Unit 4),
keep a **head and tail** so the middle is all that ever goes (Unit 5), and **offload** a giant
artifact instead of dropping it (Unit 8).

> **Security:** drop-oldest is an attack surface, the one Unit 0 named. An attacker who can pad
> the context — a long tool result, a pasted document — can push real content toward eviction,
> and against a *naive* window that includes the head, they can shove your system prompt and
> safety rules out entirely. Anchoring the head is the defense: the rules and the task are never
> the thing that gets dropped. Treat the head boundary as security-critical code, and watch the
> meter for the `tool_outputs` spike that signals someone is steering your window.

> **Observe:** this unit emits a `compaction` record — `strategy="drop"`, `trigger`,
> `tokens_before`, `tokens_after`, and `dropped` (how many turns) — using the §9 joining tuple,
> the first real compaction in the course's through-line. The loop it closes: a drop is a silent
> deletion unless you record it, so the record turns "the window got smaller somehow" into "we
> dropped 2 turns at turn N, from 9,656 to 203 tokens." The quality half — *was a dropped turn
> referenced later?* — is the `referenced_later` flag that Unit 11 adds on top of this exact line.

## Challenges

1. **Anchor the head.** Build `sliding_window` with head-anchoring and run it on an over-budget
   session. *Success:* the prompt ends under budget, the system prompt and first user message are
   still present, and a marker sits where the gap is. Then remove the anchor and watch the task
   disappear — so you can see what the anchor buys.
2. **Drop with a record.** Emit the `compaction` line on each drop and capture it with
   `2>> run.jsonl`. *Success:* a JSONL record whose `tokens_before`/`tokens_after` show the drop,
   and a one-sentence answer to "how would I later tell if a dropped turn was needed?"
3. **Break the baseline on purpose.** Move the big tool output from the old middle to the most
   recent turn and re-run. *Success:* you can explain why the sliding window now either fails to
   get under budget or has to eat the live tail — and which later unit (5 or 8) is the fix.

## Recap

- The cheapest compaction that actually frees tokens is **drop the oldest turns** — no model
  call, and by recency the coldest content goes first. This is §12's sliding window used on
  purpose.
- **Anchor the head** (system messages + first user message). A naive window evicts the oldest
  messages, which are the rules and the task; anchoring is what keeps drop-oldest safe.
- Leave a **marker** where content was removed, so the model asks instead of inventing.
- When whole components must go, shed them in priority order **history → memory → tool defs** —
  cheapest-to-lose first, the fatal-to-lose last.
- Be honest about the baseline: production's version is an **all-or-nothing** collapse that, on
  the evidence so far, **rarely fires** — and dropping is lossy. Its one bad case (a needed or
  recent giant) is exactly what Units 4, 5, and 8 fix.

## Next

**Unit 4 — Summarizing Evicted Turns:** dropping throws the old turns away; summarizing keeps a
lossy trace of them for a fraction of the tokens. You will build a structured compressor — a
four-section schema (Decisions / Entities / Facts / Open Items), a cheap model, run async with a
graceful fallback — so the window shrinks without the memory going blank.
