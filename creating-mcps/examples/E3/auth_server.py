"""
E3: the authorization server -- Unit 7's token issuer, grown into a real OAuth 2.1 one.

Unit 7 handed tokens out directly. Here a host that has never seen the gateway gets one the
way a real deployment does, and every step is a separate thing an attacker could abuse, so
every step is checked:

    /.well-known/oauth-authorization-server   what this server does and where (RFC 8414)
    /jwks.json                                its PUBLIC keys: resources verify with these
    /register      a host registers itself (RFC 7591): redirect URIs must be loopback or
                   https, and the host gets a client_id it can't choose
    /authorize     the host sends the person here with a PKCE challenge, the scopes it wants
                   and the ONE resource the token is for (RFC 8707); the server shows them a
    /consent       page: which host, sending them back where, asking what, for which
                   server. They sign in (the lab picks a name) and approve or deny; their
                   groups decide which scopes they can approve at all
    /token         the code (once, with the PKCE verifier) for an access token bound to that
                   resource and a refresh token; a refresh token (once: each use rotates it,
                   and a reused one revokes the whole family); and, for a registered
                   resource server only, TOKEN EXCHANGE (RFC 8693): the person's token for
                   one to a downstream API, saying who acts for them (`act`)
    /revoke        a refresh token, now (RFC 7009)

Access tokens are short-lived Ed25519 JWTs; nothing about a person's rights is in them but
their groups and the scopes they approved. The signing key is made at start-up and never
written down: restart the server and every token dies, which is what you want of a lab.

    python examples/E3/auth_server.py --port 8790     # alone; drill.py starts all three
"""

from __future__ import annotations

import argparse
import html
import secrets
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams,
                                      AuthorizeError, RefreshToken, RegistrationError,
                                      TokenError, construct_redirect_uri)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "07")]
from issuer import DIRECTORY  # noqa: E402  -- the same people and groups as Unit 7

ALGORITHM = "EdDSA"
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
ACCESS_TTL, REFRESH_TTL, EXCHANGED_TTL, CODE_TTL = 300, 86400, 120, 60

# What each scope lets a token do, and which directory groups may approve it. The person
# approves; their groups bound what they CAN approve. sam (contractors) approves nothing.
SCOPES = {
    "mcp:tools":    ("use the gateway's read-only tools", {"soc-l1", "soc-l3", "audit"}),
    "mcp:actions":  ("ask the gateway to change hosts (isolate, release)", {"soc-l3"}),
    "tickets:read": ("read tickets, through a server acting for you", {"soc-l1", "soc-l3",
                                                                       "audit"}),
}


def allowed(user: str, scopes: list[str]) -> list[str]:
    """The scopes in `scopes` that `user`'s groups don't let them approve."""
    groups = set(DIRECTORY.get(user, []))
    return [s for s in scopes if s not in SCOPES or not groups & SCOPES[s][1]]


def loopback_or_https(uri: str) -> bool:
    u = urlparse(uri)
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "::1",
                                                                         "localhost"))


class AuthServer:
    """The SDK's OAuthAuthorizationServerProvider, in memory, plus what it doesn't cover:
    the consent page, the JWKS and token exchange."""

    def __init__(self, issuer: str):
        self.issuer = issuer.rstrip("/")
        self.key = Ed25519PrivateKey.generate()
        self.kid = uuid.uuid4().hex[:12]
        self.resources: dict[str, dict] = {}       # resource URL -> {name, scopes}
        self.actors: dict[str, dict] = {}          # resource server's client_id -> exchange rules
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending: dict[str, tuple] = {}        # consent request id -> (client, params, at)
        self.codes: dict[str, AuthorizationCode] = {}
        self.used_codes: dict[str, str] = {}       # code -> the token family it started
        self.refresh: dict[str, tuple[RefreshToken, str]] = {}   # token -> (it, family)
        self.rotated: dict[str, str] = {}          # refresh tokens already used -> family
        self.events: list[dict] = []               # the audit log

    # -- set-up --------------------------------------------------------------------------

    def add_resource(self, url: str, name: str, scopes: list[str]) -> None:
        """A server tokens can be issued for, and the scopes it understands."""
        self.resources[url.rstrip("/")] = {"name": name, "scopes": scopes}

    def add_actor(self, resource: str, targets: dict[str, list[str]]) -> tuple[str, str]:
        """Register a resource server as a confidential client that may exchange the tokens
        issued TO it for tokens to `targets` (URL -> the most it may ask for there)."""
        client_id, secret = f"rs-{secrets.token_hex(4)}", secrets.token_urlsafe(32)
        self.clients[client_id] = OAuthClientInformationFull(
            client_id=client_id, client_secret=secret, redirect_uris=None,
            grant_types=[TOKEN_EXCHANGE], token_endpoint_auth_method="client_secret_post",
            client_name=self.resources[resource.rstrip("/")]["name"],
            client_id_issued_at=int(time.time()))
        self.actors[client_id] = {"resource": resource.rstrip("/"),
                                  "targets": {k.rstrip("/"): v for k, v in targets.items()}}
        return client_id, secret

    def log(self, event: str, **fields) -> None:
        self.events.append({"at": round(time.time(), 3), "event": event, **fields})

    # -- tokens --------------------------------------------------------------------------

    def jwks(self) -> dict:
        jwk = OKPAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.kid, "alg": ALGORITHM, "use": "sig"}]}

    def mint(self, sub: str, audience: str, scopes: list[str], client_id: str, ttl: int,
             **extra) -> tuple[str, dict]:
        now = int(time.time())
        claims = {"iss": self.issuer, "sub": sub, "aud": audience, "client_id": client_id,
                  "scope": " ".join(scopes), "groups": DIRECTORY.get(sub, []), "iat": now,
                  "nbf": now, "exp": now + ttl, "jti": secrets.token_hex(8), **extra}
        return jwt.encode(claims, self.key, algorithm=ALGORITHM,
                          headers={"kid": self.kid}), claims

    def _tokens(self, client_id: str, sub: str, resource: str, scopes: list[str],
                family: str, refresh_scopes: list[str]) -> OAuthToken:
        access, claims = self.mint(sub, resource, scopes, client_id, ACCESS_TTL)
        rt = RefreshToken(token=secrets.token_urlsafe(32), client_id=client_id,
                          scopes=refresh_scopes, expires_at=int(time.time()) + REFRESH_TTL,
                          resource=resource, subject=sub)
        self.refresh[rt.token] = (rt, family)
        self.log("token.issued", client_id=client_id, sub=sub, aud=resource,
                 scope=claims["scope"], jti=claims["jti"], family=family)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          scope=claims["scope"], refresh_token=rt.token)

    def revoke_family(self, family: str, why: str) -> None:
        dead = [t for t, (_, f) in self.refresh.items() if f == family]
        for t in dead:
            del self.refresh[t]
            self.rotated[t] = family
        self.log("family.revoked", family=family, why=why, refresh_tokens=len(dead))

    # -- the SDK's provider protocol -----------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        bad = [str(u) for u in client_info.redirect_uris or [] if not loopback_or_https(str(u))]
        if bad:
            self.log("register.refused", redirect_uris=bad)
            raise RegistrationError("invalid_redirect_uri",
                                    f"redirect URIs must be https or loopback: {bad}")
        # A host may ASK for any scope this server knows; whether it gets one is the
        # person's consent, bounded by their groups.
        client_info.scope = " ".join(sorted({s for r in self.resources.values()
                                             for s in r["scopes"]}))
        self.clients[client_info.client_id] = client_info
        self.log("client.registered", client_id=client_info.client_id,
                 name=client_info.client_name,
                 redirect_uris=[str(u) for u in client_info.redirect_uris or []])

    async def authorize(self, client: OAuthClientInformationFull,
                        params: AuthorizationParams) -> str:
        resource = (params.resource or "").rstrip("/")
        if not resource:
            raise AuthorizeError("invalid_request", "name the resource the token is for "
                                 "(RFC 8707 `resource`): a token without one is valid anywhere")
        if resource not in self.resources:
            self.log("authorize.refused", client_id=client.client_id, resource=resource)
            raise AuthorizeError("invalid_target", f"no resource here is called {resource}")
        scopes = params.scopes or self.resources[resource]["scopes"][:1]
        unknown = [s for s in scopes if s not in self.resources[resource]["scopes"]]
        if unknown:
            raise AuthorizeError("invalid_scope", f"{resource} has no scope {unknown}")
        request_id = secrets.token_urlsafe(16)
        self.pending[request_id] = (client, params.model_copy(update={"scopes": scopes}),
                                    time.time())
        self.log("consent.asked", client_id=client.client_id, resource=resource,
                 scope=" ".join(scopes))
        return f"{self.issuer}/consent?request={request_id}"

    async def load_authorization_code(self, client, code: str) -> AuthorizationCode | None:
        if code in self.used_codes:
            # RFC 6749 4.1.2: a code used twice was stolen, so what it bought is revoked.
            self.revoke_family(self.used_codes[code], "authorization code used twice")
            return None
        return self.codes.get(code)

    async def exchange_authorization_code(self, client, code: AuthorizationCode) -> OAuthToken:
        del self.codes[code.code]
        family = secrets.token_hex(6)
        self.used_codes[code.code] = family
        return self._tokens(client.client_id, code.subject, code.resource, code.scopes, family,
                            code.scopes)

    async def load_refresh_token(self, client, refresh_token: str) -> RefreshToken | None:
        if refresh_token in self.rotated:
            # Used already: either the host lost a race with itself, or someone else has it.
            # OAuth 2.1 says assume the worst: the whole family goes.
            self.revoke_family(self.rotated[refresh_token], "refresh token used twice")
            return None
        found = self.refresh.get(refresh_token)
        return found[0] if found else None

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        _, family = self.refresh.pop(refresh_token.token)
        self.rotated[refresh_token.token] = family
        refused = allowed(refresh_token.subject, scopes)
        if refused:               # their groups changed since they approved: ask them again
            self.revoke_family(family, f"no longer grants {refused}")
            raise TokenError("invalid_grant", f"{refresh_token.subject} may no longer "
                             f"approve {refused}")
        self.log("token.refreshed", client_id=client.client_id, sub=refresh_token.subject,
                 family=family)
        return self._tokens(client.client_id, refresh_token.subject, refresh_token.resource,
                            scopes, family, refresh_token.scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return None                     # resources verify the JWT themselves, with the JWKS

    async def revoke_token(self, token) -> None:
        if isinstance(token, RefreshToken) and token.token in self.refresh:
            self.revoke_family(self.refresh[token.token][1], "revoked by the client")

    # -- the consent page ----------------------------------------------------------------

    async def consent(self, request: Request):
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            if request_id not in self.pending:
                return HTMLResponse("<p>This request has expired.</p>", status_code=400)
            return HTMLResponse(self.consent_page(request_id))
        form = await request.form()
        request_id, user = str(form.get("request", "")), str(form.get("user", ""))
        pending = self.pending.pop(request_id, None)
        if pending is None or time.time() - pending[2] > 300:
            return HTMLResponse("<p>This request has expired.</p>", status_code=400)
        client, params, _ = pending
        back = dict(state=params.state, iss=self.issuer)
        refused = allowed(user, params.scopes) if user in DIRECTORY else params.scopes
        if form.get("decision") != "approve" or refused:
            why = (f"{user or 'nobody'} may not approve {' '.join(refused)}" if refused
                   and form.get("decision") == "approve" else f"{user} declined")
            self.log("consent.denied", client_id=client.client_id, sub=user, why=why)
            return RedirectResponse(construct_redirect_uri(
                str(params.redirect_uri), error="access_denied", error_description=why,
                **back), status_code=302)
        code = AuthorizationCode(
            code=secrets.token_urlsafe(32), scopes=params.scopes,
            expires_at=time.time() + CODE_TTL, client_id=client.client_id,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource.rstrip("/"), subject=user)
        self.codes[code.code] = code
        self.log("consent.given", client_id=client.client_id, sub=user,
                 scope=" ".join(params.scopes), resource=code.resource)
        return RedirectResponse(construct_redirect_uri(str(params.redirect_uri),
                                                       code=code.code, **back), status_code=302)

    def consent_page(self, request_id: str) -> str:
        client, params, _ = self.pending[request_id]
        e = html.escape
        resource = self.resources[params.resource.rstrip("/")]
        back = urlparse(str(params.redirect_uri))
        asked = "".join(f"<li><code>{e(s)}</code>: {e(SCOPES.get(s, ('?',))[0])}</li>"
                        for s in params.scopes)
        people = "".join(f'<option value="{e(u)}">{e(u)} ({", ".join(g)})</option>'
                         for u, g in DIRECTORY.items())
        return f"""<!doctype html><title>Approve access</title>
<h1>{e(client.client_name or "An unnamed host")} wants to act for you</h1>
<p>On <b>{e(resource["name"])}</b> (<code>{e(params.resource)}</code>), it asks to:</p>
<ul>{asked}</ul>
<p>Afterwards you go back to <code>{e(back.scheme)}://{e(back.netloc)}</code>.
Client id <code>{e(client.client_id)}</code>, registered by the host itself.</p>
<form method="post" action="{e(self.issuer)}/consent">
<input type="hidden" name="request" value="{e(request_id)}">
<label>Sign in as (the lab's stand-in for your identity provider's login)
<select name="user">{people}</select></label>
<button name="decision" value="approve">Approve</button>
<button name="decision" value="deny">Deny</button></form>"""

    # -- token exchange (RFC 8693) -------------------------------------------------------

    async def token(self, request: Request):
        form = await request.form()
        if form.get("grant_type") != TOKEN_EXCHANGE:
            return await self._sdk_token.handle(request)    # code and refresh: the SDK's
        try:
            actor = await ClientAuthenticator(self).authenticate_request(request)
        except AuthenticationError as e:
            return self._error("invalid_client", e.message, 401)
        rules = self.actors.get(actor.client_id)
        if rules is None:
            self.log("exchange.refused", client_id=actor.client_id, why="not a resource server")
            return self._error("unauthorized_client", "only a registered resource server may "
                               "exchange tokens")
        if form.get("subject_token_type") != ACCESS_TOKEN_TYPE:
            return self._error("invalid_request", f"subject_token_type must be {ACCESS_TOKEN_TYPE}")
        try:      # a token issued TO this actor, by us, in date: nothing else is exchangeable
            subject = jwt.decode(str(form.get("subject_token", "")), self.key.public_key(),
                                 algorithms=[ALGORITHM], audience=rules["resource"],
                                 issuer=self.issuer, options={"require": ["sub", "exp", "jti"]})
        except jwt.InvalidTokenError as e:
            self.log("exchange.refused", client_id=actor.client_id, why=str(e))
            return self._error("invalid_grant", f"subject token refused: {e}")
        target = str(form.get("resource") or form.get("audience") or "").rstrip("/")
        if target not in rules["targets"]:
            self.log("exchange.refused", client_id=actor.client_id, sub=subject["sub"],
                     why=f"may not act at {target}")
            return self._error("invalid_target", f"{actor.client_name} may not act at {target}")
        scopes = str(form.get("scope") or " ".join(rules["targets"][target])).split()
        over = [s for s in scopes if s not in rules["targets"][target]]
        refused = over or allowed(subject["sub"], scopes)
        if refused:
            self.log("exchange.refused", client_id=actor.client_id, sub=subject["sub"],
                     why=f"scope {refused}")
            return self._error("invalid_scope", f"not for {subject['sub']} through "
                               f"{actor.client_name}: {refused}")
        token, claims = self.mint(subject["sub"], target, scopes, actor.client_id,
                                  EXCHANGED_TTL, act={"sub": actor.client_id})
        self.log("token.exchanged", client_id=actor.client_id, sub=subject["sub"], aud=target,
                 scope=claims["scope"], jti=claims["jti"], from_jti=subject["jti"])
        return JSONResponse({"access_token": token, "issued_token_type": ACCESS_TOKEN_TYPE,
                             "token_type": "Bearer", "expires_in": EXCHANGED_TTL,
                             "scope": claims["scope"]}, headers={"Cache-Control": "no-store"})

    @staticmethod
    def _error(error: str, description: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"error": error, "error_description": description},
                            status_code=status, headers={"Cache-Control": "no-store"})

    # -- the app -------------------------------------------------------------------------

    def metadata(self) -> dict:
        i = self.issuer
        return {"issuer": i, "authorization_endpoint": f"{i}/authorize",
                "token_endpoint": f"{i}/token", "registration_endpoint": f"{i}/register",
                "revocation_endpoint": f"{i}/revoke", "jwks_uri": f"{i}/jwks.json",
                "scopes_supported": sorted(SCOPES), "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token",
                                          TOKEN_EXCHANGE],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["client_secret_post",
                                                          "client_secret_basic", "none"],
                "revocation_endpoint_auth_methods_supported": ["client_secret_post",
                                                               "client_secret_basic"],
                "authorization_response_iss_parameter_supported": True}

    def app(self) -> Starlette:
        sdk = create_auth_routes(
            self, AnyHttpUrl(self.issuer),
            client_registration_options=ClientRegistrationOptions(enabled=True,
                                                                  valid_scopes=sorted(SCOPES)),
            revocation_options=RevocationOptions(enabled=True))
        self._sdk_token = TokenHandler(self, ClientAuthenticator(self))
        mine = {"/.well-known/oauth-authorization-server", "/token"}
        return Starlette(routes=[
            Route("/.well-known/oauth-authorization-server",
                  lambda _: JSONResponse(self.metadata())),
            Route("/jwks.json", lambda _: JSONResponse(self.jwks())),
            Route("/consent", self.consent, methods=["GET", "POST"]),
            Route("/token", self.token, methods=["POST"]),
            *[r for r in sdk if r.path not in mine]])


def main() -> None:
    parser = argparse.ArgumentParser(description="E3's authorization server, alone.")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--resource", default="http://127.0.0.1:8791/mcp")
    args = parser.parse_args()
    import uvicorn
    server = AuthServer(f"http://127.0.0.1:{args.port}")
    server.add_resource(args.resource, "the E3 gateway", ["mcp:tools", "mcp:actions"])
    uvicorn.run(server.app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
