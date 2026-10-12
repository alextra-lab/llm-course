---
title: "Performance and Scale"
linkTitle: "9. Performance and Scale"
weight: 9
---

**Goal:** run Unit 8's servers as several replicas, and find out what that buys and what it
breaks. You'll:
- measure the two transports against each other, per call, at start-up and in memory;
- find what limits throughput before adding anything, from where the CPU goes;
- run three replicas on one port, and size their pools so they fit the backend's limits;
- move the per-caller limits into shared state, so three replicas enforce one set;
- add a result cache in the one place it's safe, and watch a cache in the wrong place leak;
- measure four OpenSearch choices, including one where the cluster's own cache quietly does
  nothing;
- package the server as an image with health checks and no secrets inside.

**Where this fits:** Unit 8 put one server per backend inside an operating envelope. Its
limits live in that one process, which its Limits note flagged. Unit 7 left a CMDB lookup on
every log question that names a host, with a note that this unit would cache it. Unit 10 then
attacks the servers you've scaled; Unit 11 watches them.

No model is needed for this unit. The numbers below come from the **large** seed
(`python env/env.py reset --size large`, 1M log documents, `OS_HEAP=2g`), with the estate, the
servers and the load generator on one 4-core laptop.

**New in the estate: Valkey**, an open-source key-value store that speaks the Redis protocol.
It holds the servers' shared state: counters and a cache. Nothing in it is a record. It keeps
nothing across a restart and evicts old keys when full. If your `env/.env` predates this
unit, copy the three `VALKEY_` lines from `env/.env.example`, then run `python env/env.py up`.

---

## Two transports, measured

The same server (Unit 7's CMDB gateway, no pools, so only the transport differs), the same
caller, the same `get_asset` call a hundred times (`examples/09/transports.py`):

| | start | p50 per call | p95 | memory | for 20 agents |
|---|---|---|---|---|---|
| stdio | 825 ms | 16.8 ms | 21.6 ms | 82 MB | 20 processes, 1.6 GB |
| HTTP, one client | 66 ms | 18.2 ms | 23.5 ms | 84 MB | 1 process, 84 MB |
| HTTP, a new client per call | | 80.5 ms | 96.2 ms | | |

Per call, the transports are within a couple of milliseconds: the work is the query, not the
pipe. The differences are elsewhere:

- **Start-up.** A stdio server is a process the host starts, so every agent pays for Python,
  the SDK and the database drivers to load: most of a second here. An HTTP server is already
  running.
- **Memory and connections.** Twenty agents over stdio are twenty processes, each with its own
  connections to the backends. Over HTTP they share one process and one pool. Unit 8's pools
  only work if there's one server to hold them.
- **Keep the client.** A host that opens a new HTTP client for every call pays for a new
  connection and a new client set-up each time: four times the latency here. The SDK's
  client is meant to live as long as the host does.

So stdio is right for one person's tools on one machine, which is what it was designed for.
A server the SOC shares is an HTTP server.

## Stateless means any replica

The 2026-07-28 spec made Streamable HTTP stateless: no session, and every request carries what
it needs (the token, the arguments, Unit 4's sealed cursor). Any replica can answer any
request, so scaling out is starting more processes behind something that spreads the
connections. `examples/09/scaled_server.py` uses uvicorn's own workers: N processes sharing one
port, with the kernel handing each new connection to one of them. A load balancer in front of
N containers works the same way.

Stateless requests don't make the *server* stateless, though. Four things held state in Unit
8's single process:

| state | in one process | across replicas |
|---|---|---|
| connection pools | one pool | one pool **per replica**: size them as a share |
| per-caller limits | one set of buckets | one set **per replica**, unless shared |
| a result cache | n/a | per replica (`local`) or shared |
| facts for the policy | looked up every time | cached per persona, per replica, for a minute |

The rest of this unit takes them in turn. First, though: will replicas help at all?

## Find the bottleneck first

The load generator from Unit 8 now also samples where the CPU goes, as a percentage of the
whole machine: OpenSearch (from its own node stats), the MCP servers, and the load generator
itself. It also records the busiest single server process as a percentage of *one* core,
because a Python process runs its Python code on one core at a time. At 100% it's pinned.

Sixty agents calling both servers every 0.05 s on average, with the rate limit raised so it
isn't what we measure:

| | calls/s | p50 | p95 | machine | OpenSearch | busiest server |
|---|---|---|---|---|---|---|
| 1 replica | 182 | 224 ms | 605 ms | 87% | 30% | 69% of a core |
| 3 replicas | 183 | 201 ms | 463 ms | 87% | 28% | 37% of a core |

Three replicas did nothing for throughput. No server process is pinned, and the machine is
nearly out of CPU, much of it spent by OpenSearch answering week-long aggregations. More
replicas only add processes competing for the same four cores. The p95 improves a little,
because a slow call no longer delays the others in the same process, but the system can't do
more work.

Now the same agents calling only the CMDB, whose queries cost Postgres very little:

| CMDB only | calls/s | p50 | p95 | machine | busiest server |
|---|---|---|---|---|---|
| 1 replica | 136 | 281 ms | 824 ms | 55% | **97%** of a core |
| 3 replicas | 280 | 166 ms | 309 ms | 80% | 69% of a core |

Here the one server process was pinned while the machine had CPU to spare, and two more
replicas doubled throughput. That's the rule: **replicas help when the server is the
bottleneck**, and you can see when it is. When the backend is the bottleneck, replicas move the
queue from the server to the backend; the fix is to ask the backend for less (caching,
below), or to give it more.

On a real deployment the servers and the backends don't share a laptop, and the numbers
change. The method doesn't: measure the server, the backend and the load generator together,
and look for the pinned process or the saturated machine before choosing a fix.

## Replicas and their pools

Each replica opens its own pools, so the pool size is no longer a constant. `MCP_PG_BUDGET`
is what all replicas of one server may hold together; each replica gets an equal share
(`pool_share`, at least 2). The default, 20 per server, leaves 10 of the gateway login's 50
for a second deployment, a migration, or a person debugging.

Get the arithmetic wrong and the failure is quiet. With a budget of 60 over three replicas,
the first replica filled its pool of 20, the next one filled part of its pool and then hit
`too many connections for role "mcp_service"`, and uvicorn restarted the failed replicas
again and again. The survivor answered `/readyz` with `ok`, the load test passed, and the
server ran with a fraction of the capacity it was configured for. `scaled_server.py` now
checks before it starts any replica:

```
3 replicas x 20 connections = 60, but mcp_service may open 50 in all (and the other server
shares them). Lower MCP_PG_BUDGET.
```

It can only check what it knows: the other server's replicas use the same login. Sizing all of
them together is a deployment decision, made once, written down, and checked like this
wherever it can be.

## Limits that replicas share

Unit 8's limiter keeps its buckets and slots in memory. With three replicas, each keeps its
own, and the same runaway agent (16 loops, no thinking) against three replicas gets this:

| runaway's successful calls in 10 s | |
|---|---|
| Unit 8, one replica | 128 |
| three replicas, limits in each replica | 371 |
| three replicas, limits shared in Valkey | 119 |

Three times the rate, three times the concurrency, and nobody decided it. The runaway's
calls spread over several connections, and each connection lands on a different replica.

`examples/09/shared_limits.py` keeps the same interface as Unit 8's `Limiter` and puts the
state in Valkey:

- **Token buckets** (rate, quota, cursors): one hash per caller holding the tokens and the
  time of the last refill.
- **Slots** (concurrency, capacity): a sorted set of leases, one per call in flight, each
  with an expiry. A replica that dies mid-call can't hold a slot forever; its lease runs out.

Every check-and-update is one Lua script, which Valkey runs atomically, so two replicas can't
both take the last token:

```lua
local req = refill(KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), now)
if req < 1 then return {'rate', tostring((1 - req) / tonumber(ARGV[2]))} end
```

Two details matter. `now` comes from Valkey's own clock (`TIME`), never the replica's, so
replicas whose clocks disagree still agree on the buckets. And when Valkey doesn't answer, the
limiter **fails open**: it lets the call through and logs `limiter.fail_open`. Refusing every
call because the limiter is down turns a limiter outage into a full outage, and the pools and
timeouts still protect the backends. If a backend can't survive an unlimited burst, choose the
other way, and write down that you did.

The cost is a round trip to Valkey per check, a fraction of a millisecond on a laptop. Unit
8's per-tool caps (`ToolCaps`) are still per replica: divide the cap by the number of replicas,
or move it to the load balancer, which can read the same headers.

## A cache, in the one safe place

The cheapest backend query is the one you don't send. With sixty agents asking a realistic
mix of questions, most of them are asked again within the minute: the same host looked up, the
same week of authentication logs counted.

**Where it sits decides whether it's safe.** `examples/09/cache.py` runs *inside* Unit 7's
policy layer, through a hook in `with_policy`. A call is authenticated, checked against the
grants, evaluated against the rules and logged first. Only then may the cache answer
instead of the backend:

```python
run = functools.partial(fn, *args, **kwargs)
if RESULTS is not None:
    return await RESULTS.get_or_run(name, call, run)
return await asyncio.to_thread(run)
```

**The key starts with the persona**, because the answer depends on it: row-level security,
field-level security and masking all differ by persona. It's persona, not person: two L3
responders get the same rows, so they can share a cached answer, and the policy has already run
for each of them.

```
key = sha256(persona | tool | arguments as canonical JSON | the "now" step, if relative)
```

**Relative windows** ("now-7d") mean something different every second, so a key containing
them is only reusable while "now" stays put. Unit 4's `windows.py` now takes `MCP_ROUND_NOW`:
round "now" down to the minute, and every caller asking for "now-7d" within that minute gets the
same window, the same query, and the same cache key. The cost: the newest seconds of logs wait
for the next minute.

**Not cached:** `get_contact` (personal data behind an approval), `search_events` (each answer
holds a cursor on the cluster), and anything following a cursor. Entries live for 60 seconds.

The same sixty agents, one replica, the shared cache:

| | calls/s | p50 | p95 | OpenSearch CPU | hits / misses |
|---|---|---|---|---|---|
| no cache | 182 | 224 ms | 605 ms | 30% | |
| cache, "now" exact | 187 | 214 ms | 617 ms | 29% | 662 / 428 |
| cache, "now" rounded to the minute | 326 | 98 ms | 259 ms | 6% | 2,229 / 27 |

With an exact "now", only the CMDB answers repeat, and the log questions never hit. Rounded,
99% of calls are answered from the cache, OpenSearch's CPU falls from 30% to 6%, and
throughput nearly doubles, on the same machine where three replicas changed nothing.

**Local or shared?** `MCP_CACHE=local` keeps a copy per replica: each warms its own, and the
hit rate falls as replicas multiply. `shared` keeps one copy in Valkey, at the price of a round
trip on every hit. With this mix's handful of distinct questions, both reached over 95% hits.
With thousands of distinct hosts, or a dozen replicas, shared wins.

### A cache in the wrong place

`cache.py` also has `UnsafeCache`, the obvious first version: MCP middleware in front of
everything, keyed on the tool and its arguments. `examples/09/cache_probes.py` runs four calls
against each:

| call | should be | safe cache | unsafe cache |
|---|---|---|---|
| marc (L3): `get_asset db-prod-01`, a restricted host | allowed | allowed | allowed |
| priya (L1): the same | refused: RLS hides it | refused | **allowed: marc's row** |
| marc: `get_contact jump-prod-02`, gives a ticket | asks, then allowed | asks | asks |
| kim (L3): the same, declines to give a ticket | asks, then refused | asks | **allowed, never asked** |

Priya read a host that row-level security hides from her, and kim got personal data without a
ticket. The server logged no policy verdict for kim's call, so the audit trail doesn't show it
happened. Nothing in the cache code looks wrong in isolation; only its position and its key do.
This is the same lesson as Unit 7's aggregation leak: every path to the data has to go through
the checks, including the fast path.

### Facts for the policy

Unit 7's host rule asks the CMDB "can this caller see this host?" for every log question that
names one. That answer changes rarely and is the same for everyone in a persona, so
`scaled_server.py` caches it per persona for a minute, in each replica (`ttl_cached`). It's
a policy *input* cached briefly, not a verdict: the rule still runs on every call.

## Four OpenSearch choices

`examples/09/os_perf.py` runs the same request 30 times for each variant:

| experiment | variant | p50 | p95 | also |
|---|---|---|---|---|
| request cache | as admin, "now" exact | 5.3 ms | 7.1 ms | 180 of 210 shard requests from the cache |
| | as admin, "now" rounded | 4.7 ms | 6.4 ms | 209 of 210 from the cache |
| | **as l3_responder**, either | 5.7 ms | 8.9 ms | **0** from the cache |
| indexes | the 2 daily indexes the window needs | 4.2 ms | 5.7 ms | 2 shards |
| | `logs-auth-*` | 5.5 ms | 7.6 ms | 21 shards, 19 skipped |
| totals | exact total | 8.3 ms | 10.8 ms | 99,817 (eq) |
| | default | 5.1 ms | 7.8 ms | 10,000 (gte) |
| | none | 5.2 ms | 7.3 ms | |
| source | every field, 100 events | 5.8 ms | 7.8 ms | 75 KB |
| | the five the tool shows | 6.1 ms | 7.2 ms | 26 KB |

- **The request cache doesn't serve the personas.** OpenSearch caches the per-shard results of
  aggregation requests, and as admin it works: even with an exact "now", the six days fully
  inside the window are answered from the cache, and only the edge day misses. For any user
  under document- or field-level security, the security plugin turns the request cache off,
  and every persona in this estate has field-level security. So the gateway's cache isn't
  doubling something the cluster already does. On this estate it's the only cache there is.
- **Name the indexes.** The wildcard costs every shard a check, even the 19 that rule
  themselves out. It's a millisecond on 21 indexes; it grows with retention. Unit 4 already
  names the daily indexes.
- **Don't count what you won't show.** An exact total is 60% slower than the default, which
  stops counting at 10,000. Unit 4's `search_events` asks for the exact total on page one only.
- **Ask for the fields you'll return.** A third of the bytes for the same latency: less to
  parse in the server, and fewer tokens in the model's context.

At this size, single requests are all fast, and the cluster's limit shows up only under
concurrency, as the CPU tables above did. The choices still compound: every millisecond and
every byte is multiplied by every agent.

## Packaging

`examples/09/Dockerfile` builds one image for both servers:

```bash
docker build -f examples/09/Dockerfile -t creating-mcps-gateway .
docker run --rm --network host --env-file env/.env \
    -e MCP_PUBLIC_URL=http://127.0.0.1:8727/mcp \
    -v "$PWD/env/keys/issuer.pub.pem:/app/env/keys/issuer.pub.pem:ro" \
    creating-mcps-gateway --kind cmdb --workers 2
```

- **No secrets in the image.** `.dockerignore` keeps `env/.env` and `env/keys/` out. Secrets
  arrive as environment variables when the container starts, and `hold_only_my_secret()`
  (Unit 7) drops all but the server's own. There are two now: the gateway's database login and
  Valkey's password. The issuer's *public* key is mounted read-only. The private key never
  goes near a server.
- **Only what the server imports.** No marimo, no model client: a smaller image, and fewer
  packages to patch.
- **Not root.** The process can read its code and the mounted key, and write nothing.
- **The audience is the public URL.** Inside a container the server binds to `0.0.0.0`, but
  callers' tokens name the URL they use. `MCP_PUBLIC_URL` sets both the audience and the SDK's
  DNS-rebinding protection: bound to `127.0.0.1` the SDK only accepts local `Host` headers, and
  bound to `0.0.0.0` it checks nothing unless told which host is real.
- **Health and readiness.** `/healthz` answers if the process is up; `/readyz` answers `ok`
  only if this replica's pools are open and Valkey responds. An orchestrator restarts a replica
  that fails the first and stops sending traffic to one that fails the second. Both are
  answered before MCP, without a token: they say nothing about the data.
- **Graceful shutdown.** On SIGTERM, uvicorn stops accepting, lets calls in flight finish for up
  to 10 seconds, then exits. A stateless server can be replaced at any time, as long as
  replacement waits for the calls already running.

> **Security:** shared state is shared attack surface. Valkey holds every caller's buckets and
> cached answers, including rows other personas can't see. It sits on the same private network
> as the backends, behind a password, and nobody but the servers can reach it. The cache key
> must contain everything the answer depends on: the persona and, for a multi-tenant
> deployment, the tenant. A missing dimension in a cache key is an authorization bug that no
> test of the uncached path will find, which is why `cache_probes.py` runs in CI.

> **Observe:** each call's record now says `cache=hit` or `cache=miss`. `replica.start` records
> each replica's pool size, limiter and cache mode, with its pid, so you can tell replicas apart
> in the logs. `limiter.fail_open` means calls went through unlimited: alert on it. The load
> generator's CPU sampling is the cheapest bottleneck finder there is. Unit 11 turns these into
> per-replica and per-caller metrics.

> **Limits:**
> - **One machine.** Every number here shares four cores. Replicas on separate machines, and
>   a cluster on its own nodes, move every table, and you should re-run them there.
> - **Cache freshness.** A cached answer can be up to a minute old. For a CMDB that's usually
>   fine; for "is this host isolated yet?" during an incident it isn't. A tool whose answer
>   must be live shouldn't be in `CACHEABLE`. E1's write tools are never cached.
> - **Cursors.** `search_events` isn't cached, and its point-in-time cursors live on the
>   cluster, so they survive any replica; the sealed cursor carries everything else.

> **Porting to Elasticsearch:** the same choices apply. Elasticsearch's shard request cache
> has the same rule about `now` (round it, or the request can't be cached), and its document-
> and field-level security turns the request cache off for those users as well, so the
> gateway-side cache matters there too. Redis works in place of Valkey, unchanged.

## Lab: one instance vs three

Open the notebook ([`labs/09_one_instance_vs_three.py`](../labs/09_one_instance_vs_three.py))
with marimo:

```bash
marimo edit labs/09_one_instance_vs_three.py
```

It needs the estate with Valkey (`python env/env.py check` says if it's missing). It has five
parts:

1. Both transports, measured: start-up, per call, a new client per call, memory.
2. The load, with replicas, the cache, rounding, shared limits, a runaway and the Postgres
   budget as controls. Charts of throughput, p95, where the CPU goes, and what the backends
   hold. Its suggested runs reproduce this lesson's tables.
3. The cache probes, safe and unsafe, side by side.
4. The four OpenSearch choices.
5. Every run so far.

## Challenges

1. **Cache the past for longer.** A window that ends more than a day ago covers closed daily
   indexes that no longer change. Give such answers a TTL of an hour instead of a minute.
   *Success:* `event_histogram` for `now-7d` to `now-2d` is a hit an hour later, `now-7d` to
   `now` is not, and `cache_probes.py` still passes.
2. **Share the tool caps.** Move Unit 8's `ToolCaps` counts into Valkey with leases, as
   `shared_limits.py` does for slots. *Success:* with three replicas and
   `MCP_TOOL_CAPS=event_histogram=3`, at most three `event_histogram` calls run at once
   across all replicas, checked by counting `toolcaps.refused` lines.
3. **Fail closed, on purpose.** Add `MCP_LIMITS_FAIL=closed`, which refuses with a busy
   message when Valkey is down. *Success:* with Valkey stopped (`docker compose ... stop
   valkey`), `open` lets calls through and logs `limiter.fail_open`, and `closed` refuses
   them with a message saying when to retry.
4. **A real load balancer.** Run two containers of the image per server behind a load
   balancer of your choice, with health checks on `/readyz`. *Success:* stopping one container
   during a load run causes no errors beyond the calls it had in flight.

## Recap

- **Per call, stdio and HTTP are close.** Start-up, memory and pooling aren't: a shared server
  is an HTTP server, and a host keeps its client.
- **Stateless requests, stateful server.** Pools, limits, caches and cached facts all need a
  decision once there are replicas.
- **Find the bottleneck first.** A pinned server process means replicas will help: twice the
  throughput here. A saturated machine or backend means they won't: zero here.
- **Pools are per replica.** Size them as shares of one budget, and fail at start when they
  don't fit, or the extra replicas crash-loop while the survivors look healthy.
- **Shared limits** in Valkey, with atomic scripts, the store's clock, leases that expire, and
  a deliberate fail-open: the runaway gets 119 calls on three replicas, not 371.
- **A cache inside the policy, keyed by persona,** with "now" rounded: 99% hits, OpenSearch's
  load cut by four fifths, nearly twice the throughput. The same cache in front of the policy
  leaks rows and skips approvals.
- **OpenSearch:** its request cache is off for users under field-level security, so the
  gateway's cache is the cache. Name indexes, don't count what you won't show, and fetch only
  the fields you return.
- **Package** without secrets, as non-root, with the public URL as audience, `/healthz`,
  `/readyz`, and a graceful shutdown.

## Next

**[Unit 10: Attacking and Defending MCP](10-attacking-and-defending-mcp.md)**: the gateway is now shared, limited,
cached and scaled. Unit 10 attacks it, following the OWASP MCP Top 10: injection through tool
results (the hostile log lines and CMDB descriptions already planted in the estate), tool
poisoning, confused deputies and more, with each defence added and measured.
