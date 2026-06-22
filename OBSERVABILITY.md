---
title: "The Observability Standard"
linkTitle: "Observability Standard"
type: docs
---

A shared, cross-course convention for these courses. **Observability is a first-class
citizen — built *with* the principal code, not bolted on afterward.** A compression you
can't see is a compression you can't trust; a memory you can't trace is one you can't
debug; a quality you can't measure is one you can only hope for. This document defines how
every course threads observability so the lessons stay consistent and the reader builds the
instrumentation habit, not just the feature.

It exists because the field treats instrumentation as an afterthought, and because these
courses argue *from measurement, not authority* — the opinions a course reaches should
belong to its telemetry, not its author.

## The two parts of the standard

### 1. The per-lesson `Observe` note (a structural through-line)

Every course makes observability a through-line the same way it makes **security** one:
with a short, mandatory per-lesson blockquote, announced in the course's Unit 0.

- **Unit 0 announces it**, exactly as it announces the security note. One sentence:
  *"Every later unit has an `Observe` note: it instruments what the unit builds and names
  the question that telemetry answers."*
- **Every later lesson carries a `> **Observe:**` blockquote**, placed near the end beside
  the `> **Security:**` note. It states two things in 2–4 sentences:
  1. **What this unit emits or measures** — the concrete signal (a token meter, a
     compaction record, a recall metric, a joinable log line).
  2. **The loop it closes** — the decision or quality check that signal enables ("did the
     compaction drop something referenced later?", "did recall improve over the baseline?",
     "did the output change?"). Observability is the *feedback loop*, not just the log.

If a unit genuinely builds nothing to observe (a pure "decide" or "taxonomy" unit), its
`Observe` note says what to measure *when you do build it*, and points forward — it is
never omitted. Same discipline as security.

### 2. The canonical joinable telemetry artifact (reused, not reinvented)

Every course reuses the foundations telemetry shape from **[Foundations Lesson 10 — Observability & Logging](lessons/10-observability-and-logging.md)** —
do **not** invent a new one per course. One structured **JSONL record per operation**,
stamped with the joining tuple:

```json
{"session_id": "...", "trace_id": "...", "step": 0, "operation": "...", ...}
```

- **`session_id`** — the whole conversation / user session (stable across turns).
- **`trace_id`** — one logical operation (an agent run, a turn).
- **`step`** — integer ordering within the trace.
- Server ids (`response.id`, `x-request-id`) identify *one* call; the tuple above is what
  ties a whole run together — the "missing-foreign-key" point from [Foundations Lesson 10](lessons/10-observability-and-logging.md).

The reference implementations to copy from:

- **[Foundations Lesson 10 — Observability & Logging](lessons/10-observability-and-logging.md)** — `logged_chat(session_id, trace_id, step, **kwargs)` and the
  joinable-logs section. Source: [`examples/10/log_calls.py`](examples/10/log_calls.py).
- **[Agent Memory Unit 10 — Observability & Privacy](agent-memory/lessons/10-observability-and-privacy.md)** — `log_event(actor, operation, **fields)` extends the same line
  with an `actor`, an `operation`, and **PII redaction at the boundary**. Source:
  [`agent-memory/examples/10/observe.py`](agent-memory/examples/10/observe.py).

Each course adds an **`operation` plus domain fields** on top of the tuple, and may ship a
small `log_event` helper in its `common_*.py`. Domain records seen in the wild:

- **Memory:** `operation` ∈ `write|recall|read_node`, `actor`, redacted `detail`.
- **Compression:** a **compaction record** — `operation="compaction"`, `trigger`
  (soft/hard/scheduled), `strategy` (drop/summarize/head-tail/offload), `tokens_before`,
  `tokens_after`, `kept`/`dropped` (e.g. entities or turns), and a later `referenced_later`
  flag for the quality loop. (This mirrors how a production agent harness records each
  compaction so the four mechanisms can be told apart and surfaced to the user — a session
  meter showing ⚠ quality alerts, ⟳ compaction count, ↻ cache resets.)

## The rules

- **R1 — Build-as-you-go.** The unit that builds a mechanism instruments it *in the same
  unit*. Telemetry is not deferred to a later "observability unit." (A consolidation unit
  may still gather it into a harness — but the emitting happens where the mechanism is
  built.)
- **R2 — Joinable by construction.** Stamp `session_id`/`trace_id`/`step` at the point you
  write the record. No optional ids — an id you can omit is one that's missing exactly when
  you need it.
- **R3 — Close a loop.** Each unit shows at least one thing the telemetry lets you *decide
  or measure*, not just logs you accumulate. Emit → measure the effect on the output →
  decide.
- **R4 — The capstone wires it for real.** The course's final agent must actually emit the
  telemetry and print or measure a quality signal. Prose and diagrams must not claim
  instrumentation the code does not deliver.
- **R5 — Redact at the boundary.** Metadata is high-value and usually safe; *content*
  (prompts, completions, memory text) can carry PII and secrets — redact before it reaches
  a log, and decide retention deliberately. (This is where the `Observe` and `Security`
  notes meet.)

## Per-course application

- **Foundations** — already *implies* observability and meets the bar: a dedicated
  [Foundations Lesson 10 — Observability & Logging](lessons/10-observability-and-logging.md) plus
  deliberate callbacks in Lessons 2, 9, 11, 17, 23 ("make degradation loud"), 24, and 25,
  with the same JSONL+trace artifact recurring across Lessons 10/17/23. It is grandfathered: it
  is not required to add the structural `Observe` note retroactively, though it may.
- **Agent Memory** — **retrofit to first-class.** Today observability is a two-unit
  destination (Unit 9 metrics, Unit 10 telemetry/privacy) and the capstone claims controls
  it does not wire in. Bring it up to standard: announce the `Observe` note in Unit 0, add
  it to every unit, instrument the build units (6 ingestion, 7 retrieval, 8 curation) with
  the joinable line, and make the Unit 11 capstone actually emit telemetry + a recall
  measurement.
- **Context Compression** — **native.** Observability is a stated through-line from the
  start; every unit ships its meter/record alongside the mechanism, Unit 11 consolidates the
  quality-and-feedback harness, and Unit 12 surfaces compaction to the user.
- **Feedback Loops** — **the subject.** This course makes observability the topic itself: every
  unit emits the joinable line and closes a loop with it, climbing the autonomy gradient
  (reflex → reflective → deliberative → meta), with OpenTelemetry at the boundary in Unit 11.

**Feedback Loops (Course 4)** now makes this theme the *subject* rather than a through-line — the
loop, not just the log. The other three keep observability welded to the code it watches; this
standard is the shared contract all four follow.
