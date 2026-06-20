---
title: "Feedback Loops"
linkTitle: "Feedback Loops"
weight: 40
no_list: true
# Render this course as a Docsy `docs` section (like Foundations and Agent Memory). Without
# this, its pages fall back to the generic root baseof, which renders scripts.html via
# `partialCached` with no variant key -- caching the per-page Mermaid gate site-wide and
# racing it away on build, so diagrams ship as raw code. The docs baseof renders scripts.html
# per page (uncached), so the mermaid loader is included reliably.
cascade:
  type: docs
---

**Feedback Loops: Earning Autonomy by Being Observable** — the fourth course, a hands-on,
measured follow-on to the [Foundations course](/docs/).

> **Do the Foundations course first.** This course *assumes* it — it leans on §10 (observability
> and the joinable `session_id`/`trace_id`/`step` log line), §13 (conversation state), and §23
> (agents and the tool-use loop), and does not re-teach them. If those aren't familiar, start with
> [Foundations](/docs/).

The other courses make observability a *through-line*; this one makes it the **subject**. Its hook
is feedback loops — the machinery that lets an agent act on what it sees: block a runaway tool
call, deny a call that would blow the budget, critique its own turn, propose a fix. Its core is
observability — the telemetry that makes every harness and model decision visible. The two are one
idea: a feedback loop is a controller, and a controller you can't observe is one you can't trust.
The course climbs an *autonomy gradient* — reflex, reflective, deliberative, meta — arguing that
autonomy is earned, and the thing that earns it is observability. Don't ship a black box.

{{< unit-cards >}}
