"""
Unit 5, restrictive end of the dial: the CMDB as task-shaped tools.

Each tool answers one kind of question an analyst actually asks, with the SQL written once,
by a person, with parameters. The model can't write a query at all, so the blast radius is
the tools' own SQL, whoever calls them. What it gives up is flexibility: a question no tool
was shaped for can only be answered by stitching tools together, or not at all.

get_asset and find_assets are Unit 3's, registered here unchanged. The rest are new.
"""

import importlib.util
import os
import sys
from pathlib import Path
from typing import Literal, TypedDict

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent))  # creating-mcps/examples/
from common_mcp import joinable_log, load_env, pg_connect  # noqa: E402

load_env()
PERSONA = os.environ.get("MCP_PERSONA", "l1_analyst")

_spec = importlib.util.spec_from_file_location("unit3_cmdb", HERE.parent / "03" / "cmdb_server.py")
unit3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(unit3)

mcp = MCPServer(
    "cmdb",
    version="0.5.0-tasks",
    instructions=("Read-only answers from the IT referential (CMDB): hosts and their owners, "
                  "service dependencies, vulnerable hosts, host counts. Hostnames look like "
                  "jump-prod-02; service names like customer-db; CVE ids like CVE-2099-1068."),
    cache_hints={"tools/list": CacheHint(ttl_ms=300_000, scope="public")},
)
mcp.middleware.append(joinable_log("cmdb_tasks"))
mcp.tool()(unit3.get_asset)
mcp.tool()(unit3.find_assets)

Env = Literal["prod", "staging", "dev"]


def _service_id(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM cmdb.services WHERE name = %s", (name,)).fetchone()
    if row is None:
        names = [r[0] for r in conn.execute("SELECT name FROM cmdb.services ORDER BY 1")]
        raise ToolError(f"no service {name!r}. Services: {', '.join(names)}.")
    return row[0]


class Dependencies(TypedDict):
    service: str
    depends_on: list[str]
    depended_on_by: list[str]


@mcp.tool()
def service_dependencies(service: str) -> Dependencies:
    """Both directions for one service: what it depends on, and which services depend on it
    (would break if it went down)."""
    with pg_connect(PERSONA) as conn:
        sid = _service_id(conn, service)
        on = conn.execute("SELECT s.name FROM cmdb.service_dependencies d JOIN cmdb.services s "
                          "ON s.id = d.depends_on_id WHERE d.service_id = %s ORDER BY 1",
                          (sid,)).fetchall()
        by = conn.execute("SELECT s.name FROM cmdb.service_dependencies d JOIN cmdb.services s "
                          "ON s.id = d.service_id WHERE d.depends_on_id = %s ORDER BY 1",
                          (sid,)).fetchall()
    return {"service": service, "depends_on": [r[0] for r in on],
            "depended_on_by": [r[0] for r in by]}


class Affected(TypedDict):
    cve_id: str
    severity: str
    total: int
    hosts: list[str]


@mcp.tool()
def hosts_with_vulnerability(cve_id: str, environment: Env | None = None,
                             status: Literal["open", "mitigated", "accepted"] = "open",
                             limit: int = 50) -> Affected:
    """Hosts with a given vulnerability (CVE id), by finding status (default: open) and
    optionally environment. `total` counts them all; `hosts` lists at most `limit` (1-50)."""
    if not 1 <= limit <= 50:
        raise ToolError("limit must be between 1 and 50.")
    with pg_connect(PERSONA) as conn:
        sev = conn.execute("SELECT severity FROM cmdb.vulnerabilities WHERE cve_id = %s",
                           (cve_id.strip().upper(),)).fetchone()
        if sev is None:
            raise ToolError(f"no vulnerability {cve_id!r} in the CMDB. Ids look like "
                            "CVE-2099-1068.")
        rows = conn.execute(
            "SELECT a.hostname FROM cmdb.asset_vulnerabilities f JOIN cmdb.assets a "
            "ON a.id = f.asset_id WHERE f.cve_id = %s AND f.status = %s "
            "AND (%s::text IS NULL OR a.environment = %s) ORDER BY 1",
            (cve_id.strip().upper(), status, environment, environment)).fetchall()
    return {"cve_id": cve_id.strip().upper(), "severity": sev[0], "total": len(rows),
            "hosts": [r[0] for r in rows[:limit]]}


class Count(TypedDict):
    total: int
    filters: dict


@mcp.tool()
def count_assets(environment: Env | None = None, tier: Literal[1, 2, 3] | None = None,
                 classification: Literal["public", "internal", "confidential", "restricted"]
                 | None = None, service: str | None = None) -> Count:
    """How many hosts match. tier is the tier of the service they run: 1 = business critical,
    3 = least critical."""
    with pg_connect(PERSONA) as conn:
        if service is not None:
            _service_id(conn, service)
        total = conn.execute(
            "SELECT count(*) FROM cmdb.assets a JOIN cmdb.services s ON s.id = a.service_id "
            "WHERE (%s::text IS NULL OR a.environment = %s) "
            "AND (%s::int IS NULL OR s.tier = %s) "
            "AND (%s::text IS NULL OR a.classification = %s) "
            "AND (%s::text IS NULL OR s.name = %s)",
            (environment, environment, tier, tier, classification, classification,
             service, service)).fetchone()[0]
    filters = {k: v for k, v in {"environment": environment, "tier": tier,
                                 "classification": classification,
                                 "service": service}.items() if v is not None}
    return {"total": total, "filters": filters}


if __name__ == "__main__":
    mcp.run()
