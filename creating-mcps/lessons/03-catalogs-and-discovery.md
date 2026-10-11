---
title: "Catalogs and Discovery"
linkTitle: "3. Catalogs and Discovery"
weight: 3
---

**Goal:** give a model what it needs to ask good questions of the CMDB and the SIEM, and
measure the difference. You'll build the two common answers side by side:
- **raw discovery**: what Postgres and OpenSearch say about themselves;
- **a curated catalog**: what the data *means*, how to query it, and what this caller may see.

You'll check the catalog against what the backends actually enforce, measure what each approach
costs in the model's context, and run an eval that measures query accuracy over repeated runs.
The CMDB server also moves off the fixture and onto Postgres.

**Where this fits:** this is the first unit on the live estate (`python env/env.py up` and
`seed`, from Unit 0). It applies equally to the database and to the log store, and it is the
groundwork for Unit 4 (searching logs) and Unit 5 (the permission dial): before you decide
*how* a model may query, it has to know *what* there is to query. The eval runs on the Unit 2
host.

---

## Two backends, two discovery problems

Ask each backend to describe itself, as the L1 analyst, and the problems turn out to be
different.

**Postgres is small but says little.** `information_schema` describes the seven CMDB tables in
about 2.6 KB. The names are decent: `assets`, `services`, `tier`, `owner_id`. Now try to answer
"how many production hosts run business-critical services?" from it. Nothing says that
`tier = 1` means business critical. Nothing says which way `service_dependencies` points, and
"what depends on customer-db?" turns on exactly that. Nothing says that `owner_id` on a host is
a *person*, while the team that owns its service lives somewhere else. The schema has the
shape of the data but not its meaning.

**OpenSearch is huge and mostly empty.** The log indexes are daily, so `logs-*` is 63 indexes,
and each maps **296 fields**. The profiler you'll run below finds that `logs-auth-*` fills 15
of them, `logs-web-*` 20, and `logs-syslog-*` 12. The rest are the long tail of a
real-world log schema: `tls.client.ja3`, `orchestrator.cluster.name`, and so on. Asking for the
mapping of `logs-*` returns **1.1 MB**, roughly 278,000 tokens: more than `gpt-oss-120b`'s whole
context window, for an answer that's 95% noise.

Then there's permission, which surprises people:

- **Nobody can list "all indexes".** `GET _cat/indices` and `_resolve/index/*` return 403 for
  every persona, because each persona can read some patterns and not others. A discovery tool
  has to be told which patterns to try.
- **Field-level security is silent.** As the L1 analyst, `user.email` isn't in the mapping, and
  a query on it returns zero hits, not an error. A model that guesses the field gets a wrong
  answer that looks right.
- **Field masking is stranger.** The L3 responder sees `user.email` as a hash. A `terms`
  aggregation on it works, which lets you correlate requests by user. But a `term` or `exists`
  filter on it matches nothing, again silently.

The raw schema can tell a model none of this. A catalog can.

## Raw discovery as a server

Start with what most database and search MCP servers ship, so you have something to measure
against. Create **`work/raw_server.py`**:

```python
import json
import os
from typing import Any, TypedDict

from common_mcp import joinable_log, load_env, os_client, pg_connect
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

load_env()
PERSONA = os.environ.get("MCP_PERSONA", "l1_analyst")
# OpenSearch won't list "every index" for anyone who can't read every index (403), so a raw
# server still has to be told which patterns to look at.
INDEX_PATTERNS = ["logs-auth-*", "logs-web-*", "logs-syslog-*", "edr-events-*", "incidents"]
MAX_RESULT_BYTES = 100_000   # only so one call can't overflow the model; see Limits

mcp = MCPServer(
    "raw",
    version="0.1.0",
    instructions="Raw schema discovery for the CMDB (Postgres) and the SIEM (OpenSearch).",
    cache_hints={"tools/list": CacheHint(ttl_ms=300_000, scope="public")},
)
mcp.middleware.append(joinable_log("raw"))


class Table(TypedDict):
    schema: str
    table: str


class Column(TypedDict):
    column: str
    data_type: str
    nullable: bool


class Index(TypedDict):
    index: str
    docs: int


@mcp.tool()
def list_tables() -> list[Table]:
    """List the database tables you can read."""
    with pg_connect(PERSONA) as conn:
        rows = conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY 1, 2").fetchall()
    return [{"schema": s, "table": t} for s, t in rows]


@mcp.tool()
def describe_table(table: str) -> list[Column]:
    """The columns of one table, as schema.table (e.g. cmdb.assets)."""
    schema, _, name = table.rpartition(".")
    with pg_connect(PERSONA) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable = 'YES' FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (schema or "public", name)).fetchall()
    if not rows:
        raise ToolError(f"no table {table!r} that you can read; list_tables shows them.")
    return [{"column": c, "data_type": t, "nullable": n} for c, t, n in rows]


@mcp.tool()
def list_indexes() -> list[Index]:
    """List the search indexes you can read, with their document counts."""
    client = os_client(PERSONA)
    out = []
    for pattern in INDEX_PATTERNS:
        try:
            rows = client.cat.indices(index=pattern, format="json", h="index,docs.count")
        except Exception:  # 403: this persona can't read that pattern
            continue
        out += [{"index": r["index"], "docs": int(r["docs.count"])} for r in rows]
    return sorted(out, key=lambda r: r["index"])


@mcp.tool()
def describe_index(index: str) -> dict[str, Any]:
    """The field mapping of an index or index pattern, as OpenSearch returns it."""
    try:
        mapping = os_client(PERSONA).indices.get_mapping(index=index)
    except Exception as e:
        raise ToolError(f"can't read the mapping of {index!r}: {type(e).__name__}. "
                        "list_indexes shows the indexes you can read.") from None
    size = len(json.dumps(mapping))
    if size > MAX_RESULT_BYTES:
        raise ToolError(f"the mapping of {index!r} is {size:,} bytes, too big to return; "
                        "describe a single daily index (see list_indexes) instead.")
    return mapping


if __name__ == "__main__":
    mcp.run()
```

Refresh `common_mcp.py` in `work/` first (`cp examples/common_mcp.py work/`): this unit's
version quiets the OpenSearch client, which otherwise logs every HTTP request to stderr, the
same channel as your joinable log.

A few choices in there you'll meet again:

- **One persona per server process**, from `MCP_PERSONA`. Every query runs *as* that persona,
  so the database and the cluster decide what comes back. It's a stand-in: Unit 7 replaces it
  with the real caller's identity. A host passes it to the server with
  `StdioServerParameters(env=...)`. The reference host now takes an `env` argument for this.
  By default a stdio server inherits only a handful of safe variables (`PATH`, `HOME`, ...),
  never your shell's secrets, which is why each server loads `env/.env` itself.
- **A size guard that refuses instead of truncating.** Cutting a mapping in half leaves broken
  JSON and a model that thinks it saw everything. Better to say how big it was and what to ask
  for instead.
- **The return type decides the bytes.** The SDK serialises a tool's result according to its
  annotation, and it matters at this size:
  - `-> str` holding JSON gets escaped a second time inside `structuredContent`;
  - a bare `-> dict` comes back pretty-printed, with no structured content at all;
  - `-> dict[str, Any]` or a `TypedDict` comes back compact.

  On the four mappings below, getting this wrong costs about 8 KB.

*(Reference: [`examples/03/raw_server.py`](../examples/03/raw_server.py).)*

## What's actually there

Before writing a catalog, find out what's worth cataloguing. The reference profiler asks
OpenSearch, as a persona, how many documents hold each mapped field, and the top values of the
keyword fields:

```bash
python examples/03/profile.py logs-auth-*
```

```text
logs-auth-* as l1_analyst: 21 indices, 7487 docs, 15 of 296 mapped fields populated; raw _mapping is 370,314 bytes
  @timestamp                       date      100.0%  []
  event.outcome                    keyword   100.0%  ['success', 'failure']
  event.reason                     keyword    24.9%  ['invalid_password']
  host.name                        keyword   100.0%  ['jump-prod-02', 'edr-prod-09', 'idp-dev-01']
  source.ip                        ip        100.0%  ['203.0.113.77', '10.30.6.87', '10.30.8.242']
  ...
```

It's one `msearch`: an `exists` count per field, plus a `terms` aggregation on the keyword
fields. That's a first draft of a catalog, with the field names, which ones are filled, and
real example values. It can't say what `tier = 1` means, or that a masked field won't filter.
For that, a person who knows the data finishes it. Run it as `l3_responder` on `logs-web-*` and
compare with the L1 run: `user.email` is in L3's mapping but matches nothing.

## A curated catalog

The curated catalog is plain data in **`work/catalog.py`**. Copy the reference
(`cp examples/03/catalog.py work/`) and read it alongside this section. It describes all 12
datasets, and you'll extend it in the challenges. Each field is built with one helper:

```python
PUBLIC, INTERNAL, PII, SECRET = "public", "internal", "pii", "secret"
ANALYSTS = ("l1_analyst", "l3_responder", "auditor")
SOC = ("l1_analyst", "l3_responder")


def F(type_, use, desc, examples=(), sensitivity=INTERNAL, allowed=None, note=None):
    """One catalogued field. `use` says how to ask with it:
    id (exact lookup), filter (exact match / aggregate), search (full text), time, metric
    (numbers to sum, range or average), join (a key into another dataset), display."""
    field = {"type": type_, "use": use, "desc": desc, "sensitivity": sensitivity}
    if examples:
        field["examples"] = list(examples)
    if allowed is not None:
        field["allowed"] = list(allowed)
    if note:
        field["note"] = note
    return field
```

A Postgres table, written for a model reader:

```python
CMDB = {
    "cmdb.services": {
        "backend": "postgres",
        "description": "Business and technical services. Hosts run services; services depend "
                       "on other services.",
        "grain": "one row per service",
        "allowed": ANALYSTS,
        "fields": {
            "id": F("integer", "join", "Surrogate key; assets.service_id points here."),
            "name": F("text", "id", "Unique service name, kebab-case.",
                      ["remote-access", "customer-db", "payments-api"]),
            "tier": F("smallint", "filter",
                      "Business criticality: 1 = business critical, 2 = important, "
                      "3 = can wait. 'Critical services' means tier = 1.", [1, 2, 3]),
            "owner_team_id": F("integer", "join", "The TEAM that owns the service: cmdb.teams.id."),
            "description": F("text", "display", "What the service is for."),
        },
    },
}
```

And a log index pattern, which adds the time field and retention:

```python
SIEM = {
    "logs-auth-*": {
        "backend": "opensearch",
        "description": "SSH logins on Linux hosts, successful and failed.",
        "grain": "one document per login attempt",
        "time_field": "@timestamp",
        "retention": LOG_RETENTION,
        "allowed": SOC,
        "fields": _log(**{
            "event.outcome": F("keyword", "filter", "Whether the login worked.",
                               ["success", "failure"]),
            "user.name": F("keyword", "filter", "The account that tried to log in.",
                           ["svc_backup", "alice.m", "root"]),
            "source.ip": F("ip", "filter", "Where the attempt came from. CIDR works in `term`.",
                           ["203.0.113.77", "10.30.4.17"]),
        }),
    },
}
```

What each entry carries, and why:

- **Meaning, written for the reader.** "Critical means tier = 1", "service_id DEPENDS ON
  depends_on_id", "duration is in nanoseconds". These are exactly the facts a model gets
  wrong from a raw schema.
- **How to ask.** `use` separates fields you *filter* on (`keyword`: exact match,
  aggregations) from fields you *search* (`text`: `match`, with a `.keyword` twin for exact
  values). The time field and the retention say how to bound a search, which is Unit 4's
  first rule.
- **Example values,** taken from the profiler. "One of `success`, `failure`" saves a round
  trip, and a hallucinated `failed`.
- **Only the fields that matter.** `logs-auth-*` catalogues 14 of its 296 mapped fields. The
  other 282 are never mentioned, so the model never wastes a query on them. `trace.id` is
  populated but left out on purpose, because nobody asks for it.
- **A sensitivity tag** on every field: `public`, `internal`, `pii` or `secret`.
- **Who may see it.** `allowed` names the personas, per dataset and, where it differs, per
  field.

Then four small functions turn the data into what a persona sees. `describe()` is the one
that matters:

```python
def datasets_for(persona: str) -> list[str]:
    return [name for name, d in DATASETS.items() if persona in d["allowed"]]


def describe(name: str, persona: str) -> dict | None:
    """One dataset in full, as `persona` may use it. None if they may not see it at all."""
    d = DATASETS.get(name)
    if d is None or persona not in d["allowed"]:
        return None
    fields, withheld = _visible_fields(d, persona)
    out = {"name": name, "backend": d["backend"], "description": d["description"],
           "grain": d["grain"]}
    for key in ("time_field", "retention", "row_rules"):
        if key in d:
            out[key] = d[key]
    out["fields"] = fields
    withheld += len(WITHHELD.get(name, {}))
    if withheld:
        out["withheld"] = (f"{withheld} field(s) exist but are not available to your role; "
                           "don't try to query them.")
    return out
```

Two rules are worth stating plainly:

- **Withheld fields are counted, never named.** The L1 analyst's `cmdb.people` says "2 field(s)
  exist but are not available to your role". That's enough for the model to answer "I can't
  see that" instead of guessing a column, and it reveals no names.
- **`secret` fields are never catalogued for anyone.** `labels.api_token` (fake tokens planted
  in about one web log in 200) appears only in `WITHHELD`. It's counted but never described,
  even to the L3 responder.

## The catalog must agree with the backend

The catalog is **not** the security boundary. Postgres row-level security and column grants,
and OpenSearch's index and field permissions, are. Hiding a field in the catalog while the
backend still serves it would protect nothing, because a model can guess. The catalog's job is
to *agree* with the backend, so it never promises a persona something the backend will refuse.
`catalog.check(persona)` proves that against the live estate:

- every Postgres column the catalog offers must appear in that persona's
  `information_schema.columns`, which only lists columns they hold a privilege on;
- every OpenSearch field it offers must appear in that persona's mapping;
- every `secret` field must be *absent* from it.

```bash
python examples/03/catalog.py --check
```

```text
catalog 125c23bacc83: agrees with the estate for l1_analyst, l3_responder, auditor
```

Plant a mistake (offer `edr-events-*` and `people.email` to the L1 analyst, or misspell a
field) and it fails:

```text
FAIL l1_analyst: catalog offers cmdb.people columns ['email'], which the backend doesn't have or won't show this persona
FAIL l1_analyst: catalog offers logs-auth-* fields ['user.nmae'], which the backend doesn't have or won't show this persona
FAIL l1_analyst: catalog offers edr-events-*, OpenSearch refuses: AuthorizationException
```

Run it in CI, next to the schema migrations. It's the catalog's equivalent of a contract test,
and it catches the drift a hand-written catalog is prone to.

## Serve it

Create **`work/catalog_server.py`**. It offers the same catalog three ways, because where the
catalog lives is a design decision you'll measure:

```python
import json
import os
from typing import Any, TypedDict

import catalog
from common_mcp import joinable_log
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

PERSONA = os.environ.get("MCP_PERSONA", "l1_analyst")
PER_CALLER = CacheHint(ttl_ms=300_000, scope="private")

mcp = MCPServer(
    "catalog",
    version=catalog.VERSION,
    instructions=catalog.instructions_line(PERSONA),
    cache_hints={m: PER_CALLER for m in ("tools/list", "resources/list",
                                         "resources/templates/list")},
)
mcp.middleware.append(joinable_log("catalog"))


class DatasetSummary(TypedDict):
    name: str
    backend: str
    description: str


@mcp.tool()
def list_datasets() -> list[DatasetSummary]:
    """The tables and log indexes you may query, one line each. Start here."""
    return catalog.summary(PERSONA)


@mcp.tool()
def describe_dataset(name: str) -> dict[str, Any]:
    """Everything you need to query one dataset (e.g. cmdb.assets, logs-auth-*): what a row
    is, the time field, and per field its type, how to use it, and example values."""
    found = catalog.describe(name, PERSONA)
    if found is None:   # same answer for "doesn't exist" and "not yours": don't leak which
        raise ToolError(f"no dataset named {name!r} that you can use; list_datasets "
                        "shows yours.")
    return found


@mcp.resource("catalog://datasets", mime_type="application/json")
def all_datasets() -> str:
    """The whole catalog for your role: every dataset you may use, every field."""
    return json.dumps([catalog.describe(n, PERSONA) for n in catalog.datasets_for(PERSONA)])


@mcp.resource("catalog://datasets/{name}", mime_type="application/json")
def one_dataset(name: str) -> str:
    """One dataset of the catalog."""
    found = catalog.describe(name, PERSONA)
    if found is None:
        raise ValueError(f"no dataset named {name!r} that you can use")
    return json.dumps(found)


if __name__ == "__main__":
    mcp.run()
```

- **`cacheScope` is `private` now.** In Unit 1 every caller saw the same tool list, so `public`
  was right. Here the catalog depends on who's asking. A shared cache (a gateway, Unit 9) that
  handed the L3 responder's catalog to an L1 analyst would leak exactly what the catalog
  withholds.
- **"Doesn't exist" and "not yours" give the same answer.** Compare the raw server, whose
  `describe_index("edr-events-*")` tells an L1 analyst that the index exists and they're
  forbidden from it (Challenge 3).
- **The catalog version is the server version.** `VERSION` is a hash of the catalog's content,
  so any edit changes it, and the joinable log records which catalog a session ran against.

*(Reference: [`examples/03/catalog_server.py`](../examples/03/catalog_server.py) and
[`examples/03/catalog.py`](../examples/03/catalog.py).)*

## Where should the catalog live?

The same content, three placements, measured as the L1 analyst:

| Placement | When it's paid | Size |
|---|---|---|
| A summary in the server's **instructions** | once per conversation, always | 360 B |
| The whole catalog as a **resource** the host loads up front | once per conversation, always | 16.3 KB |
| **Tools** the model calls when it needs them | per question, only what's used | 1.7 KB to list, 0.6–3 KB per dataset |

There's no universal winner. Instructions are cheap but say almost nothing. The full resource
is predictable and needs no extra round trips, but every conversation pays for all twelve
datasets, including the ones it never touches. Tools cost a round trip and rely on the model
calling `describe_dataset` before it queries (which is why the instructions tell it to), but a
question about logins reads only the logins dataset. The reference serves all three, so you
can measure them against each other (Challenge 2).

## Keeping it current

A curated catalog has one weakness that raw discovery doesn't: it can go stale. Three
mechanisms keep it honest:

- **`ttlMs`** on the lists: a host that cached the catalog's tool and resource lists re-fetches
  them after five minutes, so a redeployed catalog server reaches hosts that are already
  running.
- **The version hash** in the server version and the instructions: a host, a log line or an
  eval result can say exactly which catalog it used.
- **`catalog.py --check` in CI** catches the dangerous drift: a column renamed, a field
  removed, a permission tightened. The safe drift is a new field added to an index. It stays
  invisible to the model until someone catalogues it. Challenge 1 makes the profiler report it.

## The CMDB server moves onto Postgres

Unit 1's server read an in-memory fixture. Now **`work/cmdb_server.py`** runs the same two tools
against Postgres, as the persona:

```python
import os
from typing import Literal, TypedDict

from common_mcp import joinable_log, load_env, pg_connect
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

load_env()
PERSONA = os.environ.get("MCP_PERSONA", "l1_analyst")

mcp = MCPServer(
    "cmdb",
    version="0.2.0",
    instructions=(
        "Read-only access to the IT referential (CMDB): hosts, the services they run, and "
        "who owns them. Look hosts up by exact hostname, e.g. jump-prod-02."
    ),
    cache_hints={"tools/list": CacheHint(ttl_ms=300_000, scope="public")},
)
mcp.middleware.append(joinable_log("cmdb"))


class Asset(TypedDict):
    hostname: str
    ip: str
    os: str
    environment: str
    classification: str
    service: str
    owner: str
    owner_team: str
    location: str
    description: str


ASSET_SQL = """
    SELECT a.hostname, host(a.ip), a.os, a.environment, a.classification, s.name,
           p.full_name, t.name, a.location, a.description
      FROM cmdb.assets a
      JOIN cmdb.services s ON s.id = a.service_id
      JOIN cmdb.people p ON p.id = a.owner_id
      JOIN cmdb.teams t ON t.id = p.team_id
"""


def _asset(row) -> Asset:
    keys = ("hostname", "ip", "os", "environment", "classification", "service", "owner",
            "owner_team", "location", "description")
    return dict(zip(keys, row))


@mcp.tool()
def get_asset(hostname: str) -> Asset:
    """Look up one host in the CMDB by its exact hostname (e.g. jump-prod-02): what it runs,
    where, how sensitive it is, and who owns it."""
    with pg_connect(PERSONA) as conn:
        row = conn.execute(ASSET_SQL + " WHERE a.hostname = %s",
                           (hostname.strip().lower(),)).fetchone()
    if row is None:
        raise ToolError(f"no asset named {hostname!r}. Hostnames look like jump-prod-02; "
                        "use find_assets to list hosts by service or environment.")
    return _asset(row)


class AssetPage(TypedDict):
    total: int
    returned: int
    assets: list[Asset]


@mcp.tool()
def find_assets(service: str | None = None,
                environment: Literal["prod", "staging", "dev"] | None = None,
                limit: int = 20) -> AssetPage:
    """List hosts, optionally filtered by service name (e.g. customer-db) and environment.
    Returns at most `limit` hosts (1-50) and the total number that matched."""
    if not 1 <= limit <= 50:
        raise ToolError("limit must be between 1 and 50.")
    where, args = ["TRUE"], []
    if service is not None:
        where.append("s.name = %s")
        args.append(service)
    if environment is not None:
        where.append("a.environment = %s")
        args.append(environment)
    clause = " WHERE " + " AND ".join(where)
    with pg_connect(PERSONA) as conn:
        if service is not None and conn.execute(
                "SELECT 1 FROM cmdb.services WHERE name = %s", (service,)).fetchone() is None:
            names = [r[0] for r in conn.execute("SELECT name FROM cmdb.services ORDER BY 1")]
            raise ToolError(f"no service {service!r}. Services: {', '.join(names)}.")
        total = conn.execute("SELECT count(*) FROM cmdb.assets a JOIN cmdb.services s "
                             "ON s.id = a.service_id" + clause, args).fetchone()[0]
        rows = conn.execute(ASSET_SQL + clause + " ORDER BY a.hostname LIMIT %s",
                            [*args, limit]).fetchall()
    page = [_asset(r) for r in rows]
    return {"total": total, "returned": len(page), "assets": page}


if __name__ == "__main__":
    mcp.run()
```

Same tools, same output schema, so the Unit 2 host doesn't notice the change. What changed is
who decides what comes back. Ask for `db-prod-01` as the L1 analyst and you get "no asset named
'db-prod-01'". Row-level security removed the row before your code saw it, so the server can't
leak it even if it wanted to. As `l3_responder` the same call returns the host. Every value
goes in as a query parameter (`%s`), never into the SQL string. The resources and the prompt
from Unit 1 are left out to keep the file short; adding them back is mechanical.

*(Reference: [`examples/03/cmdb_server.py`](../examples/03/cmdb_server.py).)*

## Measure it

**Cost.** [`examples/03/measure.py`](../examples/03/measure.py) runs both discovery servers
through the Unit 2 host and discovers *everything* once each way. It counts the bytes each
approach puts into the model's context:

```bash
python examples/03/measure.py
```

```text
as l1_analyst:
  raw                 tool definitions (every model call)            0 calls      1,083 bytes  ~     271 tokens
  curated             tool definitions (every model call)            0 calls        675 bytes  ~     169 tokens
  raw                 describe_table x7                              7 calls      2,275 bytes  ~     569 tokens
  raw                 describe_index x4 (newest daily index each)    4 calls     53,733 bytes  ~  13,433 tokens
  curated             describe_dataset x11                          11 calls     16,315 bytes  ~   4,079 tokens
  raw (uncapped)      _mapping for logs-*                            0 calls  1,110,963 bytes  ~ 277,741 tokens
  TOTAL raw           discover everything:  13 calls     60,485 bytes  ~  15,121 tokens
  TOTAL curated       discover everything:  12 calls     19,090 bytes  ~   4,772 tokens
```

The totals hide the more interesting result: the two backends go in opposite directions.

- **For OpenSearch the catalog is about six times smaller.** It's 9.5 KB against roughly
  57 KB, because it leaves out the 270-odd empty fields of each index.
- **For Postgres the catalog is *bigger*.** It's 6.8 KB against 2.6 KB, because it adds the
  meaning, the join directions and the example values the raw schema lacks.

So the catalog isn't simply "smaller". It spends bytes where they buy understanding and saves
them where they buy nothing. (Tokens here are estimated at 4 bytes per token; the eval below
reports the model's real counts.)

**Accuracy.** Bytes are only half the question. The other half is whether the model asks the
right thing. [`examples/03/eval_discovery.py`](../examples/03/eval_discovery.py) asks
`gpt-oss-120b` nine questions about the estate, in three conditions:

- **none:** only a `query` tool. The model can still read `information_schema` through it.
- **raw:** `query` plus the raw-discovery server.
- **curated:** `query` plus the catalog server.

```bash
python examples/03/eval_discovery.py --repeats 3
```

The questions are the ones raw schemas get wrong: business-critical (`tier = 1`), dependency
direction, a host's service's team, an e-mail the L1 analyst may not see, failed logins on the
day of the planted incident, server errors (a status-code *range*), "active" incidents (two
statuses). Three design choices make the result worth trusting:

- **Gold answers are computed live, as the same persona.** The L1 analyst counts 73
  business-critical production hosts; the L3 responder counts 84, because row-level security
  hides 11 restricted ones. The right answer depends on who asks, and the eval knows it.
- **`query` belongs to the harness, not to a server you'd ship.** It runs read-only SQL
  (`SET TRANSACTION READ ONLY`, the persona's own `statement_timeout`) or a size-capped search.
  Whether a model should ever get raw SQL is Unit 5's question. Here it's the measuring
  instrument.
- **Every condition runs several times, and the report shows the spread.** A model's tool use
  varies from run to run, so the eval reports each condition's mean accuracy, its range
  across repeats, the real prompt tokens per question (from the API's `usage`), and the tool
  calls and query errors. If two conditions' ranges overlap, you haven't shown a difference.
  Run more repeats before you claim one.

Run it on your endpoint and keep the JSONL (`--out results.jsonl`): Unit 6 compares tool
descriptions with the same harness, and Unit 11 turns it into a regression test.

> **Security:** the catalog decides what a model is *told*, never what it can *get*. Postgres
> and OpenSearch enforce access; the catalog has to agree with them, and `catalog.py --check`
> proves it does. Within that rule the catalog adds real protection:
> - fields tagged `pii` are only described to roles allowed to read them;
> - `secret` fields are never described to anyone;
> - withheld fields are counted, not named;
> - "doesn't exist" and "not yours" give the same error;
> - per-persona lists are cached `private`.
>
> Raw discovery leaks in ways that are easy to miss. Its error messages confirm which indexes
> exist. On a less carefully permissioned cluster, `describe_index` would happily return the
> names of PII and secret fields. And both servers act as one fixed persona, which is fine on a
> laptop and wrong anywhere else: Unit 7 makes it the real caller.

> **Observe:** all three servers write the joinable log, with `server` naming which one served
> each request, and the catalog server's `version` records which catalog a session used. The
> eval's JSONL records, per question and condition: correctness, the answer, tool calls,
> query errors, real prompt and completion tokens, and time. It answers *did the catalog help,
> by how much, at what cost, and how consistently?* `query_errors` is the early-warning metric
> to watch. A model that keeps sending bad SQL or DSL is telling you which dataset's catalog
> entry is unclear.

> **Limits:** discovery is paid in context, and the numbers above are per conversation:
> ~15k tokens to discover everything raw, ~5k curated. The naive raw call (the mapping of
> `logs-*`, 1.1 MB) is bigger than the model's context window, which is why the raw server
> refuses anything over 100 kB. The catalog server's lists are cached for five minutes, so a
> host pays for `tools/list` once, not per turn. On the backend side, discovery is cheap: a
> mapping or `information_schema` read is milliseconds. The profiler's `msearch` of 296 counts
> over 21 indexes takes one to two seconds, so run it when you write the catalog, not per
> request.

## Lab: catalogs, raw vs curated

Open [`labs/03_catalogs.py`](../labs/03_catalogs.py) with the estate running:

```bash
cp labs/03_catalogs.py work/
marimo edit work/03_catalogs.py
```

Pick a persona, then:

1. Profile any log pattern it can read (mapped vs populated fields, top values).
2. Browse the catalog as that persona, next to a table of how many fields each persona sees.
3. Measure discovery cost.
4. Check the catalog against the estate, with a box that plants a mistake so you can watch
   the check catch it.
5. With a model endpoint, run the accuracy eval with a repeats slider. The results table
   shows each condition's mean and its min-max spread.

## Challenges

1. **Uncatalogued fields.** Add a `--drift` option to `catalog.py` that profiles each
   OpenSearch dataset (reuse `profile.profile`) and lists fields that are populated but not
   catalogued and not withheld. *Success:* against the seeded estate it reports `trace.id`
   for each log pattern, and nothing else. When you delete `event.reason` from
   `logs-auth-*`, it reports `event.reason` too.
2. **Where should it live?** Add a fourth eval condition, `upfront`, with no catalog tools.
   Instead, the whole `catalog://datasets` resource for the persona goes into the system
   prompt. Run `raw`, `curated` and `upfront` with at least 3 repeats. *Success:* a short table
   of accuracy (mean and range) and prompt tokens per question for each condition, and one
   sentence on which you'd ship and why, that doesn't claim a difference where the ranges
   overlap.
3. **Don't confirm what exists.** As the L1 analyst, `raw__describe_index("edr-events-*")`
   fails with an authorization error, while `raw__describe_index("nope-*")` returns an empty
   mapping. The difference confirms that `edr-events-*` exists. Make `describe_index` treat
   "forbidden" and "nothing there" identically. *Success:* those two calls return
   byte-identical error text, and the server's joinable log (or a second stderr line) still
   records which one actually happened.

## Recap

- Raw discovery fails differently on the two backends. **Postgres** is small but says little:
  no meaning, no join directions, no enum values. **OpenSearch** is huge and mostly empty: 296
  mapped fields per daily index, 12 to 22 of them populated, and 1.1 MB for `logs-*`.
- Permissions shape discovery. No persona can list every index; field-level security hides
  fields silently; a masked field aggregates but won't filter.
- A **curated catalog** carries:
  - meaning, written for a model;
  - how to ask (filter vs search, the time field, retention);
  - example values;
  - only the fields that matter;
  - a **sensitivity** tag per field;
  - a per-persona **allowlist**.
- The catalog is not the security boundary; it must **agree** with the backend, and
  `--check` proves it, in CI.
- Serve it per caller (`cacheScope: private`), answer "doesn't exist" and "not yours"
  identically, and version it.
- Measured: discovering everything costs ~15k tokens raw and ~5k curated for the L1 analyst.
  The catalog is 6× smaller for the logs but larger for the database, where the meaning is
  worth its bytes. Accuracy is measured over **repeated runs**, and the spread is reported.

## Next

**[Unit 4: Searching Logs and Incidents](04-searching-logs-and-incidents.md)**: the OpenSearch
server's real tools.
You'll build bounded time windows, aggregations before raw hits, and pagination, then
investigate the planted incident yourself before letting the host do it.
