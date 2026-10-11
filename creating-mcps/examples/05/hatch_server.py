"""
Unit 5, the hybrid: restrictive tools by default, plus one escape hatch behind a person.

When no task-shaped tool fits, the model can call `escalate_query` with a query and a reason.
Nothing runs until the *person* using the host approves it, through elicitation
(`input_required`, as in Unit 2). The SDK seals the round's request_state and binds it to the
arguments, so an approval can't be replayed onto a different query. Approval opens the hatch
to the *guarded* tier, not to raw access: the same sql_guard / ppl_guard checks still run.
A person approves the question; the guard still decides what the query may do.
"""

import os
import sys
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import ppl_guard  # noqa: E402
import sql_guard  # noqa: E402
from common_mcp import joinable_log, load_env, log_note, os_client, pg_connect  # noqa: E402

load_env()
PERSONA = os.environ.get("MCP_PERSONA", "l1_analyst")
ASKED = "asked-the-user"

mcp = MCPServer(
    "hatch",
    version="0.5.0",
    instructions=("Escape hatch. Only when no other tool can answer: escalate_query asks the "
                  "user to approve one read-only query (SQL on the CMDB, or PPL on the SIEM)."),
)
mcp.middleware.append(joinable_log("hatch"))


@mcp.tool()
async def escalate_query(language: Literal["sql", "ppl"], query: str, reason: str,
                         ctx: Context, start: str | None = None,
                         end: str | None = None) -> dict[str, Any] | InputRequiredResult:
    """LAST RESORT, when no other tool can answer: ask the user to approve one read-only
    query. sql: one SELECT on the cmdb schema (schema-qualified tables). ppl: a PPL query on
    a SIEM index pattern, with start/end (UTC) for logs. `reason` says, in one sentence, why
    the other tools can't answer. The user sees the query and the reason."""
    caps = ctx.client_capabilities
    if caps is None or caps.elicitation is None:
        raise ToolError("this host can't ask the user for approval, so the hatch is closed. "
                        "Answer with the other tools, or say the question needs a person.")
    answer = (ctx.input_responses or {}).get("approve")
    if answer is None or ctx.request_state != ASKED:
        window = f"\nWindow: {start} to {end or 'now'}" if start else ""
        log_note(hatch="asked")
        return InputRequiredResult(request_state=ASKED, input_requests={"approve": ElicitRequest(
            params=ElicitRequestFormParams(
                message=(f"The assistant wants to run a {language.upper()} query that no "
                         f"regular tool covers.\n\nWhy: {reason}\n\n{query}{window}\n\n"
                         "Accept to run it (read-only, guarded); decline to refuse."),
                requested_schema={"type": "object", "properties": {}},
            ))})
    if answer.action != "accept":
        log_note(hatch="declined")
        raise ToolError("the user didn't approve this query. Don't retry it; answer with the "
                        "other tools or say what you couldn't find.")
    log_note(hatch="approved")
    try:
        if language == "sql":
            with pg_connect(PERSONA) as conn:
                return sql_guard.run(conn, query)
        return ppl_guard.run(os_client(PERSONA), query, PERSONA, start, end)
    except (sql_guard.SqlRefused, ppl_guard.PplRefused) as e:
        raise ToolError(f"approved, but the guard refused it: {e}") from None
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"{type(e).__name__}: {str(e).strip().splitlines()[0][:300]}") from None


if __name__ == "__main__":
    mcp.run()
