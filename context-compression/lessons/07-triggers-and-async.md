---
title: 'When to Fire: Triggers & Async Compression'
linkTitle: '7. When to Fire: Triggers & Async Compression'
weight: 7
---

**Goal:** decide *when* compaction runs, and get it off the critical path. Units 3–6 built the
*what* — drop, summarize, head/middle/tail, the deterministic pre-pass — but left the timing
open. This unit builds the *when*: a **soft** threshold that fires compaction in the
**background** while the turn keeps going, a **hard** threshold that **blocks** because the
window is genuinely tight, and a **re-fire cursor** that stops the soft trigger from firing
again every single turn. The theme is latency: a user should not wait on a summarizer they did
not ask for.

**Where this fits:** this unit sets the timing for the whole decision tree (Unit 0). It uses
Unit 1's meter to read the budget and Unit 2's **soft 0.65** line as the first trigger, and it
runs whichever mechanism Units 3–6 chose. It carries forward Unit 4's hard caveat —
re-inserting a regenerated recap every turn breaks the cache — and shows that running the
compute *asynchronously* does not make that caveat go away. It points to Unit 9 (the scheduled,
cache-aware reset, which is where the rewritten layout actually lands) and Unit 11 (measuring
whether all this timing helped).

---

## Two thresholds, two urgencies

A single trigger cannot serve two very different situations. When usage first creeps past a
comfortable line, you have plenty of room and plenty of time — the right move is to start
compacting quietly and let the turn finish. When usage is *almost at the ceiling*, you have no
room and no time — the next call may overflow, so you must compact **before** it, even if that
means the user waits. So the course uses two thresholds, with two urgencies:

| | Soft trigger | Hard trigger |
|---|---|---|
| **Fraction of budget** | ~**0.65** (Unit 2's line) | ~**0.85** |
| **Why it fires** | Room is shrinking; act early and cheaply | Window is nearly full; the next call may overflow |
| **Runs** | **Async** — in the background | **Sync** — before the next call |
| **Blocks the turn?** | No — the turn continues | Yes — the turn waits |
| **User-facing?** | Invisible | Asks the user first (below) |

The numbers are the production spine's defaults (`0.65` soft, `0.85` hard of a real window), and
like every threshold in this course they are dials, not constants (Unit 2). What matters is the
shape: one line where compaction *may* happen without anyone noticing, and a higher line where
it *must* happen even if someone does.

## The soft trigger: fire and forget

The reason to split the triggers is **latency**. Compaction is not free time: the deterministic
pre-pass (Unit 6) is fast, but the LLM summarizer (Unit 4) is a network call that can take
seconds. Run it inline, on the turn that crossed the line, and the user waits seconds for an
answer to a question that had nothing to do with compaction. That is a bad trade when you had
room to spare.

So at the soft line you **fire and forget**: start the compaction on a background thread and let
the current turn proceed immediately. The compute still happens; it just happens off the path
the user is waiting on.

```python
def fire_soft(messages, budget, sess, trace, step):
    """Background, fire-and-forget: the turn does NOT wait for this."""
    def work():
        t0 = time.perf_counter()
        _, before, after = compact(messages, budget)        # the slow part (summarizer in prod)
        log_event(sess, trace, step, "compaction", trigger="soft", fired=True,
                  blocking=False, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                  tokens_before=before, tokens_after=after)
    th = threading.Thread(target=work, daemon=True)
    th.start()                                               # returns at once; turn continues
    return th
```

The turn that triggered this is blocked only for as long as it takes to *start* the thread —
microseconds — instead of the seconds the compaction itself costs. That is the whole point of
the soft path. *(Reference:
[`examples/07/triggers_and_async.py`](../examples/07/triggers_and_async.py), which runs fully
offline — a fixed delay stands in for the summarizer's real cost.)*

## The re-fire cursor: do not recompact every turn

A soft trigger has a failure mode that is easy to miss. Once usage is past 0.65, it tends to
*stay* past 0.65 — so a naive "fire whenever we are over the soft line" check fires on the next
turn, and the next, and the next. Each of those compactions costs a model call **and** rewrites
the prefix, which breaks the prompt cache (Unit 2). Firing every turn is how you turn a
latency-saving feature into a cache-destroying one.

The fix is a **re-fire cursor**: remember the message index where you last fired, and refuse to
fire again until enough new messages have arrived (the production spine uses a gap of about
**4** messages).

```python
def decide(used, budget, last_fire_index, msg_index, soft=0.65, hard=0.85, gap=4):
    frac = used / budget
    if frac >= hard:
        return "hard", frac
    if frac >= soft:
        if last_fire_index is not None and (msg_index - last_fire_index) < gap:
            return "skip-refire", frac      # too soon since the last soft fire -- wait
        return "soft", frac
    return "skip", frac
```

The cursor turns "we are over the line" into "we are over the line *and* it has been a while" —
which is what you actually want. It also bounds how often anything, including injected content,
can drive a compaction (see the security note).

## The hard trigger: block, and ask

The hard line is the opposite case. At ~0.85 the window is nearly full; the next user or tool
message could push the prompt over the ceiling, where the call is rejected or silently truncated
(Unit 0). You cannot defer to a background thread that finishes "soon" — you must compact
**before** the next call, on the critical path, and accept that the turn waits.

There is one more wrinkle the production spine adds, and it is worth keeping. A hard, blocking
compaction is lossy (every compaction is), and the user is *already* waiting — so production
(ADR-0076) **asks** before it fires: *stop here, or compress and continue?* Sometimes the honest
answer is to stop, save the session, and not pay a lossy compaction at all.

```python
def ask_stop_or_compress():
    if not sys.stdin.isatty():                  # unattended (CI, a pipe): don't hang -- default
        print("  (non-interactive: defaulting to 'compress')")
        return "compress"
    ans = input("  hard threshold reached -- [s]top or [c]ompress? ").strip().lower()
    return "stop" if ans.startswith("s") else "compress"
```

The latency the soft path worked so hard to hide is now unavoidable and visible: the turn blocks
for the full compaction. That contrast — invisible at the soft line, unavoidable at the hard one
— is exactly why the two thresholds exist.

## The catch: async changes *when* you pay, not *whether* re-insertion costs

It is tempting to think the background thread solves Unit 4's caveat. It does not. Running the
summarizer asynchronously changes *when* you pay for the compute — off the critical path instead
of on it. It does **not** change what happens when you splice the result back in. A recap
regenerated and re-inserted at a fixed mid-prompt index is still a run of bytes that changes
every time, so it still invalidates the cached prefix from that point onward (Unit 2's
byte-identity rule), no matter which thread produced it.

So the soft trigger buys latency, not a free recap. This is why the production spine, under its
default cache-frozen layout, computes in the background but does **not** re-insert every turn;
the actual rewritten layout — `[head][recap][tail]` — is applied once, on a schedule, and then
frozen again. That scheduled, cache-aware reset is Unit 9. For now, hold the two facts together:
async fixes *latency*; it does not fix *cache invalidation*.

> **Security:** triggers are steerable. An attacker who can pad the context — a long tool
> result, a pasted document — can drive usage across the hard line on purpose, forcing a
> blocking, lossy compaction (and, in production, a "stop vs compress" prompt) on *their*
> schedule, hoping the summarizer drops a safety rule or the interruption confuses the user.
> The re-fire cursor is a small mitigation: it bounds how often injected content can re-trigger
> compaction. Treat a sudden climb toward the hard line — visible in Unit 1's meter — as a
> signal worth alerting on, not just a number.

> **Observe:** this unit extends the `compaction` record with the *timing* fields, all on the §10
> joining tuple. A record that **fired** carries `trigger` (soft/hard), `fired=true`, `latency_ms`
> (what the compaction work cost), and `blocking` (did that cost fall on the turn, or off it?); a
> record the cursor **suppressed** carries `fired=false` with `reason="refire-gap"`. The loop it
> closes is this unit's whole claim: with these lines you can measure **how much compaction cost
> you kept off the critical path** (every `fired` record with `blocking=false`) and **how often the
> re-fire cursor suppressed a redundant, cache-busting compaction** (every `reason="refire-gap"`
> record). A latency you did not record is a latency you cannot prove you avoided.

## Challenges

1. **Fire soft, then hard.** Run the example and watch the decision flip from `skip` to `soft`
   (background, turn continues) and finally to `hard` (blocking). *Success:* you can state the
   token fraction at each flip, and confirm the soft turn was *not* blocked while the hard turn
   was.
2. **Watch the cursor suppress a re-fire.** Find the turn where usage is over the soft line but
   the decision is `skip-refire`. *Success:* you can explain why firing there would have cost a
   model call and broken the cache, and what raising or lowering the gap would change.
3. **Measure the latency you hid.** Capture the `compaction` lines with `2>> run.jsonl`. The soft
   and hard fires cost about the *same* `latency_ms` of work — what differs is `blocking`. *Success:*
   from the log alone (the soft record's `blocking=false`), a one-sentence statement of how much
   compaction cost the soft path kept off the user's turn — not by feel.

## Recap

- Use **two thresholds**: a **soft** line (~0.65) where compaction runs **async** in the
  background without blocking the turn, and a **hard** line (~0.85) where it runs **sync** before
  the next call because the window is nearly full.
- The soft trigger is about **latency**: a summarizer is a slow network call, so **fire and
  forget** it off the critical path instead of making the user wait.
- A **re-fire cursor** (gap ~4 messages) stops the soft trigger from firing every turn — which
  would cost repeated model calls and repeatedly break the cache.
- The hard trigger **blocks**, and production **asks the user** "stop vs compress" first, because
  a blocking compaction is lossy and stopping is sometimes the better answer.
- **Async changes *when* you pay, not *whether* re-insertion costs.** A background-computed recap
  re-inserted at a fixed index still breaks the cache; the real rewritten layout is deferred to a
  scheduled, cache-aware reset (Unit 9).

## Next

**Unit 8 — Offloading & Paging: Gist Memory:** triggers decide when to shrink the window, but a
single giant artifact can be too valuable to summarize and too big to keep. Unit 8 stores the
full bytes outside the window, keeps a short reference inside it, and pages the bytes back on
demand — and meets the read→edit dependency hazard that makes naive offloading dangerous.
