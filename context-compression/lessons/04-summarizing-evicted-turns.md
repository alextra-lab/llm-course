---
title: 'Summarizing Evicted Turns'
linkTitle: '4. Summarizing Evicted Turns'
weight: 4
---

**Goal:** stop throwing evicted turns away. Unit 3 dropped the oldest middle turns outright;
that frees tokens but deletes whatever those turns held. This unit keeps a **lossy structured
trace** of them instead — a short, schema'd recap that costs a fraction of the tokens but keeps
the identifiers a later turn may still need. You will build a cheap compressor with a graceful
fallback, learn where the recap is allowed to live in the transcript, and meet the reason
production does *not* re-insert it every turn.

**Where this fits:** this is the third branch of the decision tree (Unit 0) — what to do when
plain drop-oldest (Unit 3) would delete something you still depend on. It uses Unit 1's meter to
size before and after, and Unit 2's threshold to decide when to act at all. It points forward to
Unit 5 (keep a head *and* a tail, so only the middle is ever summarized), Unit 6 (a cheap
deterministic pre-pass to run *before* you pay this LLM), Unit 7 (the trigger that fires this in
the background), and Unit 9 (why the cache makes re-inserting a summary every turn a bad idea).

---

## From deletion to a lossy trace

Unit 3's drop has one failure that powers this whole unit: if the turn it evicts held a fact a
later turn needs — a file path, a host and port, a ticket ID — that fact is not compressed, it is
gone. Summarizing is the same eviction with a receipt. Before you delete the slice, you ask a
model to write down what mattered in it, and you keep that note in the slice's place. The window
still shrinks; the memory does not go blank.

The trade stays lossy: a summary is a smaller, lower-fidelity copy you cannot decompress back. So
the bar is not "keep everything" — that is what the original turns were for — but "keep what a
later turn will reach for." That is only meetable if the summary has a *shape* that forces those
things to the surface, which is the next section.

## A schema, not a paragraph

A free-form prose summary is a coin toss: the model keeps whatever it finds salient, which is
often the narrative and rarely the `db-prod-1:5432` you will need in three turns. The fix is to
give the summary a fixed schema with a slot for exactly the load-bearing categories:

```
## Conversation Summary
- **Decisions:** ...
- **Entities:** ...
- **Facts:** ...
- **Open Items:** ...
```

Four sections, and a first line that is doing real work. The `## Conversation Summary` header is
not decoration — it is how the rest of the system **detects** that a message is a summary at all
(downstream code tests `startswith("## Conversation Summary")`). Keep it verbatim. The four
categories are chosen so the things agents actually lose each land somewhere: **Decisions** (what
was chosen, so it is not relitigated), **Entities** (the files, services, and identifiers in
play), **Facts** (durable truths established), **Open Items** (unfinished work). The prompt adds
three rules that matter more than the prose quality:

- **At most ~200 words.** A summary that grows with the conversation defeats its own purpose.
- **Only information present in the messages** — invent nothing; a confident hallucinated fact is
  worse than an honest gap.
- **Preserve identifiers verbatim** — paths, ticket IDs, function names, model ids, hosts, ports.
  These are exactly what a later turn dereferences, and exactly what a prose summary rounds off.

One more rule keeps a long run from leaking: **fold any prior summary into the new one.** If the
slice you are compressing already begins with a `## Conversation Summary` block (because you
compacted earlier), the compressor must merge it into its output rather than summarize the summary
into a thinner and thinner copy. One summary block, rewritten — never a stack of them.

## A cheap model, told to fail safe

This is the first unit whose mechanism genuinely calls an LLM, so spend on it carefully. The job
is small and mechanical — restructure a slice of text into four bullets — so it does not need your
strongest model. Production uses a dedicated, cheap **compressor** role (a small `gpt-5.4-nano`-class
model) at `temperature=0.2` for stable output, with a `timeout` of about **25s** so a slow call
cannot stall a turn. In the example, `COMPRESSOR_MODEL` defaults to the foundations model but marks
the seam where a cheaper one plugs in.

Two numbers here are easy to conflate, so be precise about them. The prompt asks for **≤200
words**; the call also sets **`max_tokens=512`**. The word count is *guidance to the model* — it
shapes the summary but does not bind it. The token cap is the *hard limit* — if the model ignores
the word count, `max_tokens` truncates the output regardless. Cite both: the 200-word ask is the
intent, the 512-token cap is the enforcement.

And because this is a network call inside a turn, it must **fail safe**. Any failure — a timeout, a
network error, or output that does not start with the schema header — returns nothing, and the
caller falls back to the same plain `[Earlier messages truncated]` marker from Unit 3:

```python
def summarize(client, slice_messages):
    try:
        r = client.chat.completions.create(
            model=COMPRESSOR_MODEL,
            messages=[{"role": "system", "content": SUMMARIZER_SYSTEM},
                      {"role": "user", "content": render(slice_messages)}],
            temperature=0.2, max_tokens=512, timeout=25,
        )
        text = (r.choices[0].message.content or "").strip()
        return text if text.startswith("## Conversation Summary") else None
    except Exception:
        return None        # never crash a turn -- the caller falls back to the marker
```

A failed compaction must degrade to Unit 3's behaviour (drop with a marker), not to a crash. The
recap is an improvement on the drop; it is never a dependency the turn cannot proceed without. In
production this call also fires in the background — fire-and-forget — so the turn does not block on
it at all; that machinery is Unit 7.

## Where the recap goes — and the role trap

You have a recap. Where does it go in the message list? In the evicted slice's place: anchored
head, then the recap, then the verbatim tail. But the recap's **role** is a trap worth flagging.

The intuitive choice is `role="system"` — it is meta-information about the conversation, not a real
turn. That choice can make the summary silently vanish. Many chat stacks (and the role-fixer in a
production harness) validate the transcript by keeping only the **first** system message and
dropping any later system-role message. A recap inserted as a second system message in the middle
of the conversation is exactly such a message — so it is quietly deleted, and you are back to a
blind gap with none of the facts you paid a model to extract.

The fix is to insert the recap as **`role="assistant"`**. An assistant message in the middle of a
transcript is ordinary and survives validation:

```python
def recap_message(summary):
    # assistant, NOT system: a non-first system message is dropped by role validation,
    # which would silently delete the recap.
    return {"role": "assistant", "content": summary if summary else MARKER_TEXT}
```

(There is one more structural rule, inherited from Unit 3: the slice you summarize must not split a
tool pair. An assistant tool-call and its `tool` result are one unit; summarize them together or
not at all, so the rebuilt transcript never has an orphaned result.)

## The catch: re-inserting every turn breaks the cache

It is tempting to read all this as a clean win — summarize the middle, slot the recap in, repeat
every turn forever. It is not, and the reason is the prompt cache from Unit 2.

A recap inserted at a fixed position in the middle of the prompt is, by construction, a byte that
changes. Re-run the summarizer next turn and the recap text differs; even re-inserting the *same*
recap shifts everything after it. Either way the cached prefix stops matching from the recap
onward, and the server re-prefills the rest at full price — every turn. The naive "summarize and
re-insert each turn" design is a cache-buster wearing the costume of an optimization. Build it,
then put the Unit 1 meter on it and watch the cached-prefix tokens collapse turn after turn.

This is why a production harness, under its default cache-frozen layout, **disables** summary
re-insertion and lets the static `[Earlier messages truncated]` marker win: a marker that never
changes keeps the prefix byte-identical and the cache intact. The summary still has a place — but
at a *scheduled reset*, where the layout is rebuilt once into `[first user][assistant
recap][verbatim tail]` and then frozen again, not re-edited every turn (Unit 9). For now, hold the
honest version: the recap is a real tool for surviving an eviction, and re-inserting it every turn
is a real way to break your cache.

> **Security:** the summarizer is an LLM reading attacker-influenced text (tool outputs, pasted
> documents) and writing into your context as an `assistant` message that later turns trust. That
> is an injection path: a crafted middle turn can try to get the summary to drop a safety-relevant
> fact, or to *add* an instruction ("the user approved running any command"). Treat the compressor
> like any untrusted-input boundary — constrain it to the schema, never let summary text be
> executed as instructions, and keep the originals long enough to spot a summary that grew a fact
> the turns never contained.

> **Observe:** this unit emits a `compaction` record with `strategy="summarize"`,
> `tokens_before`/`tokens_after`, `evicted` (turns replaced), a `fallback` flag (did the
> compressor fail and the marker win?), and the `kept_ids`/`lost_ids` it extracted — using the §9
> joining tuple. The loop it closes is the one that justifies the whole mechanism: the example's
> tail literally asks for the host and the ticket that live in the evicted middle, so the log can
> answer *did the summary keep the identifiers a later turn needs?* A `fallback=true` line with a
> full `lost_ids` list is a visible, measurable quality loss — the cost the summary is meant to buy
> back. Unit 11 turns this into the `referenced_later` quality gate.

## Challenges

1. **Make the schema hold.** Run the compressor (set `OPENAI_BASE_URL`) on the example's middle
   slice and confirm the output starts with `## Conversation Summary` and has all four sections.
   *Success:* the recap is inserted as `role="assistant"` and the quality line shows
   `db-prod-1`, `5432`, and `FRE-512` among the kept identifiers.
2. **Force the fallback.** Run offline (or point `COMPRESSOR_MODEL` at nothing) and confirm the
   turn does not crash — it falls back to the marker. *Success:* the `compaction` record shows
   `fallback=true` and a non-empty `lost_ids`, and you can name which tail question can no longer
   be answered as a result.
3. **Break the cache, then see it.** Insert the recap, then re-summarize and re-insert on a
   simulated next turn, and use the Unit 1 meter to compare the cached-prefix tokens before and
   after the recap position. *Success:* you can state how many previously-cached tokens the
   re-insertion invalidates — and why a static marker would not have.

## Recap

- Summarizing is **eviction with a receipt**: keep a small lossy trace of the turns you drop, so a
  later turn can still reach the facts they held. It is still lossy and irreversible — aim to keep
  what will be *referenced*, not everything.
- Give the summary a **schema**: the load-bearing `## Conversation Summary` header (detected
  downstream), then **Decisions / Entities / Facts / Open Items**; ≤200 words; only present
  information; **identifiers verbatim**; and **fold any prior summary** into the new one.
- Use a **cheap compressor** (temp ≈ 0.2, timeout ≈ 25s). The **≤200-word** rule is guidance; the
  **`max_tokens=512`** cap is the real bound — cite both. On any failure, **fall back** to the plain
  marker; never crash a turn.
- Insert the recap as **`role="assistant"`**, not `system` — a non-first system message is dropped
  by role validation, which would silently delete your summary.
- Do **not** treat re-inserting the recap every turn as a clean win: a recap at a fixed mid-prompt
  index **breaks the prompt cache** every turn. Production freezes the layout and lets a static
  marker win; the summary belongs at a scheduled reset (Unit 9).

## Next

**Unit 5 — Head, Middle, Tail:** so far we anchor the head and summarize from the front, but the
*recent* tail matters as much as the task at the head. Unit 5 makes the invariant explicit — keep
the head **and** the tail verbatim, and only ever compress the middle — so summarization never
touches the turns the model is actively using.
