"""
Unit 10: SOC actions, the server whose tools change something. A stand-in for a SOAR platform:
it records each action in MCP_OUTBOX (one JSON line per action) instead of touching the
estate, so an attack that gets through leaves evidence and no damage.

Two of this unit's defences live here:

    least privilege   only an incident responder (l3_responder) may change an incident's
                      status; the persona is MCP_PERSONA, as in Unit 5
    approvals         MCP_APPROVALS=1: every action first asks the person using the host to
                      confirm the exact change (input_required, as Unit 2's open_ticket does).
                      Only they can say yes; the model can't answer for them

    MCP_PERSONA=l3_responder MCP_APPROVALS=1 python examples/10/actions_server.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult, ToolAnnotations

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import joinable_log, persona  # noqa: E402

APPROVALS = os.environ.get("MCP_APPROVALS", "0").lower() in ("1", "true", "yes")
OUTBOX = os.environ.get("MCP_OUTBOX")
MAY_CHANGE_STATUS = {"l3_responder"}
ASKED = "asked-the-person"

mcp = MCPServer("soc-actions", version="0.10.0",
                instructions="Actions on SOC incidents. Each one is recorded.")
mcp.middleware.append(joinable_log("soc_actions"))


def _confirm(ctx: Context, key: str, message: str) -> InputRequiredResult | None:
    """Round 1: ask the person and stop. Round 2: return None if they accepted, else refuse."""
    if not APPROVALS:
        return None
    caps = ctx.client_capabilities
    if caps is None or caps.elicitation is None:
        raise ToolError("this action needs a host that can ask the person to confirm it. "
                        "Nothing was changed.")
    answer = (ctx.input_responses or {}).get(key)
    if answer is None or ctx.request_state != ASKED:
        return InputRequiredResult(request_state=ASKED, input_requests={key: ElicitRequest(
            params=ElicitRequestFormParams(message=message, requested_schema={
                "type": "object", "properties": {}}))})
    if answer.action != "accept":
        raise ToolError("the person declined: nothing was changed. Don't retry unless they "
                        "ask for it themselves.")
    return None


def _record(action: str, **fields) -> dict[str, Any]:
    entry = {"at": datetime.now(UTC).isoformat(), "action": action, "by": persona(), **fields}
    if OUTBOX:
        with open(OUTBOX, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
async def set_incident_status(incident_id: str,
                              status: Literal["open", "in_progress", "closed", "false_positive"],
                              note: str, ctx: Context) -> dict[str, Any] | InputRequiredResult:
    """Change an incident's status, with a note saying why. Closing an incident, or marking it
    a false positive, stops the investigation."""
    if persona() not in MAY_CHANGE_STATUS:
        raise ToolError(f"{persona()} may not change an incident's status; an incident "
                        "responder can. Nothing was changed.")
    incident_id = incident_id.strip().upper()
    ask = _confirm(ctx, "confirm", f"Set {incident_id} to {status}?\n\nNote: {note}")
    if ask is not None:
        return ask
    return _record("set_incident_status", incident_id=incident_id, status=status, note=note)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
async def notify(channel: str, message: str, ctx: Context) -> dict[str, Any] | InputRequiredResult:
    """Post a message to a team's chat channel (e.g. #soc-oncall)."""
    ask = _confirm(ctx, "confirm", f"Post to {channel}?\n\n{message}")
    if ask is not None:
        return ask
    return _record("notify", channel=channel, message=message)


if __name__ == "__main__":
    mcp.run()
