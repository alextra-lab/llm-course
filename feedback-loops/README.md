# Feedback Loops: Earning Autonomy by Being Observable

The **fourth** course in this repo — a hands-on follow-on to the
[foundations course](../README.md). The other courses make observability a *through-line*;
this one makes it the **subject**. It is the "Course 4 (Observability & Feedback)" the repo's
[Observability Standard](../OBSERVABILITY.md) anticipates.

Its hook is **feedback loops** — the machinery that lets an agent act on what it sees: block a
runaway tool call, deny a call that would blow the budget, critique its own turn, propose a fix.
Its core is **observability** — bubbling up the telemetry that makes every harness and model
decision visible. The two are one idea: a feedback loop is a controller, and **a controller you
can't observe is one you can't trust or improve.**

> **A note on these courses.** This material is based on my own evolving experience building AI
> applications and working with LLMs. It's practical and opinionated, not authoritative — the
> field moves quickly, and some choices here will date or differ from yours. Verify anything
> before relying on it in production.

## Who this is for

This course **assumes the foundations course.** It leans on §10 (observability and logging —
the joinable `session_id`/`trace_id`/`step` line you reuse here), §13 (conversation state), and
§23 (agents and the tool-use loop), and does *not* re-teach them. If those aren't familiar, do
the foundations course first — start at [`../lessons/01-hello-world.md`](../lessons/01-hello-world.md).

It is a sibling of the [Agent Memory](../agent-memory/README.md) and
[Context Compression](../context-compression/README.md) courses — read them in any order. Memory
is what an agent knows *across* sessions; compression keeps *one* session in budget; this course
is about how an agent **watches itself and acts on what it sees**.

The reader it's written for is the developer that AI-assisted (and AI-*driven*) development has
made complacent: let the model + harness + memory do their magic, and treat the result as a
**black box**. This course teaches the SRE's instinct against that — instrument first, automate
second — because the black box is exactly what you can't debug at 3am.

## The thesis

This course is **measured, not authoritative**. I am a student of this material, not an expert in
it. Rather than assert that agents should be autonomous, it argues that autonomy is *earned* — and
the thing that earns it is observability. A feedback loop senses, decides, and acts; this course
instruments all three, and the rule it walks toward is: **don't ship a black box — you earn the
right to close a loop automatically by being able to observe it.**

It argues *toward* that default down a decision tree — the **autonomy gradient**:

1. **Can you see the decision at all?** No → you have no business automating it. Instrument first.
2. **Is the signal joinable?** A loop is only as good as its signal — stamp `session_id` /
   `trace_id` / `step` so the signal feeding the loop is precise (garbage signal, garbage control).
3. **Is the action narrow, deterministic, reversible?** → close the loop *in-turn* with a gate
   (the **reflex** tier — a runaway-loop block, a budget denial).
4. **Is the action a judgment over the whole turn?** → reflect, but feed it back carefully —
   dedup before you act (the **reflective** tier).
5. **Is the action broad or high-stakes** (changing the agent itself)? → keep a **human in the
   loop**; the human's verdict is signal too (the **deliberative** tier).
6. **Are your loops themselves observed?** → watch the apparatus — the observer must be observed
   (the **meta** tier).
7. **Crossing process / service / substrate boundaries?** → adopt the **standard** (OpenTelemetry)
   so the signal stays joinable across systems.
8. **Whatever tier you're in:** measure the loop's effect on the output before you trust it.
   *Earn autonomy by being observable.*

A point about honesty, because this field oversells autonomy: most "self-improving agent" loops
are not closed at all — they are **open loops a human closes.** This course is careful to mark
which loops act automatically (and ship today) and which still need a human, and it treats every
"let it decide for itself" reflex as a tradeoff to be earned with telemetry, not assumed.

## What's new beyond the foundations setup

Same house style: hosted vLLM (`gpt-oss-120b`), OpenAI SDK, thin dependencies, you write every
line. Like Context Compression, this course adds **no external backend**. It introduces one small
shared helper, [`examples/common_loops.py`](examples/common_loops.py):

- a tiny **trace context** — `session_id` / `trace_id` / `step` minted and propagated by hand
  (the foundations §10 tuple, made first-class), so every record this course writes is joinable;
- a `log_event(operation, **fields)` line that stamps that tuple onto one JSONL record per
  operation.

The foundations [`examples/common.py`](../examples/common.py) (`get_client`, `MODEL`) is reused
as-is for the units that call a model. **Many units run fully offline** — the trace primitive, the
gates, the finite-state machine, and the hysteresis logic are pure Python and need no endpoint.
The reflection and eval units do call a model, and every script **skips cleanly** when the LLM env
isn't set, so you can read any unit without a server.

```bash
# Reuse your foundations .env (OPENAI_BASE_URL / OPENAI_API_KEY / MODEL).
# Nothing else to install — the feedback-loop units run on the foundations setup.
set -a; source ../.env; set +a
```

## How this course works

Same as the other courses: **you write the code** in your own `work/` folder, running each script
as you go; a reference solution for everything lives under `examples/NN/`. Each unit takes **one
real control loop**, builds it, and instruments it — naming the **industry term** the world uses
and grounding it in a **real artifact** from a production agent harness, because that harness
reinvented these control-theory and autonomic-computing patterns by necessity. The arc climbs the
autonomy gradient and converges on a single, defensible default in Unit 12.

## Outline

**Observability is the core, not a chapter.** Every unit builds the instrumentation *with* the
loop — a joinable trace, a structured event, a gate verdict, a reflection record — following the
repo's [Observability Standard](../OBSERVABILITY.md): a per-lesson `Observe` note and the joinable
`session_id`/`trace_id`/`step` line from foundations §10. A loop you can't see is a loop you can't
trust, so you start measuring in Unit 1.

The arc is 13 standalone units (0–12), and the course is complete.

0. **[The Loop You Can't See](lessons/00-the-loop-you-cant-see.md)** — the black-box temptation; the SRE stance; sense → decide → act; the autonomy gradient; the thesis stated; a runaway-loop war story. ✅
1. **[Joinable Signal: Trace & Session IDs by Hand](lessons/01-joinable-signal.md)** — build the correlation primitive by hand; garbage signal → garbage control; the joinable JSONL line (§10). ✅
2. **[An Event Vocabulary, Not Log Lines](lessons/02-event-vocabulary.md)** — semantic events vs ad-hoc logs; designing your event catalog; separating organic from background (system) traces. ✅
3. **[Spans & the Latency Breakdown](lessons/03-spans-and-latency.md)** — time each phase of a turn; per-operation spans; where the turn actually spends its time. ✅
4. **[The First Closed Loop: a Runtime Gate](lessons/04-runtime-gate.md)** — a finite-state gate that blocks a repeating tool call; oscillation damping; the gate emits its own verdict. ✅
5. **[Budget as Feedforward Control](lessons/05-budget-feedforward.md)** — reserve / commit / refund; deny *before* you overspend; acting on projected cost, not measured overspend. ✅
6. **[Reflection: Self-Critique from Traces](lessons/06-reflection.md)** — read your own trace and critique the turn; Reflexion / Self-Refine / the evaluator-optimizer loop. ✅
7. **[Closing the Reflective Loop](lessons/07-closing-the-loop.md)** — feed the deduplicated reflection back into the next prompt; the cleanest output → future-behavior loop. ✅
8. **[Hysteresis: Dedup & Promotion](lessons/08-hysteresis-dedup.md)** — don't act on one noisy reading; fingerprint and count; promote a proposal only after it recurs. ✅
9. **[Human in the Loop, Async](lessons/09-human-in-the-loop.md)** — a ticket queue as the approval channel; the human's verdict becomes signal; closed vs human-closed; shipped vs aspirational. ✅
10. **[Watching the Apparatus](lessons/10-watching-the-apparatus.md)** — meta-monitoring; the observer that checks the observers (joinability); MAPE-K and homeostasis. ✅
11. **[Meeting the Standard: OpenTelemetry at the Boundary](lessons/11-opentelemetry.md)** — map your hand-rolled trace onto OTel + the GenAI semantic conventions; the adopt-vs-roll-your-own decision at the cross-substrate boundary. ✅
12. **[The Measured Default](lessons/12-the-measured-default.md)** — the autonomy-gradient decision tree; evals as hypothesis, not gate; which loops to auto-close and which to keep human-closed. ✅

Start with [`lessons/00-the-loop-you-cant-see.md`](lessons/00-the-loop-you-cant-see.md).
