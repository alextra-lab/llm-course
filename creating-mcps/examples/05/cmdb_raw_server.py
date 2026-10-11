"""
Unit 5, permissive end of the dial: the CMDB as one `run_sql` tool.

This is what many database MCP servers ship: the model writes SQL, the server runs it. It is
the most flexible tool there is, and its blast radius is whatever the database account
allows. Here that account is the persona (MCP_PERSONA, default l1_analyst), so Postgres
grants, row-level security and the role's statement_timeout still apply. Read
`blast.py`'s results for what's left once they do: system catalogs, temporary tables,
`;`-chained statements, and a session that can lift its own timeout.

The only change from "run it" is the result-size check from Unit 3, so a single call can't
overflow the model's context. The database has already done the work by then.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import joinable_log, load_env, pg_connect  # noqa: E402

load_env()
PERSONA = os.environ.get("MCP_PERSONA", "l1_analyst")
MAX_RESULT_BYTES = 100_000

mcp = MCPServer(
    "cmdb",
    version="0.5.0-raw",
    instructions="The IT referential (CMDB) in Postgres, schema `cmdb`. Run SQL with run_sql.",
    cache_hints={"tools/list": CacheHint(ttl_ms=300_000, scope="public")},
)
mcp.middleware.append(joinable_log("cmdb_raw"))


@mcp.tool()
def run_sql(sql: str) -> dict[str, Any]:
    """Run SQL on the CMDB (Postgres; tables are in the `cmdb` schema: assets, services,
    service_dependencies, people, teams, vulnerabilities, asset_vulnerabilities) and return
    the result."""
    try:
        with pg_connect(PERSONA, autocommit=True) as conn:
            cur = conn.execute(sql)
            if cur.description is None:
                return {"status": cur.statusmessage}
            columns = [c.name for c in cur.description]
            rows = [[v if isinstance(v, (int, float, str, bool, type(None))) else str(v)
                     for v in r] for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001 -- the database's own message goes back as is
        raise ToolError(f"{type(e).__name__}: {str(e).strip()[:300]}") from None
    out = {"columns": columns, "rows": rows, "returned": len(rows)}
    if len(json.dumps(out)) > MAX_RESULT_BYTES:
        raise ToolError(f"the result has {len(rows)} rows, too big to return. Aggregate or "
                        "add a LIMIT.")
    return out


if __name__ == "__main__":
    mcp.run()
