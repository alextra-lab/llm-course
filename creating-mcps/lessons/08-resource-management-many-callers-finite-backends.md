---
title: "Resource Management: Many Callers, Finite Backends"
linkTitle: "8. Resource Management: Many Callers, Finite Backends"
weight: 8
---

**Goal:** put Unit 7's shared servers inside an operating envelope, so that many agents
calling at once can't exhaust the backends, and one agent stuck in a loop can't starve the
rest. You'll:
- measure first, with a load generator that runs many agents against the servers and watches
  the backends while it does;
- replace a connection per call with bounded, shared pools;
- give every caller its own limits: rate, result-token quota, concurrency, and the cursors
  its searches leave open;
- write refusals a model acts on: which limit, how long to wait, or what to ask instead;
- put time budgets at every layer, and restore one Unit 7 lost without anyone noticing;
- price queries before they run, and cap a single tool for everyone.

**Where this fits:** Unit 7 made the servers shared and carried each caller's identity to the
backends through one gateway login. That login is now the only thing Postgres and OpenSearch
see, so their own per-user limits no longer separate one agent from another. This unit
does. Unit 9 then scales the servers out; Unit 11 turns the numbers you collect here into
per-caller metrics; enterprise track E1 adds write APIs, where a retried call can do damage
twice.

No model is needed for this unit.

---

## What's finite

Before choosing limits, list what runs out. For this estate:

| resource | where | how much | what happens when it runs out |
|---|---|---|---|
| Postgres connections | the server | `max_connections` 100 | `too many connections`, for every client of the database |
| ... per login | the role | `mcp_service` 50 | the gateway is refused, every caller at once |
| OpenSearch search threads | each node | 1.5 per CPU, plus a queue of 1,000 | `rejected execution` (HTTP 429) |
| point-in-time contexts | the cluster | `search.max_open_pit_context` 300 | `Trying to create too many Point In Time contexts` (429) |
| time | each query | `statement_timeout`, search budgets | the query is cancelled, the work so far wasted |
| the model's context | the caller | a few hundred thousand tokens | the agent forgets, or fails |

Two things about this list. First, most of it is shared with things that aren't your MCP
server: the SOC's dashboards and the SIEM's own detection rules use the same cluster. A
gateway that takes all 300 point-in-time contexts breaks those too. Second, several of these
are things a call *holds after it returns*: an open connection, an open cursor. A request
rate limit doesn't see them.

## Measure first

`examples/08/loadgen.py` starts both servers with the settings you choose and runs simulated
agents against them. Each agent has its own token (an L3 responder named `agent-NN`), its
own connections, and a loop: pick a call from a realistic mix (look up a host, count assets,
a histogram, top source IPs, a page of events from one day), make it, think for a random
interval, repeat. Optionally, one more agent named `runaway` runs several loops at once and
never thinks: an agent stuck retrying, which every shared server meets sooner or later.

It records every call (latency, and what came back) and samples the backends twice a
second: the gateway's Postgres connections, and OpenSearch's search thread pool and open
search contexts. Then it summarises, for everyone and for the runaway separately:

```bash
python examples/08/loadgen.py --callers 20 --seconds 10
```
```json
{"who": "everyone", "calls": 695, "ok": 695, "ok_per_s": 67.7, "p50_ms": 11.4,
 "p95_ms": 24.9, "refused": {}, "max_pg_connections": 20, "max_open_contexts": 200}
```

Every number in this lesson came from runs like this one on the small seed, with both
servers and the estate on one laptop. Yours will differ; the differences between settings
are what matter, and the lab lets you reproduce them.

## Pools

Unit 7's servers open a Postgres connection and build an OpenSearch client for every call,
and close them after. Each one pays for a TCP connect, a TLS handshake and an
authentication, and under load those set-up costs queue behind each other. With 60 agents
calling every 0.1 s on average:

| | calls/s | p50 | p95 |
|---|---|---|---|
| a connection per call (Unit 7) | 92 | 644 ms | 1,168 ms |
| shared pools | 280 | 74 ms | 235 ms |

Three times the throughput and a ninth of the median latency, from changing nothing but
where connections come from. `common_mcp.use_pools()` sets them up when the envelope server
starts:

```python
_PG_POOL = ConnectionPool(kwargs=_pg_params(GATEWAY, autocommit=True),
                          min_size=pg_size, max_size=pg_size,
                          timeout=pg_wait, reset=reset, name="gateway", open=True)
```

- **Bounded, and full from the start.** `min_size = max_size`: ten connections per server,
  opened at start-up, never more. The pool is the server's promise to Postgres about how
  much of it it will use, whatever the callers do. Twenty in total for two servers, well
  under the `mcp_service` limit of 50, so a second replica (Unit 9) still fits.
- **One login, many people.** Every pooled connection is the gateway. On checkout, the call
  becomes its caller with `SET ROLE` (Unit 7). On return, the pool's `reset` runs
  `RESET ROLE; RESET ALL`, so the next caller never inherits the last one's identity or
  settings. `autocommit` keeps a tool's rollback from undoing the `SET ROLE` halfway through
  a call.
- **A wait with a limit.** When all ten are in use, a call waits up to `pg_wait` (2 s) and
  then gets `PoolTimeout`, which the server turns into "busy". Without the limit, a burst
  queues without end and every caller times out together.
- **OpenSearch** gets one long-lived client per persona (the impersonation header differs
  per persona), each with its own bounded HTTP connection pool (`pool_maxsize`).

### What Unit 7 lost

Unit 0 gave each persona its own `statement_timeout`: 5 s for an L1 analyst, 15 s for the
others. Unit 7 moved the queries behind the gateway login, and the L1 analyst's queries
quietly started running for up to 15 s.

The reason: settings from `ALTER ROLE l1_analyst SET statement_timeout = '5s'` apply when
that role *logs in*. `SET ROLE` changes privileges and nothing else, so through the gateway
every query ran with the gateway's own settings. No test failed: nothing in Unit 7 ran long
enough to notice. `_become()` now reads the persona's settings from `pg_db_role_setting` once
and applies them after `SET ROLE`:

```python
conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(persona)))
for name, value in _ROLE_SETTINGS[persona]:
    conn.execute(sql.SQL("SET {} = {}").format(sql.Identifier(name), sql.Literal(value)))
```

`SELECT pg_sleep(8)` as an L1 analyst through the gateway is now cancelled after 5.0 s. The
lesson is general: when a gateway or proxy takes over the connection, check every limit the
backend applied *per login*, because they now apply to the gateway instead. The connection
limit per persona went the same way, and nothing can restore that one at the backend. That's
the job of the next section.

## Per-caller limits

The backends see one login now, so they can't tell a runaway agent from twenty careful
ones. The server can: it knows the caller from the token. `examples/08/limits.py` checks four
limits on every tool call, in this order:

| limit | default | refuses when |
|---|---|---|
| **rate** | 5 calls/s per caller, bursts of 10 | the caller's request bucket is empty |
| **quota** | 50,000 result tokens per caller per minute | the caller's result bucket is in debt |
| **concurrency** | 2 calls in flight per caller | no slot frees up within 1 s |
| **capacity** | 16 calls in flight per server | no slot frees up within the same second |

Rate and quota are **token buckets**: a bucket holds up to `burst` tokens, refills at `rate`
per second, and each call takes one. A caller that has been quiet can burst; one that keeps
calling gets exactly the rate. The quota bucket is charged *after* the call, with the size of
what it returned, because a request count alone lets one caller pull megabytes in a few
calls. Its balance can go negative: a debt the caller waits off before its next call.

The order matters. The cheap checks (two bucket lookups) come first and refuse without
waiting. Only then does a call queue for a slot, briefly: a second is long enough to absorb a
burst and short enough that a caller learns it's over the limit while it can still do
something else.

The middleware sits *inside* Unit 7's identity layer, so it knows who is calling:

```python
async with LIMITER.admit(caller.sub) as waited:
    log_note(queued_ms=round(waited * 1000, 1))
    result = await call_next(ctx)
```

Ten agents and a runaway with 16 loops, for ten seconds:

| | agents: ok | agents: refused | runaway: ok | runaway: refused |
|---|---|---|---|---|
| no per-caller limits | 329 | 30 | 2,902 | 316 |
| per-caller limits | 380 | 0 | 128 | 4,818 |

Without limits, the runaway made 90% of all calls, and the agents got refusals for something
they didn't do. With limits, the agents' work is unchanged by the runaway's existence, and
the runaway gets its fair share: its rate, no more.

## What a call leaves behind

The refusals the agents got without limits weren't rate or connections. They were this:

```
Trying to create too many Point In Time contexts. Must be less than or equal to: [300].
```

`search_events` (Unit 4) opens a point in time on the cluster for each new search, so the
pages behind its cursor stay consistent while new logs arrive. The point in time stays open
until the caller reads the last page, or until it expires two minutes later. The load
generator's agents, like most real agents, read page one and move on. Every search leaves a
point in time open for two minutes; the runaway left hundreds.

None of the four limits above sees this. They count calls and what calls return, and a call
that leaves a cursor behind has returned. So the limiter counts cursors too:

- **per caller:** at most 10 open cursors (`cursors`), counted as a bucket that refills over
  the two minutes a cursor can stay open;
- **per server:** at most 100 for everyone (`cursors_total`).

The server-wide number needs care. Each point in time holds a context *per shard it covers*,
and the cluster's limit of 300 counts contexts. A day of logs spans two daily indexes, so 100
cursors hold about 200 contexts: below 300 with room for everything else that uses the
cluster. Per-caller limits alone don't protect a backend unless they can't add up past its
limit: 40 callers × 10 cursors is 400.

With 60 agents and no runaway, the cluster limit is reached either way, and the difference is
who says no. Without the envelope, the cluster refused 99 calls and was at 301 contexts. With
it, the server refused 136 calls itself, saying which limit and when to retry, and the
cluster stayed at 217 contexts, with room for the dashboards.

## Backpressure the model respects

A refusal is a message to a model. It has to say three things: that this is a limit and not
a failure, which limit, and what to do.

```
busy: you're calling faster than your rate limit (5/s). Retry the same call after 1 s;
don't retry sooner, and use what you already have meanwhile.
```

```
busy: you have 10 result cursors open. Read to the last page of a search you need, or count
and aggregate instead of listing. Retry the same call after 12 s; don't retry sooner, and use
what you already have meanwhile.
```

The server's refusals come in three kinds, and the wording makes them distinct:

- **busy** means wait, then retry the same call. The wait is in the text, for the model, and
  in `_meta` as `io.creating-mcps/retry_after_s`, for a host that schedules retries itself.
- **too slow** means don't retry as is: ask a smaller question (next section).
- **gated** means the question is too big to ask at all: narrow it.

"error, try again" is the wrong message for all three: a model told to try again does so at
once, which is how one runaway becomes three.

**Why not HTTP 429?** It's the standard status for a rate limit, and the right one for a
plain HTTP API. But the MCP Python SDK client treats a 429 on a request as a transport
failure: the call raises an exception, the host sees an error rather than a tool result, and
the model never reads the message. A tool result with `isError: true` reaches the model. The
per-tool caps below refuse in front of MCP, at the HTTP layer, and still answer with a
JSON-RPC result for that reason.

## Timeouts all the way down

A time budget at one layer only protects that layer. A client that gives up after 10 s while
the database keeps working for 15 has saved nothing. Every layer gets a budget, enforced
where the work happens, and shorter than the patience of whoever is waiting outside it:

| layer | budget | set by |
|---|---|---|
| Postgres statement | 5 s (L1), 15 s (L3, auditor) | the persona's `statement_timeout`, restored above |
| OpenSearch search | 10 s, enforced by the cluster | `cancel_after_time_interval` on every `_search` |
| pool checkout | 2 s | `pg_wait` |
| a slot in the limiter | 1 s | `queue_wait` |
| the client | 300 s to read a response, by default | the SDK's `create_mcp_http_client`: set your own |

The last row is a default worth knowing: the SDK's HTTP client waits up to five minutes for
a response, because a server may hold a stream open. A host that calls tools on behalf of a
person should pass its own, shorter `timeout`.

`cancel_after_time_interval` matters most: OpenSearch's own client timeout only stops the
client waiting, and the search runs on. With the parameter, the cluster cancels the search
itself when the budget runs out. The envelope server adds it to every search through a small
`Transport` subclass, so no tool can forget it.

When a budget runs out, `common_mcp.backend_refusal()` turns the backend's error into the
**too slow** message: "that query ran past its time budget and was stopped. Don't retry it as
is: narrow the window, add a filter, or count before you fetch."

## Price it before it runs

A timeout stops an expensive query after it has used the backend for the whole budget. A
**cost gate** refuses it first, from an estimate that costs almost nothing
(`examples/08/cost_gates.py`).

For SQL, the estimate is the planner's: `EXPLAIN` without `ANALYZE` plans the query without
running it, and reports a total cost and an estimated row count:

| query | cost | rows | verdict |
|---|---|---|---|
| one host by name | 8 | 1 | allowed |
| hosts per service | 20 | 25 | allowed |
| every asset with every CVE (no join key) | 1,270 | 100,000 | refused: rows |
| assets × assets × people | 225,406 | 1 | refused: cost |

The planner's cost unit is arbitrary. Calibrate the ceiling (50,000 here) on your own
queries, as the lab does. And keep both limits: the third query is cheap to plan and still
returns a flood.

Where is it useful? Wherever the SQL varies: Unit 5's guarded tier, and the approval hatch.
Unit 5's task-shaped tools run a fixed set of queries you already priced when you wrote them.

For logs, the estimate is even cheaper. The daily indexes a window touches are known before
the search (Unit 4), and so are their document counts. That's an upper bound on what the
search reads, whatever its filters. The envelope server refuses a log query whose window
covers more than `MCP_MAX_SCAN` documents (5 million by default), with "this window covers 8
daily indexes holding 3,497 auth events (the limit is 2,000). Narrow the window." The counts
are cached for a minute, so the gate itself costs one request a minute.

Estimates are estimates. A gate catches the order-of-magnitude mistakes; the timeouts still
catch the rest.

## One tool, for everyone

Some tools are heavier than the others: an aggregation over a week of logs costs more than a
host lookup. A cap per tool limits how many calls to it are in flight on the server at once,
for all callers together.

The 2026-07-28 transport makes this cheap. Every request carries `Mcp-Method` and `Mcp-Name`
headers (`tools/call`, `event_histogram`), and the SDK server checks they match the body, so a
caller can't dodge a cap by lying in a header. A gate in front of MCP decides from the headers
alone, without parsing a body, before the request reaches the SDK at all
(`ToolCaps` in `envelope_server.py`):

```bash
MCP_TOOL_CAPS=event_histogram=3 python examples/08/envelope_server.py --kind siem
```

With 40 agents calling every 0.05 s on average, a cap of 3 refused 84 `event_histogram` calls
and left every other tool alone. This is the layer a load balancer or API gateway in front of
your MCP servers can enforce too, because it needs nothing from the body.

> **Security:** exhaustion is an attack too (Unit 10).
> A prompt-injected agent told to "search every day of the last month, then do it again" is a
> runaway with intent; the limits here contain it without knowing why it misbehaves. Two
> details matter. Limits are keyed by the token's subject, never by anything the caller sends,
> so a caller can't spread itself over several names. And the pool's reset must run on every
> return: a connection handed back still `SET ROLE` as the last caller would give the next
> one their rows.

> **Observe:** every call's log record now says what the envelope did: `queued_ms` (time
> waiting for a slot), `result_tokens` (charged to the quota), `busy` and `retry_after` on a
> refusal, `gate` and `scan_docs` for the scan gate, `cursor_opened`, and `backend` when the
> backend itself refused. `toolcaps.refused` is logged at the HTTP layer, before MCP. Watch
> `busy` by kind: a rise in `capacity` means the server needs room, a rise in `rate` for one
> caller means that caller needs a word.

> **Limits:**
> - **One process.** The limiter's buckets and slots live in the server's memory. Two
>   replicas give every caller twice the limits. Unit 9 moves the counters to shared
>   storage.
> - **Estimates.** The cursor limit assumes every cursor stays open for its whole two
>   minutes, and the scan gate counts documents the filters would skip. Both refuse a
>   little early, which is the safe direction.
> - **Not a queue.** A refused call is refused: the caller retries. A host with a retry
>   policy (Challenge 1) does better than one without.

> **Porting to Elasticsearch:** the same limits apply. An Elasticsearch point in time also
> holds a search context on every shard it covers until it expires, so count cursors the same
> way and check your cluster's limits on open contexts. `cancel_after_time_interval` is
> OpenSearch's: in Elasticsearch, a search's `timeout` returns partial results instead, and
> the task management API (`_tasks/<id>/_cancel`) stops a running search.

## Lab: many callers, finite backends

Open the notebook ([`labs/08_operating_envelope.py`](../labs/08_operating_envelope.py))
with marimo:

```bash
marimo edit labs/08_operating_envelope.py
```

It needs the estate (`python env/env.py up` and `seed`), set up for Unit 7. It has four
parts:

1. One caller and one token bucket, simulated: move the rate and burst sliders and watch a
   burst and a steady pace get admitted or refused.
2. Price SQL with the planner, and log windows with document counts.
3. Run the load: callers, think time, a runaway, pools, every limit, tool caps and the scan
   gate as controls. Charts of successful and refused calls per second, p95 latency, and the
   Postgres connections and search contexts held; and one example of each refusal, as the
   agent read it.
4. Every run so far, side by side.

Its suggested runs reproduce this lesson's tables.

## Challenges

1. **Retry like you mean it.** Give the load generator's agents a retry policy: on a busy
   refusal, wait `retry_after_s` from `_meta` and retry once. *Success:* with a runaway and
   limits on, the agents' refusals stay at 0, and the runaway's total calls fall by at least
   half, because it now waits.
2. **Free cursors early.** The cursor limit counts a cursor as held for its whole two minutes,
   even when the caller read to the last page and the server deleted it. Make the limiter
   give the slot back when `search_events` returns no `next_cursor` on a page that followed a
   cursor. *Success:* an agent that reads every search to its last page is never refused for
   cursors.
3. **Gate the hatch.** Unit 5's approval hatch runs SQL the model writes. Put `pg_gate` in front
   of it. *Success:* the hatch refuses the cross join in `cost_gates.EXAMPLES` with a message
   saying what to change, and still runs the hosts-per-service query.
4. **Find your ceiling.** Raise the callers until p95 latency is more than twice what it is at
   20. *Success:* a short note of which resource ran out first (pool waits, open contexts,
   search queue, CPU), with the numbers from the run, and the one setting that moved the
   ceiling furthest.

## Recap

- **List what's finite** first, including what a call holds after it returns, and what the
  backend shares with things that aren't yours.
- **Measure** with many callers and a runaway, and watch the backends while you do.
- **Pools** bound what the server takes from each backend and stop paying set-up costs per
  call: three times the throughput here. Reset what each checkout changed.
- **A gateway inherits the per-login limits.** `SET ROLE` doesn't apply a role's settings;
  check every limit the backend applied per user.
- **Per-caller limits** make it fair: rate, result-token quota, concurrency, server capacity,
  and the cursors a caller leaves open. Per-caller limits protect a backend only if they
  can't add up past its own limit.
- **Refusals are instructions:** busy (wait, then retry the same call), too slow (ask
  less), gated (narrow it). As tool results, not HTTP 429s, so the model reads them.
- **Time budgets at every layer,** enforced where the work happens: the database's statement
  timeout, the cluster's own search cancellation.
- **Cost gates** refuse the order-of-magnitude mistakes from an estimate: `EXPLAIN` for SQL,
  document counts for a log window.
- **Per-tool caps** decide from the routing headers alone, in front of MCP.

## Next

**[Unit 9: Performance and Scale](09-performance-and-scale.md)**: one envelope server per backend holds now.
Unit 9 runs several replicas behind a load balancer, which the stateless 2026-07-28 transport
makes possible, and finds out which of this unit's limits survive it: a limiter that lives in
one process's memory doesn't.
