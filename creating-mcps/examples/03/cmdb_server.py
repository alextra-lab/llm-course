"""
Unit 3: Unit 1's CMDB server, moved from the in-memory fixture onto Postgres.

Same tools, same answers, one difference that matters: every query runs AS a persona
(`MCP_PERSONA`, default l1_analyst), so Postgres decides what comes back. An L1 analyst asking
for db-prod-01 (a `restricted` host) gets "no asset named ...": row-level security hid the row
before this code ever saw it. The column list never selects people.email or people.phone, and
for an L1 analyst the database would refuse them anyway.

One connection per call keeps it simple. Unit 8 replaces that with a bounded pool.
"""

import os
import sys
from pathlib import Path
from typing import Literal, TypedDict

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import joinable_log, load_env, pg_connect  # noqa: E402

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
            raise ToolError(f"unknown service {service!r}; list_datasets / the catalog "
                            "lists services in cmdb.services.")
        total = conn.execute("SELECT count(*) FROM cmdb.assets a JOIN cmdb.services s "
                             "ON s.id = a.service_id" + clause, args).fetchone()[0]
        rows = conn.execute(ASSET_SQL + clause + " ORDER BY a.hostname LIMIT %s",
                            [*args, limit]).fetchall()
    page = [_asset(r) for r in rows]
    return {"total": total, "returned": len(page), "assets": page}


if __name__ == "__main__":
    mcp.run()
