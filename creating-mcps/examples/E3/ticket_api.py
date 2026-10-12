"""
E3: the ticketing API -- a downstream service the gateway calls FOR the person.

It is not an MCP server; it's the kind of internal API an MCP server sits in front of. It
takes a bearer token like any OAuth resource and accepts one only if it was issued for IT
(the person's gateway token is refused here: no token passthrough), carries `tickets:read`,
and says who is acting (`act`): every answer names both the person and the server acting
for them, and so does its log.

    GET /tickets/{id}      the ticket, if the person may see it
"""

from __future__ import annotations

import time

import jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tokens import Verifier

TICKETS = {
    "INC-0042": {"title": "svc_backup pivot to db-prod-01", "severity": "high",
                 "status": "containment", "owner": "marc", "restricted": False},
    "INC-0043": {"title": "HR laptop: suspected insider", "severity": "high",
                 "status": "open", "owner": "marc", "restricted": True},
    "CHG-0107": {"title": "Isolate db-prod-01 (emergency)", "severity": "high",
                 "status": "approved", "owner": "marc", "restricted": False},
}
RESTRICTED_TO = {"soc-l3"}          # who may read a restricted ticket


class TicketApi:
    def __init__(self, issuer: str, url: str):
        self.url = url.rstrip("/")
        self.verifier = Verifier(issuer, self.url)
        self.log: list[dict] = []

    def _deny(self, status: int, error: str, why: str, **seen) -> JSONResponse:
        self.log.append({"at": round(time.time(), 3), "outcome": error, "why": why, **seen})
        header = f'Bearer error="{error}", error_description="{why}"'
        return JSONResponse({"error": error, "error_description": why}, status_code=status,
                            headers={"WWW-Authenticate": header})

    async def ticket(self, request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return self._deny(401, "invalid_token", "no token")
        try:
            claims = self.verifier.check(auth[7:])
        except jwt.InvalidTokenError as e:
            return self._deny(401, "invalid_token", str(e))
        seen = {"sub": claims["sub"], "act": (claims.get("act") or {}).get("sub"),
                "jti": claims["jti"]}
        if "tickets:read" not in str(claims.get("scope", "")).split():
            return self._deny(403, "insufficient_scope", "needs tickets:read", **seen)
        if not seen["act"]:
            return self._deny(403, "invalid_token", "no `act`: this API is only called "
                              "through a server acting for someone", **seen)
        ticket_id = request.path_params["ticket_id"]
        ticket = TICKETS.get(ticket_id)
        if ticket is None or (ticket["restricted"]
                              and not RESTRICTED_TO & set(claims.get("groups", []))):
            # Not yours to see looks exactly like not there.
            self.log.append({"at": round(time.time(), 3), "outcome": "not_found",
                             "ticket": ticket_id, **seen})
            return JSONResponse({"error": "not_found"}, status_code=404)
        self.log.append({"at": round(time.time(), 3), "outcome": "ok", "ticket": ticket_id,
                         **seen})
        return JSONResponse({"id": ticket_id, **{k: v for k, v in ticket.items()
                                                 if k != "restricted"},
                             "read_for": seen["sub"], "read_by": seen["act"]})

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/tickets/{ticket_id}", self.ticket)])
