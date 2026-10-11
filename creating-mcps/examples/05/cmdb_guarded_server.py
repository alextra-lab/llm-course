"""
Unit 5, the guarded middle: the CMDB as one `query_sql` tool that checks before it runs.

Same flexibility as run_sql for anything that is one read-only SELECT on the `cmdb` schema.
Everything else is refused with an instruction, by sql_guard.py: a parse-tree check (one
statement, reads only, allowlisted schema, no unknown functions), then a row cap and a READ
ONLY transaction with a 2 s statement_timeout of its own, as the persona.
"""

import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import sql_guard  # noqa: E402
from common_mcp import joinable_log, load_env, log_note, pg_connect  # noqa: E402

load_env()
PERSONA = os.environ.get("MCP_PERSONA", "l1_analyst")

mcp = MCPServer(
    "cmdb",
    version="0.5.0-guarded",
    instructions=("The IT referential (CMDB) in Postgres. query_sql runs one read-only SELECT "
                  "on the `cmdb` schema, at most 200 rows, 2 seconds."),
    cache_hints={"tools/list": CacheHint(ttl_ms=300_000, scope="public")},
)
mcp.middleware.append(joinable_log("cmdb_guarded"))


@mcp.tool()
def query_sql(sql: str) -> dict[str, Any]:
    """Run one read-only SELECT on the CMDB (Postgres). Tables, always schema-qualified:
    cmdb.assets, cmdb.services, cmdb.service_dependencies, cmdb.people, cmdb.teams,
    cmdb.vulnerabilities, cmdb.asset_vulnerabilities. At most 200 rows come back: aggregate
    (count, GROUP BY) rather than listing. Standard SQL functions only."""
    try:
        with pg_connect(PERSONA) as conn:
            out = sql_guard.run(conn, sql)
    except sql_guard.SqlRefused as e:
        log_note(guard="refused")
        raise ToolError(str(e)) from None
    except Exception as e:  # noqa: BLE001 -- the database refused or timed out
        log_note(guard="passed", backend="error")
        raise ToolError(f"{type(e).__name__}: {str(e).strip().splitlines()[0][:300]}") from None
    log_note(guard="passed", returned=out["returned"])
    return out


if __name__ == "__main__":
    mcp.run()
