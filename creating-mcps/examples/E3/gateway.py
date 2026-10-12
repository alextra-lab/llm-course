"""
E3: the gateway as an OAuth protected resource -- found, not configured.

The host is given one URL. Everything else it discovers:

    POST /mcp, no token     401, `WWW-Authenticate: Bearer resource_metadata=".../.well-known/
                            oauth-protected-resource/mcp"` (the SDK, from AuthSettings)
    that metadata           this resource's URL and the authorization server that issues for it
                            (RFC 9728); from there, the server's own metadata (RFC 8414)

Then on every request the token must be for THIS URL (aud), from that issuer, in date and
carry `mcp:tools`. Three tools show the rest:

    whoami          what the token says: who, which host, which scopes, for how long
    ticket_status   reads a ticket from the ticketing API FOR the person: the gateway
                    exchanges their token for one to that API (RFC 8693) and never forwards
                    theirs; the API sees both of them, the person and the gateway acting
    isolate_host    needs `mcp:actions` as well. A token without it is stopped before the
                    tool runs with 403 `insufficient_scope` naming the scopes needed, and the
                    host goes back to ask the person for more: STEP-UP. Only people whose
                    groups allow it can approve. (E1 isolates for real; here the point is
                    who may ask.)

drill.py starts it with the authorization server and the ticket API.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl
from starlette.types import ASGIApp, Receive, Scope, Send

from auth_server import ACCESS_TOKEN_TYPE, TOKEN_EXCHANGE
from tokens import Verifier

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)
CHANGES = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True,
                          open_world_hint=False)
BASE_SCOPE = "mcp:tools"
STEP_UP = {"isolate_host": "mcp:actions"}         # tool -> the extra scope it needs


def _caller() -> dict:
    token = get_access_token()
    if token is None:                               # can't happen over HTTP: the SDK 401s first
        raise ToolError("no token")
    return {"token": token.token, "scopes": token.scopes, **(token.claims or {})}


class StepUp:
    """Ask for more scope before the tool runs, in the one place OAuth can say so: HTTP.

    A tool can only fail; a 403 with `error="insufficient_scope"` and the scopes needed tells
    the host what to ask the person for, and the SDK's client does just that. The tool's name
    comes from the `Mcp-Name` header, which the SDK refuses when it differs from the body, and
    the tool checks the scope again anyway."""

    def __init__(self, app: ASGIApp, verifier: Verifier, metadata_url: str):
        self.app, self.verifier, self.metadata_url = app, verifier, metadata_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        needs = STEP_UP.get(headers.get("mcp-name", "")) if (
            scope["type"] == "http" and headers.get("mcp-method") == "tools/call") else None
        auth = headers.get("authorization", "")
        if needs and auth.lower().startswith("bearer "):
            try:
                have = str(self.verifier.check(auth[7:]).get("scope", "")).split()
            except jwt.InvalidTokenError:
                have = None                         # a bad token: the SDK's 401 handles it
            if have is not None and needs not in have:
                want = " ".join(dict.fromkeys([*have, needs]))
                header = (f'Bearer error="insufficient_scope", scope="{want}", '
                          f'resource_metadata="{self.metadata_url}", '
                          f'error_description="{headers["mcp-name"]} needs {needs}"')
                body = (b'{"error":"insufficient_scope","error_description":"'
                        + f'{headers["mcp-name"]} needs {needs}'.encode() + b'"}')
                await send({"type": "http.response.start", "status": 403, "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", header.encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


class Gateway:
    def __init__(self, issuer: str, resource: str, tickets: str, client_id: str,
                 client_secret: str):
        self.issuer, self.resource, self.tickets = issuer.rstrip("/"), resource, tickets
        self.client_id, self.client_secret = client_id, client_secret
        self.verifier = Verifier(issuer, resource)
        self.isolations: list[dict] = []

    async def exchange(self, subject_token: str) -> dict:
        """The person's token (for us) -> a token for the ticket API, acting for them."""
        async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
            r = await http.post(f"{self.issuer}/token", data={
                "grant_type": TOKEN_EXCHANGE, "client_id": self.client_id,
                "client_secret": self.client_secret, "subject_token": subject_token,
                "subject_token_type": ACCESS_TOKEN_TYPE, "resource": self.tickets,
                "scope": "tickets:read"})
        if r.status_code != 200:
            raise ToolError(f"the authorization server refused the exchange: "
                            f"{r.json().get('error_description', r.text)}")
        return r.json()

    def build(self) -> MCPServer:
        mcp = MCPServer("e3-gateway", version="0.13.0", token_verifier=self.verifier,
                        instructions="Incident desk. Tools act as the signed-in person.",
                        auth=AuthSettings(issuer_url=self.issuer, resource_server_url=self.resource,
                                          required_scopes=[BASE_SCOPE],
                                          validate_token_resource=True))

        @mcp.tool(annotations=READ_ONLY)
        def whoami() -> dict[str, Any]:
            """Who this session acts for, and what the token allows."""
            c = _caller()
            return {"sub": c["sub"], "groups": c.get("groups", []), "client_id": c["client_id"],
                    "scopes": c["scopes"], "aud": c["aud"], "jti": c["jti"],
                    "expires_in": c["exp"] - int(time.time())}

        @mcp.tool(annotations=READ_ONLY)
        async def ticket_status(ticket: str) -> dict[str, Any]:
            """A ticket from the ticketing system (INC-0042, CHG-0107, ...), read as you."""
            c = _caller()
            got = await self.exchange(c["token"])
            claims = jwt.decode(got["access_token"], options={"verify_signature": False})
            async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
                r = await http.get(f"{self.tickets}/tickets/{ticket}",
                                   headers={"Authorization": f"Bearer {got['access_token']}"})
            if r.status_code == 404:
                raise ToolError(f"no ticket {ticket} that {c['sub']} can see")
            if r.status_code != 200:
                raise ToolError(f"the ticketing API refused: {r.text[:200]}")
            # What the gateway used is described, never returned: a token is not data.
            return {"ticket": r.json(), "exchanged": {
                "aud": claims["aud"], "sub": claims["sub"], "act": claims.get("act"),
                "scope": claims["scope"], "expires_in": got["expires_in"],
                "jti": claims["jti"]}}

        @mcp.tool(annotations=CHANGES)
        def isolate_host(hostname: str, ticket: str) -> dict[str, Any]:
            """Ask for a host to be cut off the network. Needs the mcp:actions scope."""
            c = _caller()
            if STEP_UP["isolate_host"] not in c["scopes"]:      # never trust only the door
                raise ToolError("isolate_host needs mcp:actions")
            request = {"hostname": hostname, "ticket": ticket, "requested_by": c["sub"],
                       "via": c["client_id"], "scopes": c["scopes"], "at": round(time.time())}
            self.isolations.append(request)
            return {"requested": True, **request}

        return mcp

    def app(self) -> ASGIApp:
        metadata_url = str(build_resource_metadata_url(AnyHttpUrl(self.resource)))
        return StepUp(self.build().streamable_http_app(), self.verifier, metadata_url)
