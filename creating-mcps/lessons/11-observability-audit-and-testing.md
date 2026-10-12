---
title: "Observability, Audit and Testing"
linkTitle: "11. Observability, Audit and Testing"
weight: 11
---

**Goal:** know what the gateway did, for whom, and why, and know when a change breaks it.
You'll:
- carry one trace across the client, the SDK and the server, and see the SDK's own spans;
- ship every joinable record to OpenSearch without slowing or failing a request;
- write an audit log that records every call, allowed or refused, and withholds a result it
  can't record;
- build per-caller metrics and a service-level objective from the gateway's own telemetry;
- pin the server's contract with snapshot tests, its decisions with behaviour tests, and the
  model's tool choices with an eval gate.

**Where this fits:** Unit 1 stamped every request with a session, a trace and a step. Units 4
to 9 added fields to that record: what a query cost, who the caller was, what the policy
decided, which limit said no. Unit 10 named MCP08, *Lack of Audit and Telemetry*, as the
category this unit answers. Unit 12, the capstone, is reviewed against what this unit records.

No model is needed, except for the eval gate's second half.

## One record, three readers

The same request matters to three people, and each wants something different:

| Reader | Asks | Needs | Store here |
|---|---|---|---|
| Operator | Is it healthy? Who's using it? What's slow? | aggregates, recent, may be sampled | telemetry in OpenSearch |
| Auditor | Who saw this host, as whom, and why was it allowed? | every call, kept for months, exact | `audit.tool_calls` in Postgres |
| Developer | Did my change break a tool, a rule or the model's choices? | tests that fail in CI | snapshots, behaviour tests, evals |

Telemetry may drop a record under load; an audit log may not. Telemetry is kept for weeks;
an audit log for as long as policy says. That's why they're two stores with two sets of
rules, written from the same joinable record.

## Trace context across the boundary

The 2026-07-28 spec carries W3C trace context in each request's `_meta` (SEP-414), and
deprecates MCP's own logging capability in favour of stderr and OpenTelemetry. The Python SDK
does more than carry it: it opens a span itself. Every request a client sends gets a CLIENT
span, `MCP send tools/call get_asset`, whose context it injects into `_meta.traceparent`.
Every request a server answers gets a SERVER span, `tools/call get_asset`, parented on that
context. With only `opentelemetry-api` installed, these spans are no-ops. Configure a tracer
provider and they're real:

```bash
python examples/11/sdk_spans.py --check
```

It serves a one-tool server over HTTP in the same process, keeps the spans in memory, and
checks that four things agree on one trace: the host's own span for the investigation, the
SDK's client span, its server span, and the joinable record the server logged. The record's
new `parent_span_id` is the client span, read from the same `traceparent`.

> **Observe:** once a tracer provider is configured, the SDK writes `_meta.traceparent` from
> the current span, over whatever the caller put there. Unit 1's `trace_meta()` makes its own
> trace id, which is right for a host without tracing and wrong for one with it. The traffic
> generator in this unit opens a span per investigation and takes the trace id from it, so
> its records join either way.

`telemetry.to_span()` turns a joinable record into the same span, named and attributed as
the SDK names it: `mcp.method.name`, `mcp.protocol.version`, `gen_ai.tool.name`,
`gen_ai.operation.name` and `error.type` (`tool_error` for a result with `isError`, the
JSON-RPC code for a protocol error, the exception's class for a crash). OpenTelemetry's
conventions for MCP are at Development stability, so expect renames; keeping the mapping in
one function means one place to change.

## Shipping the record

Since Unit 1 each record has gone to stderr. With three replicas on a shared server, stderr is
three files nobody reads together. `examples/11/telemetry.py` adds a sink: each finished record
is also put on a queue, and a background thread ships the queue in bulk to a daily index,
`mcp-telemetry-YYYY.MM.DD`.

- **As whom.** The gateway's own login, `mcp_service`, may create and write `mcp-telemetry-*`
  and nothing else. The auditor's role may read it. Neither can touch the other's indexes.
- **Never in the way.** The sink never blocks a request and never raises into one. When the
  queue is full it drops the record and counts it; when the cluster is away it counts the
  failure and keeps serving.
- **Not silent.** The first failure, and every thousandth after, is a line on stderr with the
  reason. Telemetry that fails quietly looks exactly like a quiet day.
- **Typed.** `env.py` installs an index template first: strings are keywords, `ms` is a
  number, `@timestamp` is a date. Without it the first record to arrive decides each field's
  type, and an aggregation over `ms` may find a string.

> **Security:** the first version of this sink shipped nothing, and said nothing. A bulk
> request is a cluster-level action in OpenSearch, so a role with write permission on an index
> pattern still can't send one until it's granted `indices:data/write/bulk`. Each document in
> the bulk is then checked against the index permissions, so the gateway still can't write
> anywhere but its own telemetry. Grant the cluster action, not a wider index pattern.

`examples/11/observed_server.py` is Unit 9's scaled server with the sink and the audit writer
turned on. A uvicorn worker ends without running `atexit` handlers, so the server also ships
what's still queued when it's told to stop.

## The audit log

`audit.tool_calls` has been in the CMDB since Unit 0, empty. `examples/11/audit.py` fills it:
one row per `tools/call`, written by the gateway's login, which may insert there and do
nothing else, and read by the auditor, who may select there and do nothing else.

Each row has the caller, the persona the backend saw, the token id, the policy's verdict and
rule, the approval ticket if there was one, the outcome, and the session, trace and step that
join it to everything else. It has the call's **arguments**, because "who looked at
`db-prod-01`?" is the question, and never its **results**, which would make the audit log the
most sensitive table in the estate.

Where it sits matters. The middleware order, outermost first:

```text
[the SDK's own: OpenTelemetry, request state] -> joinable_log -> audit -> identity
    -> envelope -> [cache] -> tool (with the policy inside)
```

Inside the joinable log, so the record says whether the audit row was written. Outside the
identity layer, so it sees the calls identity refuses before the policy runs, like a
contractor calling a tool they aren't granted. It reads what the inner layers decided from
the request's log notes, so no layer has to know the audit exists.

**Fail closed.** If the row can't be written, the result is withheld:

```text
This call couldn't be recorded in the audit log, so its result is withheld. Try again
shortly, or tell the user the audit log is unavailable.
```

`MCP_AUDIT_FAIL=open` returns the result anyway and logs the failure. Closed is right for a
gateway to personal data; open may be right for a read-only status page. Decide it, write it
down, and test it: `test_no_audit_row_no_result` does.

> **Limits:** the row is written after the call, so failing closed withholds a result but
> can't undo a change. For tools that change something (Unit 10's actions, E1's backend APIs),
> write an "attempted" row before the call and an outcome row after, and refuse the call when
> the first can't be written.

```bash
python examples/11/audit.py --host db-prod-01      # who looked at it, as the auditor
python examples/11/audit.py --trace <trace_id>     # one investigation, step by step
python examples/11/audit.py                        # calls per caller and verdict
```

## Per-caller metrics

`examples/11/traffic.py` runs a compressed day against the observed servers: Priya, an L1
analyst, triages hosts including one she isn't allowed to see; Marc works the planted
incident; Sam, a contractor with no grants, tries anyway; and a team of L3 agents loop over
the same playbooks. Every call carries its session, trace and step.

```bash
python examples/11/traffic.py --agents 4 --seconds 15
python examples/11/metrics.py --since now-15m
```

`examples/11/metrics.py` reads the telemetry as the auditor, with OpenSearch aggregations
only: no metrics pipeline and no second copy of the data.

- **By tool:** calls, refusals, errors, p50/p95/p99 latency.
- **By caller:** calls, denials, busy refusals, failures, result tokens, p95.
- **Refusals by reason:** the policy rule or the limit that said no.
- **Requests by method:** `tools/call` next to everything else.
- **One objective:** 99% of the calls the policy allowed succeed within a latency, and how
  much of the error budget the window has spent.

Two decisions shape every number. First, a **refusal isn't an error**. When the policy denies
Sam or a limit turns away a runaway agent, the server did its job; counting those as errors
makes the error rate a measure of how many people tried something they shouldn't. Second, a
**tool error is a success** for the objective. "No asset named `db-prod-01`" is a good answer
the model can act on. What spends the budget is a crash, a protocol error, or an answer slower
than the objective.

> **Observe:** look at requests by method. In this course's runs, `tools/list` requests
> roughly equal `tools/call`: the SDK client re-reads the tool list around calls (among other
> things, to validate structured results against output schemas). That's real load on the
> gateway, and the identity layer filters the list per caller each time. Per-tool metrics
> never show it; per-method metrics do.

## Testing the server

Three kinds of test, each catching what the others can't. All of them run in memory against
the gateway, with the estate behind it.

```bash
pytest examples/11/tests
```

**Contract tests** (`test_contracts.py`). A tool's name, description and schemas are the
server's API, and the model is its client. A renamed argument, a reworded description, or a
tool that appears for the wrong group changes what agents do without failing a unit test. Each
group's `tools/list` (L1, L3, audit and contractors on the CMDB; L1 and L3 on the SIEM) is
snapshotted in `examples/11/contracts/`, reviewed like code, and compared on every run. A
difference fails with what changed:

```text
siem as soc-l1:
  event_histogram: argument changed: dataset
```

That one is the L1 analyst suddenly being offered the `edr` dataset: a policy change, caught
by an API test. When a change is intended, regenerate and commit the snapshots with it:

```bash
UPDATE_CONTRACTS=1 pytest examples/11/tests/test_contracts.py
```

**Behaviour tests** (`test_behaviour.py`). Each pins a decision an earlier unit made: a tool
you aren't granted is indistinguishable from one that doesn't exist (Unit 7); a restricted
host's logs stay restricted (Unit 7); L1 never sees contact details (Unit 7); every call is an
audit row joinable by trace, and no row means no result (this unit). A refactor that quietly
undoes one of them fails here.

**The eval gate** (`eval_gate.py`). Unit 6's tool-selection eval, as a merge gate. A model's
choices vary between runs, so the gate runs the golden set several times and compares each
metric's mean with a floor, and the worst single run with a lower one. The floors live in
`eval_floors.json` and come from your own baseline, a margin below what your model measured:

```bash
python examples/11/eval_gate.py --repeats 5 --baseline    # measure, write the floors
python examples/11/eval_gate.py                           # the gate
```

Its first half, checking that the golden set still names tools and arguments that exist,
needs no model and runs in CI. The second half needs the model, and says it was skipped
without one rather than passing silently.

> **Security:** the tests in this unit are also MCP08 evidence. "Every call is recorded" is a
> claim; a test that makes the audit store fail and checks that no result leaves is proof.

## Lab: the gateway's own dashboard

Open the notebook ([`labs/11_observability.py`](../labs/11_observability.py)) with marimo:

```bash
marimo edit labs/11_observability.py
```

It has five parts:

1. A day at the SOC: the people and the agents, with the audit set to fail closed or open.
2. The dashboard: calls over time, the objective and its budget, and the tables by tool, by
   caller, by refusal reason and by method.
3. One investigation, end to end: what the client recorded, what the servers recorded, what
   the audit log kept, and the same records as spans.
4. The auditor's question: who looked at a host.
5. The spans the SDK already makes, checked against the record.

## Challenges

1. **Attempted, then done.** Change the audit writer to write an `attempted` row before the
   call and complete it after, and to refuse the call when the first write fails. *Success:*
   with the audit store down, `actions_server.py`'s `set_incident_status` changes nothing,
   and `test_no_audit_row_no_result` still passes.
2. **Export the spans.** Install `opentelemetry-exporter-otlp`, run an OpenTelemetry collector,
   and configure the observed server's tracer provider to export to it. *Success:* the SDK's
   server spans and the host's client spans show as one trace in the collector's backend.
3. **A per-caller objective.** Add `slo_by_caller()` to `metrics.py`: the same objective,
   computed for each caller. *Success:* the lab lists which callers spent the budget, and
   a runaway agent from Unit 8's load generator is at the top.

## Recap

- **One record, three readers.** Telemetry for operators, an audit log for auditors, tests for
  developers, all from the same joinable record.
- **The SDK traces for you.** It opens client and server spans and carries the trace in
  `_meta`; configure a tracer provider, and take your trace ids from your spans.
- **Telemetry never gets in the way, and never fails quietly.** Queue, ship in bulk, drop and
  count, say so on stderr.
- **The audit log is not telemetry.** Every call, arguments not results, written by a login
  that can only insert, read by one that can only select, and no row means no result.
- **A refusal isn't an error.** Count them apart, and set the objective on allowed calls.
- **Test the contract, the decisions and the choices.** Snapshots of each caller's tool list,
  behaviour tests for each rule, and an eval gate with floors from repeated runs.

## Next

**[Unit 12: The Measured Default (Capstone)](12-the-measured-default.md)**: a SOC / IT-ops
gateway that triages the planted incident end to end and proposes containment, reviewed for
production readiness against everything the course has measured.
