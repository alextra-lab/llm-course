"""
Unit 3: raw discovery -- the schema exactly as Postgres and OpenSearch describe it.

This is the approach most database and search MCP servers ship: list the tables or indexes,
then describe one. It needs no maintenance and is never out of date. It also tells the model
nothing about what the data *means*, and on a log cluster it returns hundreds of empty fields
per index. Unit 3 measures it against the curated catalog (catalog_server.py).

The server acts as ONE persona for every call, chosen when it starts (default l1_analyst).
Unit 7 replaces that with the real caller's identity.

    MCP_PERSONA=l3_responder python examples/02/host.py ...   # (via a host's server table)
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, TypedDict

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import joinable_log, load_env, os_client, pg_connect  # noqa: E402

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
        except Exception:  # noqa: BLE001 -- 403: this persona can't read that pattern
            continue
        out += [{"index": r["index"], "docs": int(r["docs.count"])} for r in rows]
    return sorted(out, key=lambda r: r["index"])


@mcp.tool()
def describe_index(index: str) -> dict[str, Any]:
    """The field mapping of an index or index pattern, as OpenSearch returns it."""
    try:
        mapping = os_client(PERSONA).indices.get_mapping(index=index)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"can't read the mapping of {index!r}: {type(e).__name__}. "
                        "list_indexes shows the indexes you can read.") from None
    size = len(json.dumps(mapping))
    if size > MAX_RESULT_BYTES:
        raise ToolError(f"the mapping of {index!r} is {size:,} bytes, too big to return; "
                        "describe a single daily index (see list_indexes) instead.")
    return mapping


if __name__ == "__main__":
    mcp.run()
