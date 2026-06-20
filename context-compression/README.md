# Context Compression: Keeping a Long Agent Inside the Window

The **third** course in this repo — a hands-on follow-on to the
[foundations course](../README.md). The foundations course covered context in about a
lesson and a half (§4 tokens and the window; §13 stateless history with windowing and
summarization). The [Agent Memory](../agent-memory/README.md) course then set this whole
topic aside on purpose — its Unit 0 says context management is "the thing you already
built," and goes off to study memory *across* sessions instead. This course goes back and
goes deep on the part that was waved away: how a single, long-running agent stays inside
the token budget **without losing the things it still needs** — measuring the window,
dropping and windowing, structured summarization, head/middle/tail preservation,
offloading and paging, cache-aware compaction, and a *measured* default — one the
instrumentation earns, not the author.

> **A note on these courses.** This material is based on my own evolving experience
> building AI applications and working with LLMs. It's practical and opinionated, not
> authoritative — the field moves quickly, and some choices here will date or differ from
> yours. Verify anything before relying on it in production.

## Who this is for

This course **assumes the foundations course.** It leans on §4 (tokens and the context
window), §11 (prompt caching), §13 (conversation state and history), and §23 (agents),
and does *not* re-teach them. If those aren't familiar, do the foundations course first —
start at [`../lessons/01-hello-world.md`](../lessons/01-hello-world.md). It is a sibling of
the [Agent Memory](../agent-memory/README.md) course, not a sequel: read them in either
order. Memory is about what an agent knows *across* sessions; this course is about keeping
*one* session inside the window.

## The thesis

This course is **measured, not authoritative**. I am a student of this material, not an
expert in it — so rather than assert a right answer, the course *instruments* every
compression it makes and watches what happens to the model's output. The opinions it does
reach belong to the measurements, not to me. It still argues *toward* a default — do the
cheapest thing that works, compress only what you must — but it walks there down a decision
tree, and it will tell you to do nothing at all when nothing is the right answer:

1. **Are you under budget?** Yes → do nothing. The cheapest compression is none —
   compressing early costs answer quality *and* throws away your prompt cache.
2. **Approaching the budget?** → drop or window the oldest turns first (§13). It's cheap,
   and on old turns it's usually safe.
3. **Losing content from the *middle* that still matters?** → keep the **head** (system +
   first user message) and the **tail** (recent turns) verbatim, and compress only the
   middle.
4. **Compressing the middle?** → run a cheap **deterministic pre-pass** (collapse large
   tool outputs to one-line descriptors) *before* you pay an LLM summarizer. Cheap before
   smart.
5. **A single tool output or artifact is enormous?** → **offload** the bytes to storage,
   keep a short reference in context, and **page it back** only when needed.
6. **Do latency and cost matter?** → be **cache-aware**: every compaction invalidates the
   KV cache, so *schedule* compaction, don't reflexively fire it every turn.
7. **Whatever branch you took:** *instrument it as you build it.* Measure whether the
   compaction changed the model's output, whether you dropped something that was needed
   later, and make every compaction **observable** and **traceable**. That feedback loop
   is how a non-expert earns a defensible default — and it is the spine of this course.

One point about honesty, because this field is full of confident claims: compression is
not free and not always right. A summary is a lossy copy; truncating a tool result can
corrupt the very file the model is reading; and an LLM summarizer can cost more than the
tokens it saves — recent work finds that simply *masking* stale tool observations matches,
and sometimes beats, LLM summarization at half the cost. The course treats every "compress
it" reflex as a tradeoff to be measured against a baseline, and it keeps returning to the
cheapest move of all — **the tokens you never generate.** Sometimes the answer is not to
compress a giant turn but to *decompose the task* so the giant turn never happens.

## What's new beyond the foundations setup

Same house style: hosted vLLM (`gpt-oss-120b`), OpenAI SDK, thin dependencies, you write
every line. Unlike the Agent Memory course, this one adds **no external backend** — no
database, nothing to install beyond the foundations `requirements.txt`. It introduces one
small shared helper, [`examples/common_context.py`](examples/common_context.py):

- a **token estimator** — a word/character heuristic, in keeping with the foundations
  no-`tiktoken` stance (§4): when an exact count matters we ask the *server* (response
  `usage`, or vLLM's `/tokenize`); for budgeting math the heuristic is enough; and
- a tiny on-disk **blob store** (plain files / SQLite, like Agent Memory Unit 2) for the
  offloading-and-paging unit.

The foundations `examples/common.py` (`get_client`, `MODEL`, `EMBED_MODEL`) is reused
as-is for the compressor-model calls. Every script **skips cleanly** when the LLM env
isn't set, so you can read any unit without an endpoint running.

```bash
# Reuse your foundations .env (OPENAI_BASE_URL / OPENAI_API_KEY / MODEL).
# Nothing else to install — the compression units run on the foundations setup.
set -a; source ../.env; set +a
```

## How this course works

Same as the foundations course: **you write the code** in your own `work/` folder, running
each script as you go; a reference solution for everything lives under `examples/NN/`. Each
unit **builds** a piece, **decides** a tradeoff, and **cites** the SOTA it draws on — and
the whole arc converges on a single, defensible default: a layered compaction strategy
that does the least it can get away with, preserves what matters, and measures the rest.

## Outline

**Observability is a through-line, not a chapter.** Every unit builds the instrumentation
for the mechanism *alongside* the mechanism — a token meter, a compaction record, a
before/after trace of what the model saw and how its output changed. Compression you can't
see is compression you can't trust, so the telemetry is first-class code here, not an
afterthought. This course follows the repo's [Observability Standard](../OBSERVABILITY.md):
a per-lesson `Observe` note, and the joinable `session_id`/`trace_id`/`step` log line reused
from foundations §10. Unit 11 consolidates it into a quality-and-feedback harness, but you
start measuring in Unit 1. (Observability and evaluation are deep enough to deserve their own
course one day; this one keeps them welded to the compression they watch.)

The arc is 13 standalone units (0–12). *(Authoring in progress — links appear as units
land.)*

0. **[The Context Problem](lessons/00-the-context-problem.md)** — the window as a budget; context rot, lost-in-the-middle, primacy/recency; compression vs. memory; the thesis stated. ✅
1. **[Measuring the Window](lessons/01-measuring-the-window.md)** — token accounting without `tiktoken`; where the budget goes (system / tools / history / tool outputs); build a context meter. ✅
2. **[The Cheapest Compression Is None](lessons/02-the-cheapest-compression-is-none.md)** — the cost of compressing too early (quality *and* cache); when *not* to compress; headroom thinking. ✅
3. **[Drop & Window: The Safe Baseline](lessons/03-drop-and-window.md)** — eviction policies, drop-oldest, trim priority (history → memory → tool defs), the sliding window (reuses §13). ✅
4. **[Summarizing Evicted Turns](lessons/04-summarizing-evicted-turns.md)** — structured summarization; a 4-section schema (Decisions / Entities / Facts / Open Items); a cheap compressor model; async; graceful fallback. ✅
5. **[Head, Middle, Tail](lessons/05-head-middle-tail.md)** — the anchored-preservation invariant: keep head + tail verbatim, compress only the middle. ✅
6. **[Cheap Before Smart: The Deterministic Pre-Pass](lessons/06-deterministic-pre-pass.md)** — collapse large tool outputs to one-line descriptors before the summarizer; observation masking vs. LLM summarization. ✅
7. **[When to Fire: Triggers & Async Compression](lessons/07-triggers-and-async.md)** — threshold triggers (soft async / hard sync), the re-fire cursor, non-blocking background compression, latency. ✅
8. **[Offloading & Paging: Gist Memory](lessons/08-offloading-and-paging.md)** — store full bytes externally, keep a short reference, page back on demand; the read→edit dependency hazard. ✅
9. **[Cache-Aware Compaction](lessons/09-cache-aware-compaction.md)** — KV-cache & prefill economics; the byte-identity invariant; volatility-gradient layout; why compaction breaks the cache; cost-optimal scheduling. ✅
10. **[Prompt-Level Compression](lessons/10-prompt-level-compression.md)** — semantic / perplexity-based compression (LLMLingua); system-prompt token trimming; the robustness caution for small models. ✅
11. **[Measuring Compression Quality](lessons/11-measuring-compression-quality.md)** — consolidate the through-line into a harness: the feedback loop (did you drop something referenced later?), before/after token curves, did the *output* change, a no-regression gate. ✅
12. **[The Measured Default](lessons/12-the-measured-default.md)** — the mechanism taxonomy; surfacing compaction to the user (meters, traces); wire it all together + the decision tree + when *not* to compress (decompose instead).

Start with [`lessons/00-the-context-problem.md`](lessons/00-the-context-problem.md).
