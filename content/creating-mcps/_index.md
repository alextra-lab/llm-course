---
title: "Creating MCPs"
linkTitle: "Creating MCPs"
weight: 50
no_list: true
# Render this course as a Docsy `docs` section, like the other follow-on courses (see
# content/feedback-loops/_index.md for why the cascade matters to the Mermaid loader).
cascade:
  type: docs
---

**Creating MCPs: Safe, Observable, Bounded Servers for the Enterprise** — the fifth course, a
hands-on, measured follow-on to the [Foundations course](/docs/).

> **Do the Foundations course first.** This course *assumes* it — it leans on §14–15 (tool calling
> and the tool-use loop), §16–17 (sandboxing), §18 (MCP by hand) and §21 (security), and does not
> re-teach them. If those aren't familiar, start with [Foundations](/docs/).

Foundations §18 built an MCP server by hand and ended on *"in production, reach for the official
SDK."* This course starts there, on the current protocol (2026-07-28) and the Python SDK v2, and
asks the questions the quickstarts skip. Its examples are the systems a SOC or IT-operations team
actually wires agents into: a CMDB in Postgres and a SIEM in OpenSearch, with personas who may see
different things and a planted intrusion to find. An MCP server is three things at once — an
interface for a model reader, a trust boundary, and a shared resource with an operating envelope —
and the course measures its way to a default for all three: catalogs and discovery, restrictive
vs permissive tools, identity and enterprise rules, per-caller limits, performance, the OWASP MCP
Top 10, and audit. Labs are reactive [marimo](https://marimo.io) notebooks; the estate runs on a
laptop or a shared lab server.

{{< unit-cards >}}
