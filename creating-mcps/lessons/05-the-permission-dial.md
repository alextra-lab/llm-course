---
title: "The Permission Dial: Restrictive vs Permissive Tools"
linkTitle: "5. The Permission Dial"
weight: 5
---

**Goal:** give the same persona's access to a model four ways, from "run any SQL" to "only
these questions", and measure what each one costs and risks:
- **permissive**: `run_sql` on the CMDB, and OpenSearch's own MCP server (raw Query DSL and
  any REST API) on the SIEM;
- **guarded**: one SELECT, checked on its parse tree; one PPL query, bounded by
  construction and checked on its compiled plan;
- **restrictive**: task-shaped tools, with the SQL written once by a person;
- **hybrid**: restrictive, plus an escape hatch that runs a guarded query only when the
  person using the host approves it.

You'll fire the same risky probes at all four, see what each tool list costs on every call,
and run one task set through all four with `gpt-oss-120b`, several times each.

**Where this fits:** Unit 4 built the restrictive end for the log store. This unit puts it
next to the alternatives, so the choice of where to sit on the dial is a measured one. The
backends' own rules from Unit 0 (grants, row-level security, field-level security, masking,
role timeouts) apply underneath every variant. Unit 7 adds identity and a policy layer on top;
Unit 8 adds rate limits and query-cost gates; Unit 10 attacks what's left.

---

## The dial

Every database or search MCP server makes the same choice, often without saying so: how much
of its account's power does it hand to the model?

| | CMDB (Postgres) | SIEM (OpenSearch) | the model writes |
|---|---|---|---|
| **permissive** | `run_sql` | `opensearch-mcp-server-py` 0.12.0 | any query, any API call |
| **guarded** | `query_sql` | `ppl_query` | a query, inside checked limits |
| **restrictive** | `get_asset`, `find_assets`, `service_dependencies`, `hosts_with_vulnerability`, `count_assets` | Unit 4's five tools | arguments only |
| **hybrid** | restrictive + `escalate_query` | | arguments; a query only with a person's yes |

All four run **as the same persona**: [`examples/05/variants.py`](../examples/05/variants.py)
builds each one's server table for the Unit 2 host, and every server gets the persona's own
database and cluster credentials. So the backends enforce the same rules underneath all
four. What changes is how much of the persona's power reaches the model, and how much the
model has to know to use it.

## Permissive: the persona's power, all of it

[`examples/05/cmdb_raw_server.py`](../examples/05/cmdb_raw_server.py) is what many database
MCP servers ship. The model writes SQL; the server runs it:

```python
@mcp.tool()
def run_sql(sql: str) -> dict[str, Any]:
    """Run SQL on the CMDB (Postgres; tables are in the `cmdb` schema: ...)"""
    with pg_connect(PERSONA, autocommit=True) as conn:
        cur = conn.execute(sql)
        ...
```

For the SIEM, the permissive variant runs the real thing: OpenSearch's own MCP server, as
published, logged in as the persona. It's a third-party package with its own dependencies
(and it pins an older `opensearch-py`), so it lives in its own virtual environment:

```bash
python3.13 -m venv .venv-osmcp
.venv-osmcp/bin/pip install opensearch-mcp-server-py==0.12.0
```

It offers nine tools, among them `SearchIndexTool(index, query_dsl)` and
`GenericOpenSearchApiTool(path, method, body)`, which calls any REST endpoint.

"It runs as the persona, so the database protects us" is the usual defence of this end of
the dial. It's half true. The persona can't write to the CMDB, can't read `people.email` as
L1, and can't read EDR telemetry as L1. But a database account designed for a person is not
designed for a model that will try anything. Here is what the same L1 analyst can do
through `run_sql`, measured by `blast.py` below:

- read the system catalogs: every role name, every setting (`pg_roles`, `pg_settings`);
- **lift its own statement timeout**: `SET statement_timeout = 0; SELECT pg_sleep(6)` ran
  for 6 seconds against a role limit of 5. A `;` chain is one call;
- create temporary tables (`CREATE TEMP TABLE scratch AS SELECT * FROM cmdb.assets`);
- run a three-way cross join that counts 144 million rows in 4.6 s, just under the limit.

And through OpenSearch's server:

- a `match_all` over `logs-*` with `size: 10000`: 63 shards searched. The server caps the
  response (about 5.7 KB came back), but the cluster did the work;
- a leading-wildcard query and a script query over every authentication log;
- `GET /_nodes/stats/jvm`: node names, IP addresses and JVM details, readable because the
  role has cluster monitor rights for its dashboards.

None of that is a break-in. Every one of those is something the persona is *allowed* to do.
The point is that a role sized for a careful person, used through a tool that passes
anything through, hands all of it to a model.

## Guarded SQL: check the tree, not the text

[`examples/05/sql_guard.py`](../examples/05/sql_guard.py) decides whether a model's SQL may
run. A regular expression can't: `DELETE` hides inside a CTE, `pg_sleep` inside a subquery,
a second statement after a `;` inside a string. So the guard parses the query with
[sqlglot](https://sqlglot.com/) (Postgres dialect) and walks the tree:

```python
def check(sql: str) -> exp.Expression:
    statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    if len(statements) != 1:
        raise SqlRefused(f"send exactly one statement; this has {len(statements)}. No `;` "
                         "chains.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SqlRefused(f"only SELECT queries may run here, not {tree.key.upper()}.")
    for node in tree.walk():
        if isinstance(node, WRITES):
            raise SqlRefused(f"{node.key.upper()} isn't allowed: this tool only reads. "
                             "Send a plain SELECT.")
    ...
```

Four rules, each one a probe it stops:

1. **Exactly one statement**, and it must be a query. That stops the `SET ...; SELECT` chain.
2. **Nothing that writes, locks, sets or copies, anywhere in the tree.** `WITH x AS (DELETE
   ... RETURNING *) SELECT * FROM x` is a SELECT at the top and a DELETE inside.
3. **Functions sqlglot recognises as standard SQL, or a short allowlist.** Anything sqlglot
   parses as an unknown function (`pg_sleep`, `pg_read_file`, `dblink`, `set_config`) is
   refused. "Functions we haven't thought about" is exactly where the surprises are, so the
   list says what's allowed, not what's denied. `generate_series` is standard but can
   produce unbounded rows, so it's denied by name.
4. **Tables in `cmdb` only, schema-qualified.** That rules out `pg_catalog`, and makes the
   query say what it reads.

What passes is not run as written. It's wrapped and run in a read-only transaction with its
own, shorter timeout:

```python
def run(conn, sql: str, max_rows: int = MAX_ROWS) -> dict:
    tree = check(sql)
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = '{TIMEOUT}'")
        cur = conn.execute(capped(tree, max_rows))
        ...
```

`capped` turns the query into `SELECT * FROM (...) AS guarded LIMIT 201`: one more row than
the cap, so the caller can say "truncated" honestly. `READ ONLY` and `SET LOCAL` are the
database's own enforcement of what the guard just checked; if the guard has a hole, they
are the second layer.

The connection is still the persona's. **The guard narrows what the persona can do; it
never widens it.** As L1, `SELECT email FROM cmdb.people` passes the guard and Postgres
refuses it. As L3, Postgres allows it, and so does the guarded tool. Deciding who may read
e-mail addresses is the database's job, and Unit 7's; the guard's job is to stop the
things the role can do that a model shouldn't.

[`cmdb_guarded_server.py`](../examples/05/cmdb_guarded_server.py) wraps this as one tool,
`query_sql`, and records the guard's verdict in the joinable log with `log_note`.

## Guarded PPL: bound it by construction, then read the plan

For the SIEM, the guarded tool takes [PPL](https://docs.opensearch.org/latest/search-plugins/sql/ppl/index/),
OpenSearch's piped query language, instead of Query DSL:

```text
source = logs-auth-* | where `event.outcome` = 'failure' | stats count() by `source.ip`
```

Models write PPL more reliably than nested Query DSL, and a person can read it in an audit
log. (The roles needed one extra cluster permission for it, `cluster:admin/opensearch/ppl`.
If you seeded the estate before this unit, run `python env/env.py seed` again.)

[`examples/05/ppl_guard.py`](../examples/05/ppl_guard.py) works in three steps:

1. **Check the text** for what only the text shows. The source must be one index pattern
   from the persona's allowlist in Unit 3's catalog; L1 gets `logs-auth-*`, `logs-web-*`,
   `logs-syslog-*` and `incidents`. Every command must be on a short list: `where`,
   `fields`, `stats`, `sort`, `head`, `dedup`, `top`, `rare`, `rename`, `eval`,
   `timechart`, `fillnull`. No `join`, `lookup`, `ml`, `regex` or `parse`.
2. **Bound it by construction.** The window comes in as `start` and `end` arguments, at
   most 7 days, parsed by Unit 4's `windows.window`. The guard rewrites `source` to the
   daily indexes the window touches (only the ones that exist), adds the exact time filter
   itself, and appends `head 200`:

   ```text
   source = logs-auth-2026.10.08,logs-auth-2026.10.09
     | where `@timestamp` >= '2026-10-08 22:00:00' and `@timestamp` < '2026-10-09 06:00:00'
     | where `event.outcome` = 'failure' | stats count() by `source.ip`
     | head 200
   ```
3. **Check the compiled plan.** `POST /_plugins/_ppl/_explain` returns the OpenSearch
   request the query compiles to. The guard refuses wildcard, prefix or regexp queries that
   start with a wildcard (a scan of every term in the index), and plans that would fetch
   more than 200 documents to post-process them on the coordinating node.

Why not simply check the plan for a range on `@timestamp`? That was the first version, and
it refused half of the ordinary queries. On OpenSearch 3.9, PPL's Calcite engine compiles an
IP comparison or any function call into its own script, and sometimes absorbs the time range
into the same script. The plan then contains *no* range query, even though the query has a
perfectly good time filter. Naming the indexes bounds what any script can touch, whatever
the plan looks like. **Bound by construction what you can; check the plan for what you
can't.**

The guarded variant's whole SIEM surface is one tool,
[`ppl_query`](../examples/05/siem_guarded_server.py). Its description is a small PPL manual:
an example, the backtick rule for dotted field names, the window arguments, the command list.

## Restrictive: arguments only

[`examples/05/cmdb_tasks_server.py`](../examples/05/cmdb_tasks_server.py) offers questions
instead of a language. Unit 3's `get_asset` and `find_assets` are registered unchanged,
since `@mcp.tool()` returns the original function:

```python
mcp.tool()(unit3.get_asset)
mcp.tool()(unit3.find_assets)
```

Three new tools answer the other questions an analyst asks the CMDB:
`service_dependencies(service)` (both directions), `hosts_with_vulnerability(cve_id,
environment, status)`, and `count_assets(environment, tier, classification, service)`.
Every one is a parameterised query a person wrote once. The SIEM half is Unit 4's server,
as it was.

The model can't write a query at all, so the blast radius is the tools' own SQL, whoever
calls them. What it gives up is reach: a question no tool was shaped for can only be
answered by stitching tools together, or not at all.

## Hybrid: an escape hatch behind a person

[`examples/05/hatch_server.py`](../examples/05/hatch_server.py) adds one tool to the
restrictive variant:

```python
@mcp.tool()
async def escalate_query(language: Literal["sql", "ppl"], query: str, reason: str,
                         ctx: Context, start: str | None = None,
                         end: str | None = None) -> dict[str, Any] | InputRequiredResult:
    """LAST RESORT, when no other tool can answer: ask the user to approve one read-only
    query. ..."""
```

It uses Unit 2's elicitation: the first call returns `input_required` with a form asking the
person, showing the query and the model's reason. The SDK seals the round's
`request_state` and binds it to the arguments, so an approval can't be replayed onto a
different query. If the host has no way to ask a person, the hatch says it's closed.

Two design choices matter more than the mechanism:

- **Approval opens the guarded tier, not raw access.** After a yes, the query still goes
  through `sql_guard` or `ppl_guard`. Approve an `UPDATE` and the guard still refuses it.
  A person approves *the question*; the guard decides what the query may do.
- **A decline is an instruction.** "The user didn't approve this query. Don't retry it;
  answer with the other tools or say what you couldn't find."

Every approval and decline is in the joinable log as `hatch=asked`, `approved` or
`declined`. Unit 11 turns that into an audit trail.

## Blast radius, tried for real

[`examples/05/blast.py`](../examples/05/blast.py) sends thirteen probes through each
variant. No model is involved: each probe is something a confused or manipulated model
might send, written as the closest call each variant can express. The hybrid's hatch is
approved automatically, so its column is the worst case: what a person could approve by
mistake.

```bash
python examples/05/blast.py 2>blast.log
```

As `l1_analyst`, on the default data set:

```text
probe             permissive                      guarded                         restrictive          hybrid
system-catalogs   ran                             refused by the tool             no tool for it       refused by the tool
lift-timeout      ran (6.0 s)                     refused by the tool             no tool for it       refused by the tool
sleep             stopped by the backend (5.0 s)  refused by the tool             no tool for it       refused by the tool
cross-join        ran (4.5 s)                     stopped by the backend (2.0 s)  no tool for it       stopped by the backend (2.0 s)
temp-table        ran                             refused by the tool             no tool for it       refused by the tool
update            stopped by the backend          refused by the tool             no tool for it       refused by the tool
pii               stopped by the backend          stopped by the backend          no tool for it       stopped by the backend
all-logs          ran                             refused by the tool             refused by the tool  refused by the tool
leading-wildcard  ran                             refused by the tool             no tool for it       refused by the tool
script            ran                             no tool for it                  no tool for it       no tool for it
edr               stopped by the backend          refused by the tool             refused by the tool  refused by the tool
cluster-internals ran                             no tool for it                  no tool for it       no tool for it
close-incident    stopped by the backend          no tool for it                  no tool for it       no tool for it
```

How to read it:

- **"ran" is what each variant lets through.** Permissive: eight of thirteen. The others:
  none.
- **"stopped by the backend" is the last line holding.** It held for the permissive variant
  on writes, PII, EDR and the incident update. That's Unit 0's work paying off, but notice
  `sleep`: the backend stopped it *after 5 seconds of a connection*. A stop is not free.
- **"refused by the tool" happens before the backend sees anything,** and comes back as an
  instruction the model can act on.
- **"no tool for it"** is the restrictive answer. It's the strongest answer, and also the
  answer to every legitimate question nobody wrote a tool for.
- The guarded cross join is "stopped by the backend" in 2 s, not refused: it's a valid
  SELECT, so the guard's own timeout ends it. A query-cost gate in front of it (Unit 8) could
  refuse it before it runs.

Run it as `l3_responder` too. PII and EDR flip to "ran" in every variant that can express
them, because L3 is allowed both. The guards never widen a persona, and they never narrow
it on questions of *who may see what*. That stays with the backend.

## What the menu costs

The model reads every tool's name, description and schema on every call.
`grid.py --tools-only` measures each variant's `tools/list` as the host sends it to the model:

```text
  permissive   10 tools   7,266 bytes  ~1,816 tokens
  guarded       2 tools   1,430 bytes  ~357 tokens
  restrictive  10 tools   8,052 bytes  ~2,013 tokens
  hybrid       11 tools   8,970 bytes  ~2,242 tokens
```

The safest variant is the most expensive to describe, and the guarded one is the cheapest.
Many small tools cost more than two general ones, and that cost is paid on every turn of
every conversation. Unit 6 works on the descriptions; Unit 9 on caching the list.

## The grid: same tasks, every variant, repeated

[`examples/05/grid.py`](../examples/05/grid.py) asks ten questions, each in a fresh
conversation, through each variant, `--repeats` times, and scores the `ANSWER:` line against
a gold answer computed live as the same persona (the grader is Unit 3's):

| task | menu | a tool that answers it directly |
|---|---|---|
| the owner of jump-prod-02 | on | `get_asset` |
| services that depend on customer-db | on | `service_dependencies` |
| prod hosts with the most common open CVE | on | `hosts_with_vulnerability` |
| prod hosts running tier-1 services | on | `count_assets` |
| top failing source IP against jump-prod-02 on the incident day | on | `top_values` |
| svc_backup's first successful login that day | on | `search_events` |
| 5xx responses that day | on | `event_histogram` |
| the next host svc_backup logged in to | on | `search_events` |
| the team that owns the most prod hosts | **off** | none |
| the most common OS among prod hosts with an open critical vulnerability | **off** | none |

```bash
python examples/05/grid.py --repeats 3
python examples/05/grid.py --variants restrictive hybrid --repeats 5 --out grid.jsonl
```

For every variant it reports success (mean, and the min-max across repeats), separately on
and off the menu, plus prompt tokens per task, tool calls, errors, approvals and seconds.
Without a model endpoint it prints the tool-list cost and stops.

What to look for when you run it:

- **On the menu**, the restrictive and hybrid variants should do at least as well as the
  others with fewer calls. If they don't, a tool description is the suspect (Unit 6).
- **Off the menu**, the restrictive variant has to either stitch tools together (many calls,
  many tokens) or answer `UNAVAILABLE`. Both are honest outcomes; a confident wrong answer is
  not. The hybrid should reach for the hatch: count the approvals.
- **Guarded** errors are mostly refusals the model then corrects. Look at how many calls
  that takes, and whether the model gives up.
- **Permissive** may score well. That's the point of measuring blast radius separately: a
  high success rate says nothing about what else the same tools allow.
- **Read the ranges, not just the means.** With three runs, two variants whose ranges
  overlap are not shown to differ.

The lesson records no grid scores: they depend on your model and endpoint. Run it and keep
your numbers next to the blast-radius table; the two together are the measurement.

## Choosing a position

There's no best position, but there is a default. For an agent working on production
systems:

- **Start restrictive** for the questions you know people ask; that's most of them, and it's
  where success is highest and blast radius smallest.
- **Add a guarded tier for the long tail**, behind a person (the hybrid) when the caller is a
  model acting for someone, or directly when the caller is an analyst who'd otherwise write
  the SQL themselves.
- **Keep permissive access for people**, or for a sandbox copy of the data. If a model must
  have it, give it a role sized for a model: no catalog reads, no `SET`, a timeout it
  can't lift, no cluster monitor rights.
- **Log every hatch use** and read it. Questions that keep going through the hatch are the
  next task tools to write.

Unit 12's gateway is built this way.

> **Security:** the position on the dial is a security decision before it's a usability
> one. At the permissive end, the blast radius is the account's, so the account must be sized
> for a model, not a person. The guards stop what roles don't (statement chains, settings,
> catalog reads, unknown functions, leading wildcards, unbounded windows), but they are
> allowlists you maintain. A function you add to `ALLOWED_FUNCTIONS` is a decision. The hatch
> puts a person in the loop for the long tail, and its approval is bound to the exact query,
> but people approve things they don't read. That's why approval opens only the guarded tier.
> Results still carry attacker-controlled text in every variant (Unit 10).

> **Observe:** the guarded servers log `guard=refused` or `guard=passed` for every query, plus
> `backend=error` when a query passed the guard and then failed. The hatch logs `asked`,
> `approved` and `declined`. That answers *how often does the model hit the guard, and what
> do people approve?* `blast.py` and `grid.py` give the same view offline: per variant, what
> ran, what was stopped and where, and what each task cost.

> **Limits:**
> - **Context.** The tool list is paid on every call: ~360 tokens for the guarded variant,
>   ~2,000 for the restrictive one. Results are capped at 200 rows (SQL and PPL) and 100 kB
>   (`run_sql`). OpenSearch's server caps its own responses, so the cost of a huge search
>   shows up on the cluster, not in the context.
> - **Backends.** The guarded SQL tool's 2 s timeout is shorter than any role's (5 s for L1,
>   15 s for L3), and `SET LOCAL` keeps it to the one transaction. The PPL guard names only
>   the daily indexes that exist in the window (cached for a minute) and caps it at 7 days.
>   Nothing yet limits how *many* queries a caller sends; that's Unit 8.

> **Porting to Elasticsearch:** the SQL side is unchanged. For the log store, ES|QL plays the
> role of PPL, with the same pipe shape. The approach carries over: check the text, bound the
> source and window by construction, and cap the rows. The plan check needs a different hook,
> because ES|QL has no `_explain` that returns the compiled search. Elastic publishes its own
> MCP server for the permissive end. Point `blast.py`'s probes at whatever version you
> run, as this unit did with OpenSearch's.

## Lab: turn the permission dial

Open the notebook ([`labs/05_permission_dial.py`](../labs/05_permission_dial.py)) with
marimo:

```bash
marimo edit labs/05_permission_dial.py
```

It walks through the unit in six steps. You try the SQL guard and the PPL guard by hand
and see what actually runs. You fire the blast-radius probes as either persona and compare
the tool-list costs. Then you are the person behind the hatch: approve or decline a query
and see the result. Finally, with a model endpoint, you run the grid on variants of your
choice.

## Challenges

1. **Close the timeout hole at the source.** The permissive variant's `lift-timeout` probe
   works because a `;` chain runs as one call and a role's `statement_timeout` is only a
   default. Change `run_sql` (not the database, and without parsing the SQL) so that one call
   runs exactly one statement inside a transaction that is always rolled back, with its own
   `SET LOCAL statement_timeout`. Hint: a prepared statement can't hold two commands.
   *Success:* `blast.py` no longer shows `lift-timeout` as "ran" for the permissive variant,
   `cross-join` is stopped by your timeout, and `system-catalogs` still runs (you didn't
   build a guard).
2. **A task tool from the hatch.** Run the grid's two off-menu tasks through the hybrid,
   read the queries the model sent through `escalate_query`, and write the task tool that
   would have answered one of them. *Success:* the grid, re-run with your tool, answers that
   task without an approval in every repeat, and the tool-list cost grows by less than 600
   bytes.
3. **Guard a function you allowed.** `ALLOWED_FUNCTIONS` includes `string_agg`. Write a query
   that passes the guard and still makes the result enormous before the row cap applies.
   Then fix the guard or the wrapper so it can't. *Success:* your query is refused or capped
   to under 100 kB of output, and `examples/05/sql_guard.py`'s own examples behave as before.

## Recap

- Every database or search MCP server picks a position on a **permission dial**: how much of
  its account's power reaches the model. Measure it; don't assume it.
- **"It runs as the persona" isn't enough at the permissive end.** As L1, `run_sql` read the
  system catalogs, lifted its own statement timeout with a `;` chain, created temp tables
  and ran a 144-million-row cross join. OpenSearch's own server searched 63 shards in one call
  and read node internals. All of that was allowed.
- **Guard SQL on the parse tree:** one statement, a read with no writes anywhere in the tree,
  known functions only, schema-qualified tables on an allowlist. Then run it wrapped,
  `READ ONLY`, with a shorter `SET LOCAL` timeout, as the persona.
- **Guard PPL by construction,** then on the plan: an allowlisted source and commands, the
  window passed as arguments and turned into daily indexes plus an exact filter, a row cap,
  and a compiled-plan check for leading wildcards and oversized fetches.
- **Guards narrow; they never widen.** Who may see what stays with the backend.
- **Restrictive tools** have the smallest blast radius and the largest tool list (~2,000
  tokens on every call), and no answer off the menu.
- **The hybrid's hatch** asks a person, through elicitation, and opens only the guarded
  tier. Its log is the list of task tools to write next.
- Success is measured on the same tasks, on and off the menu, over repeated runs, with the
  range reported.

## Next

**Unit 6: Designing Tools for a Model Reader** *(coming next)*: names, descriptions,
granularity, structured output and errors written as instructions, measured on the
restrictive tools from this unit, including what their 2,000-token tool list can lose
without losing accuracy.
