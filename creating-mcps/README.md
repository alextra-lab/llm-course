# Creating MCPs: Safe, Observable, Bounded Servers for the Enterprise

The **fifth** course in this repo, a hands-on follow-on to the
[foundations course](../README.md). Foundations §18 built a Model Context Protocol server, client
and bridge by hand and ended on *"in production, reach for the official SDK."* This course starts
there, and asks the questions the quickstarts skip: what should an agent be allowed to see, how
much of a shared backend may it use, how fast does it answer, and how do you know what it did?

Its examples are the systems a security or IT-operations team actually wires agents into: a
**CMDB** (the IT referential: assets, services, owners, vulnerabilities) in Postgres and a
**SIEM** (server logs, EDR telemetry, incidents) in OpenSearch, with four personas who are allowed
to see different things. Every unit builds on that same estate, and a planted intrusion runs
through it for the capstone to find.

> **A note on these courses.** This material is based on my own evolving experience building AI
> applications and working with LLMs. It's practical and opinionated, not authoritative: the
> field moves quickly, and some choices here will date or differ from yours. Verify anything
> before relying on it in production.

## Who this is for

This course **assumes the foundations course.** It leans on §14–15 (tool calling and the
tool-use loop), §16–17 (sandboxing), §18 (MCP by hand) and §21 (security), and does *not*
re-teach them. If those aren't familiar, do the foundations course first; start at
[`../lessons/01-hello-world.md`](../lessons/01-hello-world.md).

It is a sibling of [Agent Memory](../agent-memory/README.md),
[Context Compression](../context-compression/README.md) and
[Feedback Loops](../feedback-loops/README.md); read them in any order. It reuses Feedback Loops'
instrumentation habits (the joinable trace, an event per decision) but doesn't require it.

The reader it's written for is the developer who has to put an MCP server in front of systems
that matter: a production database, a log cluster, a ticketing or EDR API. The quickstarts get a
tool working in ten minutes. This course is about the next ten weeks: who the server acts for,
what it may return, how it behaves when fifty agents call it at once, and what an attacker can do
through it.

## The thesis

This course is **measured, not authoritative**. It doesn't claim one right way to build an MCP
server; it argues *toward* a default by measuring trade-offs as it goes. Tool designs are
evaluated against a fixed task set over repeated runs, limits are load-tested, and attacks are
actually run. An MCP server is **three things at once**: an interface for a model reader, a
trust boundary, and a shared resource with an operating envelope. The default it walks toward is
a decision tree:

1. **Do you need MCP at all?** A capability reused across agents, apps or teams → MCP. Two local
   functions in one script → call them directly.
2. **How does the agent learn what data exists?** Through a curated **catalog** of tables, indexes
   and fields (descriptions, sensitivity tags, a per-caller allowlist), not a raw dump of every
   schema and mapping.
3. **Where on the permission dial?** Start **restrictive**: curated, task-shaped tools. Move toward
   **permissive** (raw SQL, raw query DSL) only when the work truly needs it, and then with guards
   you can observe.
4. **Which enterprise rules apply?** Every call passes a policy check before it runs (role,
   environment, data classification, change window, approval) and the verdict is logged.
5. **Who is calling, and as whom?** Every call carries a real identity all the way to the backend.
   Never one shared superuser.
6. **What is the operating envelope?** Bound concurrency, connections, rate, query cost, response
   size and time per caller, and say "slow down" so the model backs off instead of retrying.
7. **Is it fast enough to be useful?** Latency budgets, pooling, caching and pagination are
   measured, not guessed.
8. **Can you see it?** Every request leaves a joinable, auditable record, and tool-selection
   quality is measured, not assumed.
9. **Has it been attacked?** Run the OWASP MCP Top 10 against it before anyone else does.

## What's new beyond the foundations setup

Same house style: hosted vLLM (`gpt-oss-120b`), OpenAI SDK, thin dependencies, you write every
line. Four things are new.

- **The current protocol.** The course targets the **2026-07-28 MCP specification** (stateless
  requests, no initialize handshake, capabilities on every request) and the **Python SDK v2**
  (`mcp>=2`). §18's hand-rolled server used the 2024 handshake; Unit 1 puts the two side by side.
- **Python 3.13 or newer.** This course alone; the other courses still run on 3.9+.
- **A local estate.** One `docker compose` brings up **Postgres 17** (the CMDB) and
  **OpenSearch 3** (the SIEM) with seeded, deterministic data, four personas, and row-, column-,
  index- and field-level security already enforced. It runs on a laptop, or once on a shared
  server for a whole class. See [`env/README.md`](env/README.md).
- **marimo labs.** Each unit has a reactive [marimo](https://marimo.io) notebook under `labs/`
  for the interactive part: protocol inspectors, sliders, load generators, the chat host, the
  attack suite. Notebooks are plain `.py` files, so they diff and review like code. Your MCP
  *servers* stay ordinary Python files; the notebook only drives them.

The shared helper is [`examples/common_mcp.py`](examples/common_mcp.py): it connects to the
estate **as a persona** (`pg_connect`, `os_client`) and reuses Foundations'
[`examples/common.py`](../examples/common.py) for the model. Units that call the model skip
cleanly when the LLM env isn't set.

```bash
cd creating-mcps
python3.13 -m venv .venv && . .venv/bin/activate
pip install -e .
cp env/.env.example env/.env          # laptop defaults work as-is
python env/env.py up                  # Postgres + OpenSearch (Docker)
python env/env.py seed                # deterministic data, about a minute
python env/env.py check               # what works, and which units need what
```

## How this course works

Same as the other courses: **you write the code** in your own `work/` folder, running each script
as you go; a reference solution for everything lives under `examples/NN/`. Each unit ends with a
marimo lab and challenges with a stated *Success:* condition.

Every unit carries three notes, the course's through-lines:

| Note | Says what |
|---|---|
| `Security` | what this capability lets an attacker reach through the model, and how the unit closes it |
| `Observe` | what the unit emits, and which question that telemetry answers, per the repo's [Observability Standard](../OBSERVABILITY.md) |
| `Limits` | new for this course: what the unit costs (latency, tokens, connections, cluster load) and what bounds it |

## Outline

The **core path** is 13 units plus references. Units are written in order; ✅ marks the ones that
are published.

0. **[The Problem and the Thesis](lessons/00-the-problem-and-the-thesis.md)**: why MCP, the
   three-things-at-once view, the estate, the decision tree; the same question asked as three
   people. Lab: raw JSON-RPC frames against §18's server. ✅
1. **[The Current Protocol and a First Server](lessons/01-the-current-protocol-and-a-first-server.md)**:
   the 2026-07-28 model (stateless requests, `server/discover`, no sessions),
   tools/resources/prompts, caching hints; a first CMDB server with SDK v2 and a joinable log.
   Lab: a protocol inspector, §18 and SDK side by side. ✅
2. **[The Host Side](lessons/02-the-host-side.md)**: the SDK v2 `Client`, bridging tools into
   `gpt-oss-120b`, several servers at once, elicitation with `input_required`. Lab: a chat host
   with a tool-call side panel. ✅
3. **[Catalogs and Discovery](lessons/03-catalogs-and-discovery.md)**: raw `information_schema`
   and `_mapping` vs a curated catalog with descriptions, sensitivity tags and a per-caller
   allowlist; its token cost. Lab: no catalog vs raw vs curated, measured. ✅
4. **[Searching Logs and Incidents](lessons/04-searching-logs-and-incidents.md)**: bounded
   time windows, aggregate first then drill down, shaping and paginating results. Lab:
   investigate the planted incident by hand, then let the host do it. ✅
5. **[The Permission Dial: Restrictive vs Permissive Tools](lessons/05-the-permission-dial.md)**:
   the CMDB and OpenSearch four ways (raw, guarded, task-shaped, and task-shaped with an
   approval hatch), measured on success, tokens, latency and blast radius. Lab: guards by
   hand, blast-radius probes and the comparison grid. ✅
6. **[Designing Tools for a Model Reader](lessons/06-designing-tools-for-a-model-reader.md)**:
   names, descriptions, parameters, schema vs prose, structured output, annotations, errors
   written as instructions, the token cost of `tools/list`. Lab: the same tools described four
   ways, linted, error-audited and A/B'd on tool selection and recovery. ✅
7. **[Identity, Least Privilege and Enterprise Rules](lessons/07-identity-least-privilege-and-enterprise-rules.md)**:
   who is calling, no token passthrough, per-caller tool lists, identity carried to Postgres
   and OpenSearch, a policy layer. Lab: four layers of refusal. ✅
8. **[Resource Management: Many Callers, Finite Backends](lessons/08-resource-management-many-callers-finite-backends.md)**:
   pools, per-caller rate limits, quotas and cursor limits, backpressure the model respects,
   timeouts at every layer, query-cost gates and per-tool caps. Lab: a load generator with
   sliders, a runaway agent included. ✅
9. **[Performance and Scale](lessons/09-performance-and-scale.md)**: stdio vs Streamable HTTP,
   stateless replicas, pools that fit a budget, limits and a cache shared through Valkey (and a
   cache that leaks), OpenSearch choices, packaging. Lab: one instance vs three, with and
   without the cache. ✅
10. **[Attacking and Defending MCP](lessons/10-attacking-and-defending-mcp.md)**: the OWASP MCP
    Top 10 mapped onto the gateway, and a host whose defences switch on one at a time: an
    allowlist, description vetting and pinning, egress, redaction, fencing, approvals and an
    audit record. Lab: the guarded host. *In progress: the negative cases are placeholders.*
11. **[Observability, Audit and Testing](lessons/11-observability-audit-and-testing.md)**: the
    SDK's own spans and one trace across the boundary, telemetry shipped to OpenSearch, an audit
    log that fails closed, per-caller metrics and an objective, contract snapshots, behaviour
    tests and an eval gate. Lab: the gateway's own dashboard. ✅
12. **[The Measured Default (Capstone)](lessons/12-the-measured-default.md)**: a SOC / IT-ops
    gateway composed from every unit's layers, a triage of the planted incident end to end that
    *proposes* containment for a person to approve, and a production-readiness review that
    reports pass, warn, fail and an honest todo. Lab: the measured default. ✅
13. **[References](lessons/13-references.md)**: the spec and SDK docs, identity standards,
    OWASP, observability, Postgres and OpenSearch, an Elasticsearch porting note, and the
    established MCP courses as prep and complements. ✅

An optional **enterprise track** plugs into the capstone gateway:

- **E1. Backend APIs with Approvals** *(after Unit 8)*: EDR and ticketing APIs, idempotency,
  circuit breakers, destructive actions behind approval and a change-window rule, the Tasks
  extension.
- **E2. The Local Systems Server** *(after Unit 10)*: host triage on the permission dial, behind
  the §16–17 sandbox.
- **E3. Full OAuth for Remote Servers** *(after Unit 7)*: protected-resource metadata, client
  registration, audience-bound tokens, step-up scopes, token exchange.

Start with [`lessons/00-the-problem-and-the-thesis.md`](lessons/00-the-problem-and-the-thesis.md).
