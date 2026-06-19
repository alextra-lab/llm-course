---
title: "Agent Memory"
linkTitle: "Agent Memory"
weight: 30
no_list: true
# Render this course as a Docsy `docs` section (like Foundations). Without this, its pages
# fall back to the generic root baseof, which renders scripts.html via `partialCached` with
# no variant key -- caching the per-page Mermaid gate site-wide and racing it away on build,
# so diagrams ship as raw code. The docs baseof renders scripts.html per page (uncached).
cascade:
  type: docs
---

**Agent Memory: From Chat History to a Knowledge Graph** — the second course, a hands-on,
opinionated follow-on to the [Foundations course](/docs/).

> **Do the Foundations course first.** This course *assumes* it — it leans on §13
> (conversation state), §19 (embeddings), §20 (RAG), and §23 (agents) and does not re-teach
> them. If those aren't familiar, start with [Foundations](/docs/).

It goes deep on what an agent knows **across** sessions: a taxonomy of memory, conversational
ingestion, a real Neo4j knowledge graph, hybrid (graph + vector) retrieval, lifecycle and
decay, measurement, and a single measured, opinionated default. Work through the units in
order — each one builds a piece, decides a tradeoff, and cites the SOTA it draws on.

{{< unit-cards >}}
