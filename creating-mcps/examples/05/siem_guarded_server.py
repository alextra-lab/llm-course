"""
Unit 5, the guarded middle for OpenSearch: one `ppl_query` tool.

The model writes PPL; ppl_guard.py checks the source and the commands, rewrites the source to
the daily indexes of the window the caller passes, adds the exact time filter and a row cap,
then checks the compiled plan before it runs. As the persona, so index permissions,
field-level security and masking still apply to whatever PPL reads.
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
import ppl_guard  # noqa: E402
from common_mcp import joinable_log, load_env, log_note, os_client  # noqa: E402

load_env()
PERSONA = os.environ.get("MCP_PERSONA", "l1_analyst")

mcp = MCPServer(
    "siem",
    version="0.5.0-guarded",
    instructions=(
        "SOC logs and incidents in OpenSearch, queried with PPL. Index patterns you may use: "
        f"{', '.join(ppl_guard.allowed_indexes(PERSONA))}. Log queries take a window "
        "(start, end; UTC) as arguments, at most 7 days."),
    cache_hints={"tools/list": CacheHint(ttl_ms=300_000, scope="private")},
)
mcp.middleware.append(joinable_log("siem_guarded"))


@mcp.tool()
def ppl_query(query: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Run one PPL query, e.g.
    source = logs-auth-* | where `event.outcome` = 'failure' | stats count() by `source.ip`
    Quote dotted field names with backticks. For logs-* pass the window as start/end
    (ISO 8601 like 2026-10-09T00:00:00Z, or now-24h; at most 7 days); don't put it in the
    query. incidents needs no window. Allowed commands: where, fields, stats, sort, head,
    dedup, top, rare, rename, eval, timechart, fillnull. At most 200 rows come back."""
    try:
        out = ppl_guard.run(os_client(PERSONA), query, PERSONA, start, end)
    except ppl_guard.PplRefused as e:
        failed = str(e).startswith("the query failed")      # passed the guard, then failed
        log_note(guard="passed", backend="error") if failed else log_note(guard="refused")
        raise ToolError(str(e)) from None
    log_note(guard="passed", returned=out["returned"])
    return out


if __name__ == "__main__":
    mcp.run()
