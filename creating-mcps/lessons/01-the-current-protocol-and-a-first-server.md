---
title: "The Current Protocol and a First Server"
linkTitle: "1. The Current Protocol and a First Server"
weight: 1
---

**Goal:** build your first real MCP server, a read-only **CMDB server**, with the official Python
SDK v2, on the current protocol (**2026-07-28**). Along the way you'll see exactly what changed
since the hand-rolled server of Foundations §18: no handshake, no sessions, and every request
carrying what it needs. You'll drive the server with the SDK's `Client`, watch every frame go over
the wire, and give it a joinable log from its first line of code.

**Where this fits:** Unit 0 set up the estate and argued the thesis. This unit is the protocol
foundation the rest of the course stands on. It builds on
[Foundations §18](../../lessons/18-model-context-protocol.md) (MCP by hand) and
[§10](../../lessons/10-observability-and-logging.md) (the joinable log line). To keep the protocol
the only new thing, the server reads the CMDB from an **in-memory fixture**, the same data as the
estate's Postgres, with no Docker needed. Unit 2 puts a model in front of this server; Unit 3 moves
it onto Postgres.

---

## What changed since §18

§18's server spoke the protocol as it launched in 2024. A client opened a connection, sent
`initialize`, waited for the answer, sent `notifications/initialized`, and only then made
requests. What was agreed in that handshake (the protocol version, what each side could do)
lived on the connection for as long as it stayed open. Over HTTP the server went further and
issued an `Mcp-Session-Id`, so requests had to come back to the server that held that session.

The 2026-07-28 revision removed all of that. Here is the change, side by side:

| | 2024 (§18) | 2026-07-28 (this course) |
|---|---|---|
| Starting a conversation | `initialize` + `notifications/initialized`, then requests | Nothing. Send a request. `server/discover` is there if you want to ask first |
| Protocol version, client info, capabilities | Sent once, in the handshake | Sent on **every** request, in `params._meta` |
| Sessions | Connection state; `Mcp-Session-Id` over HTTP | None. Any server instance can answer any request |
| Caching | Not in the protocol | List results carry `ttlMs` and `cacheScope` hints |
| Tool schemas | JSON Schema (draft unspecified) | JSON Schema 2020-12 |
| Tracing | Not in the protocol | W3C trace context (`traceparent`) in `_meta` |
| `ping`, sampling, roots, MCP logging | In the spec | `ping` removed; sampling, roots and logging deprecated |

Every 2026 request now looks like this. The envelope is the three `io.modelcontextprotocol/*`
keys:

```json
{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
 "params": {"name": "get_asset", "arguments": {"hostname": "jump-prod-02"},
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                      "io.modelcontextprotocol/clientInfo": {"name": "unit1-client", "version": "0.1"},
                      "io.modelcontextprotocol/clientCapabilities": {}}}}
```

Why trade about 200 bytes per request for this? Because **statelessness is what lets you
operate a server**. If nothing about the caller lives on the server between requests, you can run
three instances behind a load balancer, restart one mid-incident, or put a gateway in front that
routes each request on its own merits. The operational half of this course (Units 7–9) depends on
that. Anything a tool genuinely needs to remember between calls now goes in an explicit
**handle** that the model passes back, such as a search cursor or a ticket id. It no longer
hides in a connection.

A few things moved out of the protocol rather than disappearing:

- **Sampling** (a server borrowing the client's model) is deprecated. A server that needs a
  model calls a model API itself.
- **Roots** (the client telling a server which folders it may touch) is deprecated. That
  becomes a tool parameter or server configuration.
- **MCP logging** (log messages sent to the client) is deprecated in favour of the server's own
  stderr and OpenTelemetry. That's why this unit's log goes to stderr.

You'll still meet all three in older servers, and the SDK still supports them. This course doesn't
teach them.

> **The SDK speaks both eras.** The Python SDK v2 serves 2024- and 2025-era clients as well as
> 2026 ones. The first frame on a connection decides which: `initialize` starts the old
> conversation, anything carrying the `_meta` envelope starts the new one. Hosts that haven't
> caught up yet can still use everything you build here.

## Three primitives, three controllers

An MCP server offers three kinds of thing. The difference between them is **who decides to use
them**:

- **Tools** are chosen by the **model**. They're actions with typed arguments: "look up this
  host", "search these logs". Most of this course is about tools, because the model is the
  caller you have to design for.
- **Resources** are chosen by the **application**. They're data the host can read and show by
  URI, like `cmdb://services`, either loaded into context up front or offered to the user to
  attach. Unit 3 uses them for catalogs.
- **Prompts** are chosen by the **user**. They're reusable, parameterised instructions; most
  hosts show them as slash commands. "Triage this host" is a prompt.

Errors come in two kinds, and the distinction matters for a model reader:

- A **protocol error** is a JSON-RPC `error`: unknown method, malformed params, unsupported
  version. The request never reached your code, and the model usually never sees it.
- A **tool error** is a *successful* response whose result has `isError: true`, with text the
  model reads. "No asset named jump-prod-2; hostnames look like jump-prod-02" is a tool error.
  Write it so the model can recover.

## Build the server

Refresh your copy of the course helper first. This unit adds `cmdb_fixture()` to it, which
returns the estate's CMDB as plain Python data:

```bash
cp examples/common_mcp.py work/
```

Create **`work/cmdb_server.py`**. Start with the data and the server object:

```python
import json
import sys
import time
import uuid
from typing import Literal, TypedDict

from common_mcp import cmdb_fixture
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

CMDB = cmdb_fixture()
TEAMS = {t["id"]: t for t in CMDB["teams"]}
PEOPLE = {p["id"]: p for p in CMDB["people"]}
SERVICES = {s["id"]: s for s in CMDB["services"]}
SERVICE_BY_NAME = {s["name"]: s for s in CMDB["services"]}
ASSETS = {a["hostname"]: a for a in CMDB["assets"]}

LISTS = CacheHint(ttl_ms=300_000, scope="public")

mcp = MCPServer(
    "cmdb",
    version="0.1.0",
    instructions=(
        "Read-only access to the IT referential (CMDB): hosts, the services they run, and "
        "who owns them. Look hosts up by exact hostname, e.g. jump-prod-02."
    ),
    cache_hints={m: LISTS for m in ("tools/list", "resources/list",
                                    "resources/templates/list", "prompts/list")},
)
```

`MCPServer` is the SDK v2's high-level server (it was called `FastMCP` in v1). Two of its
arguments are worth a second look:

- **`instructions`** is returned to every client that asks `server/discover`. Hosts put it in
  front of the model, so it is the first thing a model reads about your server. Keep it short
  and specific.
- **`cache_hints`** tells clients how long they may reuse a list. The tool, resource and prompt
  lists change only when you redeploy, so five minutes is safe. `public` means a gateway may
  share one cached copy between callers, which is true *for now*: in Unit 7, different callers
  see different tools, and this becomes `private`.

Now the first tool. Its return type does real work:

```python
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


def _asset(a: dict) -> Asset:
    owner = PEOPLE[a["owner_id"]]
    return {
        "hostname": a["hostname"], "ip": a["ip"], "os": a["os"],
        "environment": a["environment"], "classification": a["classification"],
        "service": SERVICES[a["service_id"]]["name"],
        "owner": owner["full_name"], "owner_team": TEAMS[owner["team_id"]]["name"],
        "location": a["location"], "description": a["description"],
    }


@mcp.tool()
def get_asset(hostname: str) -> Asset:
    """Look up one host in the CMDB by its exact hostname (e.g. jump-prod-02): what it runs,
    where, how sensitive it is, and who owns it."""
    asset = ASSETS.get(hostname.strip().lower())
    if asset is None:
        raise ToolError(f"no asset named {hostname!r}. Hostnames look like jump-prod-02; "
                        "use find_assets to list hosts by service or environment.")
    return _asset(asset)
```

From that one decorated function, the SDK builds everything a client needs:

- **The name and description** come from the function name and docstring. The docstring is
  written for the model, not for you.
- **An `inputSchema`** comes from the parameters. A call with `hostname: 42` is rejected before
  your code runs.
- **An `outputSchema`** comes from the `Asset` return type, so every result also carries
  `structuredContent`, a typed JSON object a host can use without parsing text. A text copy is
  included for older clients.
- **`ToolError`** turns into a tool error with *your* message. Any other exception also becomes
  a tool error, but the SDK replaces its message with a generic "Error executing tool
  get_asset", so internals don't leak. So raise `ToolError` with an instruction for anything the
  model can fix, and let real bugs stay generic.

Notice what `Asset` does *not* contain: the owner's e-mail and phone. The CMDB has them. The
cheapest way to keep personal data out of a model's context is for the tool's output schema to
never include it.

The second tool lists hosts, and is bounded from the start:

```python
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
    if service is not None and service not in SERVICE_BY_NAME:
        raise ToolError(f"unknown service {service!r}; read cmdb://services for the list.")
    matches = [a for a in CMDB["assets"]
               if (service is None or SERVICES[a["service_id"]]["name"] == service)
               and (environment is None or a["environment"] == environment)]
    page = [_asset(a) for a in matches[:limit]]
    return {"total": len(matches), "returned": len(page), "assets": page}
```

`Literal[...]` becomes an `enum` in the schema, so the model is told the three valid
environments instead of guessing. Every result reports `total` as well as what it returned. A
model that sees "3 of 8" knows there is more; one that sees three hosts assumes that's all.

Then the resources and a prompt:

```python
@mcp.resource("cmdb://services", mime_type="application/json")
def services() -> str:
    """Every service in the CMDB with its tier (1 = business critical) and owning team."""
    return json.dumps([{"name": s["name"], "tier": s["tier"],
                        "owner_team": TEAMS[s["owner_team_id"]]["name"]}
                       for s in CMDB["services"]])


@mcp.resource("cmdb://services/{name}", mime_type="application/json")
def service(name: str) -> str:
    """One service: what it is, its tier, its owning team and on-call channel, what it
    depends on, what depends on it, and how many hosts run it in each environment."""
    s = SERVICE_BY_NAME.get(name)
    if s is None:
        raise ValueError(f"unknown service {name!r}")
    team = TEAMS[s["owner_team_id"]]
    deps = CMDB["service_dependencies"]
    hosts = {}
    for a in CMDB["assets"]:
        if a["service_id"] == s["id"]:
            hosts[a["environment"]] = hosts.get(a["environment"], 0) + 1
    return json.dumps({
        "name": s["name"], "tier": s["tier"], "description": s["description"],
        "owner_team": team["name"], "oncall": team["oncall_slack"],
        "depends_on": [SERVICES[d["depends_on_id"]]["name"] for d in deps
                       if d["service_id"] == s["id"]],
        "depended_on_by": [SERVICES[d["service_id"]]["name"] for d in deps
                           if d["depends_on_id"] == s["id"]],
        "hosts": hosts,
    })


@mcp.prompt()
def triage_host(hostname: str) -> str:
    """Start triaging an alert on a host: what it is, who owns it, and its blast radius."""
    return (f"An alert fired on {hostname}. Use get_asset to find what the host is and who "
            "owns it, then read its service (cmdb://services/<name>) to see what depends on "
            "it. Answer in three lines: what it is, who to contact, and what else is at risk "
            "if it is compromised.")
```

A URI with `{name}` in it becomes a **resource template**: one function serves
`cmdb://services/remote-access`, `cmdb://services/customer-db`, and so on.

## A joinable log from the first line

The [Observability Standard](../../OBSERVABILITY.md) asks for one JSONL record per operation,
stamped with `session_id`, `trace_id` and `step`, so that every record from one agent run can be
joined back together. A server has a problem with that: those ids belong to the *caller*. The
protocol gives the caller a place to send them, in `_meta`, and a standard for one of them:
the W3C `traceparent`, whose second field is the trace id. `session_id` and `step` are this
course's own `_meta` keys.

The SDK lets you wrap every request in **middleware**: an async function that gets the request
context and a `call_next` to continue the chain. Add one that writes the record:

```python
def _meta_dict(meta):
    if not meta:
        return {}
    return meta if isinstance(meta, dict) else meta.model_dump(by_alias=True, exclude_none=True)


def _trace_id(meta):
    """The caller's W3C trace id from `traceparent`, or a fresh one we flag as ours."""
    parts = str(meta.get("traceparent", "")).split("-")
    if len(parts) == 4 and len(parts[1]) == 32:
        return parts[1], "caller"
    return uuid.uuid4().hex, "server"


async def joinable_log(ctx, call_next):
    meta = _meta_dict(ctx.meta)
    trace_id, trace_origin = _trace_id(meta)
    params = ctx.params or {}
    record = {
        "session_id": meta.get("session_id"),
        "trace_id": trace_id,
        "step": meta.get("step"),
        "operation": "mcp.request",
        "server": "cmdb",
        "method": ctx.method,
        "name": params.get("name") or params.get("uri"),
        "arg_keys": sorted((params.get("arguments") or {}).keys()),
        "client": (meta.get("io.modelcontextprotocol/clientInfo") or {}).get("name"),
        "protocol_version": ctx.protocol_version,
        "trace_origin": trace_origin,
    }
    t0 = time.perf_counter()
    try:
        result = await call_next(ctx)
    except MCPError as e:
        record.update(outcome="error", code=e.error.code)
        raise
    except Exception as e:
        record.update(outcome="crash", error=type(e).__name__)
        raise
    else:
        is_error = (result.get("isError") if isinstance(result, dict)
                    else getattr(result, "is_error", False))
        record["outcome"] = "tool_error" if is_error else "ok"
        return result
    finally:
        record["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        print(json.dumps(record), file=sys.stderr, flush=True)


mcp.middleware.append(joinable_log)

if __name__ == "__main__":
    mcp.run()
```

Three decisions in there are worth defending:

- **It logs to stderr.** Over stdio, the server's stdout *is* the protocol channel. A single
  stray `print()` to stdout corrupts the stream and the client drops the connection. Hosts
  capture a server's stderr into their own logs, which is where an operator will look.
- **It logs argument names, never values** (`arg_keys`). Arguments can carry personal data,
  secrets or hostile text. The Standard's rule R5 says redact at the boundary, and the cheapest
  redaction is not to record the value at all.
- **It never invents the caller's ids.** If no `traceparent` arrives, the server mints a trace id
  so the record is still unique, but marks `trace_origin: "server"`. Then a missing id is
  visible in the log instead of silently papered over. `session_id` stays `null`, which tells
  you which client isn't stamping it.

`mcp.run()` serves over **stdio** by default: JSON-RPC on stdin and stdout, one message per line,
exactly like §18. Unit 9 switches to Streamable HTTP. Nothing above changes when it does.

*(Reference: [`examples/01/cmdb_server.py`](../examples/01/cmdb_server.py).)*

## Drive it with the SDK client

A server is a process a client starts. Create **`work/client.py`**:

```python
import asyncio
import json
import secrets
import sys
import uuid
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.types import Implementation

SERVER = Path(__file__).resolve().parent / "cmdb_server.py"


def trace_meta(session_id, trace_id, step):
    return {"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01",
            "session_id": session_id, "step": step}


async def main():
    server = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    session_id, trace_id = uuid.uuid4().hex, uuid.uuid4().hex
    async with Client(server, client_info=Implementation(name="unit1-client", version="0.1")) as client:
        print(f"protocol {client.protocol_version}, server {client.server_info.name}")
        print(f"instructions: {client.instructions}")

        tools = await client.list_tools()
        print(f"tools: {[t.name for t in tools.tools]} "
              f"(ttlMs={tools.ttl_ms}, cacheScope={tools.cache_scope})")
        await client.list_tools()

        r = await client.call_tool("get_asset", {"hostname": "jump-prod-02"},
                                   meta=trace_meta(session_id, trace_id, 1))
        print(f"get_asset -> {json.dumps(r.structured_content)}")

        r = await client.call_tool("get_asset", {"hostname": "jump-prod-2"},
                                   meta=trace_meta(session_id, trace_id, 2))
        print(f"get_asset (typo) -> isError={r.is_error}: {r.content[0].text}")

        res = await client.read_resource("cmdb://services/remote-access",
                                         meta=trace_meta(session_id, trace_id, 3))
        print(f"cmdb://services/remote-access -> {res.contents[0].text}")


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python work/client.py
```

You'll see the client's output interleaved with the server's log lines on stderr (shortened
here):

```text
{"session_id": null, ..., "method": "server/discover", ..., "trace_origin": "server", "outcome": "ok", "ms": 0.37}
protocol 2026-07-28, server cmdb
instructions: Read-only access to the IT referential (CMDB): hosts, the services they run, ...
{"session_id": null, ..., "method": "tools/list", ..., "outcome": "ok", "ms": 0.42}
tools: ['get_asset', 'find_assets'] (ttlMs=300000, cacheScope=public)
{"session_id": "52fd29...", "trace_id": "2fa45f...", "step": 1, ..., "name": "get_asset", "arg_keys": ["hostname"], ..., "outcome": "ok", "ms": 2.11}
get_asset -> {"hostname": "jump-prod-02", "ip": "10.20.167.107", ..., "service": "remote-access", "owner": "Camille Michel", ...}
{"session_id": "52fd29...", "trace_id": "2fa45f...", "step": 2, ..., "outcome": "tool_error", "ms": 1.32}
get_asset (typo) -> isError=True: Error executing tool get_asset: no asset named 'jump-prod-2'. Hostnames look like jump-prod-02; use find_assets to list hosts by service or environment.
...
```

Read it against the protocol:

1. **The client asked `server/discover` first.** `Client` does that by default to learn the
   server's versions, capabilities and instructions. It's a convenience, not a handshake: no
   state was created, and the next request would work without it.
2. **`list_tools()` was called twice but logged once.** The first answer said `ttlMs=300000`, so
   the SDK client served the second call from its own cache, and no frame went over the wire.
3. **The tool calls share a `session_id` and `trace_id`, and count `step` 1, 2, 3.** Grep the
   server's stderr for that trace id and you have the whole run. The discover and list
   requests have no session, because they belong to the host's setup, not to a turn.
4. **The typo came back as `isError=True` with your hint**, as a result, not an exception. The
   SDK also logs its own line about the failure to stderr.

To *see* the frames, run the same script through the **frame tap**, a 55-line stdio proxy that
records every message in both directions:

```bash
cp examples/01/frame_tap.py work/
python examples/01/client.py --tap frames.jsonl
```

The reference [`examples/01/client.py`](../examples/01/client.py) has the `--tap` flag and calls
`find_assets` and the prompt too. [`examples/01/frame_tap.py`](../examples/01/frame_tap.py) is a
tool, not a lesson, but read it: it's how every later lab shows you the wire.

## Use it from a real host

Your server already works in any MCP host, because it speaks both protocol eras. Point the host
at the *absolute* path of your Python and your server:

- **Claude Code:** `claude mcp add cmdb -- /path/to/.venv/bin/python /path/to/creating-mcps/work/cmdb_server.py`,
  then ask *"who owns jump-prod-02?"* and approve the tool call.
- **VS Code (Copilot agent mode):** add it to `.vscode/mcp.json`:

  ```json
  {"servers": {"cmdb": {"type": "stdio",
                        "command": "/path/to/.venv/bin/python",
                        "args": ["/path/to/creating-mcps/work/cmdb_server.py"]}}}
  ```

The host decides which protocol era it speaks, and the server's stderr log shows you which
(`protocol_version` on each record). Unit 2 builds a host of your own, with `gpt-oss-120b` doing
the choosing.

> **Security:** this server has **no idea who is calling**. It returns `restricted` assets such
> as `db-prod-01` to anyone, which Unit 0's L1 analyst could not see in Postgres. That's
> acceptable for a fixture on your laptop and nowhere else; Unit 7 carries the caller's identity
> through to the backend. Two defences are already in place:
> - the output schema leaves personal data (owner e-mail, phone) out entirely;
> - unexpected exceptions reach the model as a generic error, never a traceback.
>
> One thing is not defended yet. `get_asset` returns each host's free-text `description`
> verbatim, and one host in the estate, `build-legacy-01`, has a description written to steer an
> AI assistant. Look at it (Challenge 3). Text that a server passes through from its backend is
> untrusted input to the model (Foundations §21). Unit 10 is about exactly this.

> **Observe:** every request now leaves one joinable JSONL record on stderr: `session_id`,
> `trace_id` and `step` from the caller's `_meta`, plus the method, the tool or resource name,
> the argument *names*, the outcome and the latency. The loop it closes is **attribution**: given
> any agent run, you can pull every request it made to this server and see which ones failed and
> how long each took. A record whose `trace_origin` is `server` names a client that isn't
> propagating context, which is the first thing to fix when a trace has gaps.

> **Limits:** the protocol's own overhead is tiny. A local stdio call takes about 1–2 ms
> round trip, and most of that is the tool itself. The cost that grows is **bytes**: the 2026
> envelope adds about 200 bytes to every request, and `find_assets` returns about 300 bytes
> per host, so its `limit` cap of 50 is also a cap on how much context one call can consume. Two
> mechanisms hold that down: list caching (`ttlMs`) saves a round trip and a few kilobytes per
> turn, and `limit` with `total` keeps one call from flooding the context. Unit 4 makes
> "bounded by default" the rule for search.

## Lab: a protocol inspector

Open [`labs/01_protocol_inspector.py`](../labs/01_protocol_inspector.py):

```bash
cp labs/01_protocol_inspector.py work/
marimo edit work/01_protocol_inspector.py
```

It runs §18's server and your Unit 1 server side by side, and you send hand-written frames to
either or both:

- send `tools/list` with and without the `_meta` envelope;
- try the 2024 handshake on the 2026 server;
- ask for a protocol version that doesn't exist;
- watch the byte column grow.

The second half runs `client.py` through the frame tap and lays out every frame next to the
server's log lines, so you can follow one `trace_id` from the client, across the wire, and into
the log. It needs neither the estate nor the model endpoint.

## Challenges

1. **A third tool.** Add `assets_with_cve(cve_id)`: the hosts affected by a CVE (from
   `asset_vulnerabilities`), with each finding's `status`. Return a bounded page like
   `find_assets`, and make an unknown CVE id a `ToolError` that says what CVE ids look like.
   *Success:* `CVE-2099-1001` returns a page with a correct `total`, `CVE-2099-0` returns
   `isError: true` with your hint, and the frame tap shows your tool's `outputSchema` in
   `tools/list`.
2. **Ids from the host side.** Run `client.py` without passing `meta=` on the tool calls.
   *Success:* you can point to the log records whose `trace_origin` is `server`, explain why
   their `session_id` is `null`, and say in one sentence why the server must not make up a
   `session_id` itself.
3. **Read the hostile record.** Call `get_asset("build-legacy-01")` and read its `description`.
   *Success:* you can say what that text is trying to make an agent do, whose data it is after,
   and why returning it verbatim from a "read-only" tool is still a risk. (Don't fix
   it yet; Unit 10 measures the fixes.)

## Recap

- The **2026-07-28** protocol has no handshake and no sessions. Every request carries its
  version, client info and capabilities in `_meta`, so any server instance can answer any
  request. `server/discover` is optional.
- **Tools** are chosen by the model, **resources** by the application, **prompts** by the
  user. A **tool error** is a result the model reads (`isError: true`); a **protocol error**
  never reaches your code.
- With the SDK v2, one decorated function gives you an input schema, an **output schema**, and
  `structuredContent`. The return type decides what can leave the server; `ToolError` decides
  what the model is told.
- `cache_hints` sets `ttlMs`/`cacheScope` on lists, and the SDK client honours them.
- A **middleware** writes one joinable record per request to **stderr**, using the caller's
  `traceparent`, `session_id` and `step`, recording argument names, never values.

## Next

**Unit 2: The Host Side** *(coming next)*: you'll build the other end, a host that bridges these
tools into `gpt-oss-120b`'s tool calling, runs several servers at once, and handles
elicitation the 2026 way, with `input_required`.
