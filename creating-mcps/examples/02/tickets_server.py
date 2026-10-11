"""
Unit 2: a small ticketing server, the second server the host runs, and the one that asks.

`open_ticket` changes something, so it doesn't just do it: it returns `input_required` with a
form ("open this ticket? at which priority?") that the *host* must show to the *user*. The host
retries the call with the user's answer, and only then is the ticket opened. That's
elicitation on the 2026-07-28 protocol: a multi-round-trip call, with no server-to-client
request and no session. Round 1 also hands the client a `request_state`, which the SDK
seals and binds to this tool and these arguments; round 2 must echo it, so a client can't skip
the question and send a made-up "accept". The open tickets live in this process's memory; a
real ticketing backend holds them elsewhere (enterprise track E1).

    python examples/02/host.py          # the host runs this server next to the CMDB server
"""

import itertools
import sys
from pathlib import Path
from typing import Literal, TypedDict

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import joinable_log  # noqa: E402

mcp = MCPServer(
    "tickets",
    version="0.1.0",
    instructions="Incident tickets. Opening a ticket always asks the user to confirm it first.",
)
mcp.middleware.append(joinable_log("tickets"))

TICKETS: list[dict] = []
_ids = itertools.count(4100)
ASKED = "asked-the-user"   # sealed by the SDK and echoed back by the client: proof we asked


class Ticket(TypedDict):
    id: str
    host: str
    summary: str
    priority: Literal["P1", "P2", "P3"]
    status: Literal["open", "closed"]


@mcp.tool()
async def open_ticket(host: str, summary: str, ctx: Context) -> Ticket | InputRequiredResult:
    """Open an incident ticket about a host. The user is asked to confirm it and to choose its
    priority before anything is created; if they decline, no ticket is opened."""
    caps = ctx.client_capabilities
    if caps is None or caps.elicitation is None:
        raise ToolError("opening a ticket needs a host that can ask the user to confirm it, and "
                        "this one can't. No ticket was opened; tell the user to open it by hand.")

    answer = (ctx.input_responses or {}).get("confirm")
    if answer is None or ctx.request_state != ASKED:   # round 1: ask, and stop here
        return InputRequiredResult(request_state=ASKED, input_requests={"confirm": ElicitRequest(
            params=ElicitRequestFormParams(
                message=f"Open an incident ticket for {host}?\n\n{summary}",
                requested_schema={
                    "type": "object",
                    "properties": {"priority": {
                        "type": "string", "enum": ["P1", "P2", "P3"],
                        "description": "P1 = drop everything, P3 = next business day"}},
                    "required": ["priority"],
                },
            ))})

    if answer.action != "accept":            # round 2: the user said no (or closed the form)
        said = "declined" if answer.action == "decline" else "closed the form"
        raise ToolError(f"the user {said}: no ticket was opened. Don't retry unless they "
                        "ask again.")
    priority = (answer.content or {}).get("priority")
    if priority not in ("P1", "P2", "P3"):
        raise ToolError("the confirmation came back without a valid priority; no ticket was opened.")
    ticket = {"id": f"INC-{next(_ids)}", "host": host, "summary": summary,
              "priority": priority, "status": "open"}
    TICKETS.append(ticket)
    return ticket


class TicketList(TypedDict):
    total: int
    tickets: list[Ticket]


@mcp.tool()
def list_tickets(host: str | None = None) -> TicketList:
    """List the incident tickets opened so far, optionally only those about one host."""
    found = [t for t in TICKETS if host is None or t["host"] == host]
    return {"total": len(found), "tickets": found}


if __name__ == "__main__":
    mcp.run()
