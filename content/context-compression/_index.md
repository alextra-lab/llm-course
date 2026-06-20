---
title: "Context Compression"
linkTitle: "Context Compression"
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

**Context Compression: Keeping a Long Agent Inside the Window** — the third course, a hands-on,
measured follow-on to the [Foundations course](/docs/).

> **Do the Foundations course first.** This course *assumes* it — it leans on §4 (tokens and the
> context window), §11 (prompt caching), §13 (conversation state and history), and §23 (agents),
> and does not re-teach them. If those aren't familiar, start with [Foundations](/docs/).

It is a sibling of the [Agent Memory](/agent-memory/) course, not a sequel — read them in either
order. Memory is about what an agent knows *across* sessions; this course is about keeping *one*
session inside the token budget without losing what it still needs: measuring the window,
dropping and windowing, structured summarization, head/middle/tail preservation, a deterministic
pre-pass, and a default the instrumentation earns rather than the author. The course is being
written unit by unit; work through what has landed in order — each one builds a piece, decides a
tradeoff, and cites the SOTA it draws on.

{{< unit-cards >}}
