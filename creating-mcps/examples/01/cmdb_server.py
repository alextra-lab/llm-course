"""
Unit 1 reference solution: a first CMDB server on the 2026-07-28 protocol, with the SDK v2.

Serves the course CMDB (from the in-memory fixture, so no Docker is needed) as:
  * two tools      get_asset(hostname), find_assets(service, environment, limit)
  * two resources  cmdb://services and the template cmdb://services/{name}
  * one prompt     triage_host(hostname)

and writes one joinable JSONL record per request to stderr (stdout is the protocol channel:
nothing else may ever be printed there). It is a process a client starts, not a script you
run by hand:

    python examples/01/client.py                           # the normal way to drive it
    python examples/01/cmdb_server.py < /dev/null          # clean exit at EOF
"""

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Literal, TypedDict

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import cmdb_fixture  # noqa: E402

CMDB = cmdb_fixture()
TEAMS = {t["id"]: t for t in CMDB["teams"]}
PEOPLE = {p["id"]: p for p in CMDB["people"]}
SERVICES = {s["id"]: s for s in CMDB["services"]}
SERVICE_BY_NAME = {s["name"]: s for s in CMDB["services"]}
ASSETS = {a["hostname"]: a for a in CMDB["assets"]}

# The lists change only when the code does, so let clients cache them for five minutes, and
# share that cache across callers ("public"): every caller sees the same lists -- for now.
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


# --- the joinable log: one record per request, stamped with session_id / trace_id / step ---
def _meta_dict(meta) -> dict:
    if not meta:
        return {}
    return meta if isinstance(meta, dict) else meta.model_dump(by_alias=True, exclude_none=True)


def _trace_id(meta: dict) -> tuple[str, str]:
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
        "session_id": meta.get("session_id"),     # the caller's; a server cannot invent it
        "trace_id": trace_id,
        "step": meta.get("step"),
        "operation": "mcp.request",
        "server": "cmdb",
        "method": ctx.method,
        "name": params.get("name") or params.get("uri"),
        "arg_keys": sorted((params.get("arguments") or {}).keys()),  # keys, never values
        "client": (meta.get("io.modelcontextprotocol/clientInfo") or {}).get("name"),
        "protocol_version": ctx.protocol_version,
        "trace_origin": trace_origin,
    }
    t0 = time.perf_counter()
    try:
        result = await call_next(ctx)
    except MCPError as e:                      # a protocol error: bad params, unknown method...
        record.update(outcome="error", code=e.error.code)
        raise
    except Exception as e:                     # anything else is a bug in the server
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


# --- tools ----------------------------------------------------------------------------------
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
        # The owner's name and team, but not their e-mail or phone: the output schema itself
        # decides that personal data never leaves through this tool.
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


# --- resources: things the host can load and show, addressed by URI -------------------------
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
    hosts: dict[str, int] = {}
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


# --- prompts: reusable instructions the *user* picks (a slash command in most hosts) --------
@mcp.prompt()
def triage_host(hostname: str) -> str:
    """Start triaging an alert on a host: what it is, who owns it, and its blast radius."""
    return (f"An alert fired on {hostname}. Use get_asset to find what the host is and who "
            "owns it, then read its service (cmdb://services/<name>) to see what depends on "
            "it. Answer in three lines: what it is, who to contact, and what else is at risk "
            "if it is compromised.")


if __name__ == "__main__":
    mcp.run()  # stdio by default: JSON-RPC on stdin/stdout, one message per line
