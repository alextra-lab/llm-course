"""
E3: the whole OAuth flow, end to end, with every guard tried -- and checked.

Three servers start on free loopback ports: the authorization server, the gateway (a
protected resource) and the ticketing API behind it. A host is given ONLY the gateway's URL.

    discovery       no token: 401 naming the resource metadata; that names the issuer; the
                    issuer's metadata names everything else
    priya           the host registers, sends her to consent with PKCE and the gateway as the
                    resource; she approves `mcp:tools`; the token works there and nowhere else
    on her behalf   ticket_status: the gateway exchanges her token for one to the ticket API
                    (`act` = the gateway); a restricted ticket stays invisible to her
    step-up         isolate_host: 403 insufficient_scope; the host asks her for `mcp:actions`;
                    her groups can't approve it, so nothing happens
    marc            the same step-up, approved: the call goes through, and the token now
                    carries both scopes
    refresh         the token runs out; the refresh token buys a new one and is rotated; the
                    old one, replayed, is refused AND revokes the family
    audiences       her gateway token at the ticket API, an exchanged token at the gateway,
                    and a host trying token exchange: all refused
    by hand         the flow without the SDK's client: a hostile redirect URI, no resource, a
                    resource the host may not ask for, a wrong PKCE verifier, a code used
                    twice, and someone with no rights at all

    python examples/E3/drill.py          # needs nothing but the course's Python packages
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import httpx  # noqa: E402
import jwt  # noqa: E402
from auth_server import ACCESS_TOKEN_TYPE, TOKEN_EXCHANGE, AuthServer  # noqa: E402
from gateway import Gateway  # noqa: E402
from ticket_api import TicketApi  # noqa: E402

REDIRECT = "http://127.0.0.1:33418/callback"      # the host's loopback listener (never opened)
for _name in ("httpx", "httpx2", "mcp.client.auth", "mcp.server.lowlevel.server",
              "mcp.server.streamable_http_manager", "mcp.server.mcpserver"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)   # the drill reports; they needn't


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Servers:
    """The authorization server, the gateway and the ticket API, each on its own port, in
    background threads of this process (so a lab can read their logs directly)."""

    def __init__(self):
        a, g, t = _free_port(), _free_port(), _free_port()
        self.issuer = f"http://127.0.0.1:{a}"
        self.resource = f"http://127.0.0.1:{g}/mcp"
        self.tickets_url = f"http://127.0.0.1:{t}"
        self.auth = AuthServer(self.issuer)
        self.auth.add_resource(self.resource, "the E3 gateway", ["mcp:tools", "mcp:actions"])
        self.gateway_client = self.auth.add_actor(self.resource,
                                                  {self.tickets_url: ["tickets:read"]})
        self.gateway = Gateway(self.issuer, self.resource, self.tickets_url,
                               *self.gateway_client)
        self.tickets = TicketApi(self.issuer, self.tickets_url)
        self._apps = [(self.auth.app(), a), (self.gateway.app(), g), (self.tickets.app(), t)]
        self._servers = []

    def __enter__(self) -> "Servers":
        import uvicorn
        for app, port in self._apps:
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                                   log_level="warning", lifespan="on"))
            threading.Thread(target=server.run, daemon=True).start()
            self._servers.append(server)
        deadline = time.monotonic() + 15
        while not all(s.started for s in self._servers):
            if time.monotonic() > deadline:
                raise RuntimeError("the E3 servers didn't start")
            time.sleep(0.05)
        return self

    def __exit__(self, *_):
        for s in self._servers:
            s.should_exit = True


class Browser:
    """The person at the browser: follows the host's link to the consent page, signs in as
    `person` and answers `decision`. Records what the page showed them."""

    def __init__(self, person: str, decision: str = "approve"):
        self.person, self.decision = person, decision
        self.pages: list[dict] = []
        self._back: dict = {}

    async def visit(self, url: str) -> dict:
        async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
            r = await http.get(url)
            if r.status_code == 302 and "/consent?" in r.headers["location"]:
                consent = r.headers["location"]
                page = (await http.get(consent)).text
                text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.split("<form")[0]))
                asked = parse_qs(urlparse(url).query)
                self.pages.append({"person": self.person, "decision": self.decision,
                                   "scope": asked.get("scope", [""])[0],
                                   "resource": asked.get("resource", [""])[0],
                                   "page": text.strip()})
                r = await http.post(consent.split("?")[0], data={
                    "request": parse_qs(urlparse(consent).query)["request"][0],
                    "user": self.person, "decision": self.decision})
            if r.status_code != 302:
                return {"error": "no_redirect", "status": r.status_code, "body": r.text[:300]}
            return {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}

    async def redirect_handler(self, url: str) -> None:       # the host opens the browser
        self._back = await self.visit(url)

    async def callback_handler(self):                          # the browser comes back
        from mcp.shared.auth import AuthorizationCodeResult
        back = self._back
        if "code" not in back:
            raise PermissionError(f"{back.get('error')}: {back.get('error_description', '')}")
        return AuthorizationCodeResult(code=back["code"], state=back.get("state"),
                                       iss=back.get("iss"))


class Storage:
    """Where the host keeps its registration and tokens (in memory: one run)."""

    def __init__(self):
        self.tokens = self.client = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, client_info):
        self.client = client_info


@contextlib.asynccontextmanager
async def connect(servers: Servers, browser: Browser, storage: Storage, wire: list,
                  name: str = "E3 lab host"):
    """An MCP client that knows only the gateway's URL; `wire` gets every HTTP exchange."""
    import httpx2
    from mcp import Client
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.auth import OAuthClientMetadata

    provider = OAuthClientProvider(
        server_url=servers.resource, storage=storage,
        client_metadata=OAuthClientMetadata(
            client_name=name, redirect_uris=[REDIRECT], token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"], response_types=["code"]),
        redirect_handler=browser.redirect_handler, callback_handler=browser.callback_handler)

    async def record(response):
        req = response.request
        wire.append({"method": req.method, "url": str(req.url).split("?")[0],
                     "mcp": req.headers.get("mcp-method", ""),
                     "status": response.status_code,
                     "www_authenticate": response.headers.get("www-authenticate", "")})

    http = httpx2.AsyncClient(auth=provider, timeout=30, event_hooks={"response": [record]})
    async with http, Client(streamable_http_client(servers.resource, http_client=http)) as client:
        yield client, provider


def _root(e: BaseException) -> str:
    while getattr(e, "exceptions", None) or e.__cause__:
        e = e.exceptions[0] if getattr(e, "exceptions", None) else e.__cause__
    return f"{type(e).__name__}: {e}"


async def call(client, tool: str, args: dict | None = None) -> dict:
    """A tool call, its outcome whatever it is."""
    try:
        r = await client.call_tool(tool, args or {})
    except Exception as e:  # noqa: BLE001 -- a refused authorization surfaces as an exception
        return {"tool": tool, "args": args or {}, "error": True, "data": None, "text": _root(e)}
    text = "\n".join(c.text for c in r.content if getattr(c, "text", None))
    return {"tool": tool, "args": args or {}, "error": r.is_error, "data": r.structured_content,
            "text": text}


# =========================================================================================
# The scenes
# =========================================================================================

async def discovery(s: Servers) -> dict:
    """What a host can learn with no token at all, in the order it learns it."""
    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        r = await http.post(s.resource, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                            headers={"accept": "application/json, text/event-stream"})
        challenge = r.headers.get("www-authenticate", "")
        prm_url = re.search(r'resource_metadata="([^"]+)"', challenge).group(1)
        prm = (await http.get(prm_url)).json()
        asm_url = f"{prm['authorization_servers'][0].rstrip('/')}/.well-known/oauth-authorization-server"
        asm = (await http.get(asm_url)).json()
        jwks = (await http.get(asm["jwks_uri"])).json()
    return {"status": r.status_code, "www_authenticate": challenge, "prm_url": prm_url,
            "prm": prm, "asm_url": asm_url, "asm": asm, "jwks": jwks}


async def person(s: Servers, who: str, decision: str = "approve") -> dict:
    """`who` connects through a fresh host, reads, then tries the step-up action."""
    browser, storage, wire = Browser(who, decision), Storage(), []
    calls, stages, first = [], {}, None
    isolate = {"hostname": "db-prod-01", "ticket": "CHG-0107"}
    try:
        async with connect(s, browser, storage, wire) as (client, provider):
            stages["connected"] = len(wire)
            calls.append(await call(client, "whoami"))
            first = storage.tokens
            calls.append(await call(client, "ticket_status", {"ticket": "INC-0042"}))
            calls.append(await call(client, "ticket_status", {"ticket": "INC-0043"}))
            stages["before_step_up"] = len(wire)
            calls.append(await call(client, "isolate_host", isolate))
            if not calls[-1]["error"]:
                calls.append(await call(client, "whoami"))
    except Exception as e:  # noqa: BLE001 -- a refused step-up ends the host's connection
        if len(calls) < 4:
            calls.append({"tool": "isolate_host", "args": isolate, "error": True,
                          "data": None, "text": _root(e)})
        else:
            calls[3] = {**calls[3], "error": True, "text": _root(e)}
    return {"person": who, "calls": calls, "wire": wire, "stages": stages,
            "consent": browser.pages, "first_token": first.access_token if first else None,
            "client_id": storage.client.client_id if storage.client else None}


async def refresh(s: Servers) -> dict:
    """marc's token runs out: refresh, rotate, and a replay of the old refresh token."""
    browser, storage, wire = Browser("marc"), Storage(), []
    async with connect(s, browser, storage, wire) as (client, provider):
        before = await call(client, "whoami")
        old = storage.tokens
        provider.context.token_expiry_time = time.time() - 1     # as if 5 minutes passed
        after = await call(client, "whoami")
        new = storage.tokens
    form = {"grant_type": "refresh_token", "client_id": storage.client.client_id}
    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        replay = await http.post(f"{s.issuer}/token", data={**form,
                                                             "refresh_token": old.refresh_token})
        current = await http.post(f"{s.issuer}/token", data={**form,
                                                              "refresh_token": new.refresh_token})
    return {"before": before, "after": after, "pages": len(browser.pages),
            "rotated": old.refresh_token != new.refresh_token,
            "replay": replay.json(), "current_after_replay": current.json(),
            "wire": wire}


async def audiences(s: Servers, gateway_token: str, host_client_id: str) -> dict:
    """Every token tried where it was not issued for."""
    out = {}
    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        r = await http.get(f"{s.tickets_url}/tickets/INC-0042",
                           headers={"Authorization": f"Bearer {gateway_token}"})
        out["gateway_token_at_ticket_api"] = {"status": r.status_code, "body": r.json()}
        cid, secret = s.gateway_client
        ex = (await http.post(f"{s.issuer}/token", data={
            "grant_type": TOKEN_EXCHANGE, "client_id": cid, "client_secret": secret,
            "subject_token": gateway_token, "subject_token_type": ACCESS_TOKEN_TYPE,
            "resource": s.tickets_url, "scope": "tickets:read"})).json()
        r = await http.post(s.resource, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                            headers={"Authorization": f"Bearer {ex['access_token']}",
                                     "accept": "application/json, text/event-stream"})
        out["exchanged_token_at_gateway"] = {"status": r.status_code,
                                             "www_authenticate": r.headers.get("www-authenticate")}
        r = await http.post(f"{s.issuer}/token", data={
            "grant_type": TOKEN_EXCHANGE, "client_id": host_client_id,
            "subject_token": gateway_token, "subject_token_type": ACCESS_TOKEN_TYPE,
            "resource": s.tickets_url})
        out["host_tries_exchange"] = {"status": r.status_code, "body": r.json()}
        r = await http.post(f"{s.issuer}/token", data={
            "grant_type": TOKEN_EXCHANGE, "client_id": cid, "client_secret": secret,
            "subject_token": gateway_token, "subject_token_type": ACCESS_TOKEN_TYPE,
            "resource": "http://127.0.0.1:1/elsewhere"})
        out["gateway_exchanges_elsewhere"] = {"status": r.status_code, "body": r.json()}
    out["exchanged_claims"] = jwt.decode(ex["access_token"], options={"verify_signature": False})
    return out


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.decode().rstrip("=")


async def by_hand(s: Servers) -> list[dict]:
    """The flow as plain HTTP, every guard tried once. Each step: what was sent, what came back."""
    steps: list[dict] = []

    def step(what: str, r: httpx.Response | dict, expect: str) -> dict:
        body = r if isinstance(r, dict) else (r.json() if r.headers.get(
            "content-type", "").startswith("application/json") else {"text": r.text[:200]})
        status = None if isinstance(r, dict) else r.status_code
        row = {"step": what, "status": status, "got": body, "expect": expect}
        steps.append(row)
        return body

    async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
        step("register with a redirect URI on someone else's site",
             await http.post(f"{s.issuer}/register", json={
                 "client_name": "not a host", "redirect_uris": ["http://evil.example/cb"],
                 "token_endpoint_auth_method": "none"}), "invalid_redirect_uri")
        reg = step("register a host (loopback redirect, public client)",
                   await http.post(f"{s.issuer}/register", json={
                       "client_name": "by hand", "redirect_uris": [REDIRECT],
                       "token_endpoint_auth_method": "none",
                       "grant_types": ["authorization_code", "refresh_token"]}), "client_id")
        cid = reg["client_id"]
        verifier, challenge = _pkce()
        base = {"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT,
                "code_challenge": challenge, "code_challenge_method": "S256",
                "state": "xyz", "scope": "mcp:tools"}

        async def authorize(who: str, **params) -> dict:
            return await Browser(who).visit(f"{s.issuer}/authorize?{urlencode({**base, **params})}")

        step("authorize without naming a resource", await authorize("priya"), "invalid_request")
        step("authorize for the ticket API directly", await authorize(
            "priya", resource=s.tickets_url), "invalid_target")
        step("authorize as sam (contractors)", await authorize("sam", resource=s.resource),
             "access_denied")
        back = step("authorize as priya, for the gateway", await authorize(
            "priya", resource=s.resource), "code")
        token = {"grant_type": "authorization_code", "client_id": cid, "code": back.get("code"),
                 "redirect_uri": REDIRECT}
        step("redeem the code with the wrong PKCE verifier", await http.post(
            f"{s.issuer}/token", data={**token, "code_verifier": _pkce()[0]}), "invalid_grant")
        got = step("redeem it with the right one", await http.post(
            f"{s.issuer}/token", data={**token, "code_verifier": verifier}), "access_token")
        step("redeem the same code again", await http.post(
            f"{s.issuer}/token", data={**token, "code_verifier": verifier}), "invalid_grant")
        step("use the refresh token that code bought", await http.post(
            f"{s.issuer}/token", data={"grant_type": "refresh_token", "client_id": cid,
                                       "refresh_token": got.get("refresh_token", "")}),
             "invalid_grant")
    return steps


async def run_all() -> dict:
    with Servers() as s:
        found = await discovery(s)
        priya = await person(s, "priya")
        marc = await person(s, "marc")
        fresh = await refresh(s)
        aud = await audiences(s, priya["first_token"], priya["client_id"])
        hand = await by_hand(s)
        return {"discovery": found, "priya": priya, "marc": marc, "refresh": fresh,
                "audiences": aud, "by_hand": hand, "events": list(s.auth.events),
                "ticket_log": list(s.tickets.log), "isolations": list(s.gateway.isolations),
                "resource": s.resource, "issuer": s.issuer, "tickets_url": s.tickets_url}


# =========================================================================================
# The checks
# =========================================================================================

def check(run: dict) -> list[tuple[str, bool, str]]:
    out = []

    def row(name, ok, detail):
        out.append((name, bool(ok), detail))

    d = run["discovery"]
    row("no token: 401 naming the resource metadata",
        d["status"] == 401 and d["prm_url"].endswith("/.well-known/oauth-protected-resource/mcp"),
        d["www_authenticate"][:90])
    row("the metadata names this resource and its issuer",
        d["prm"]["resource"].rstrip("/") == run["resource"]
        and d["prm"]["authorization_servers"][0].rstrip("/") == run["issuer"],
        f"{d['prm']['resource']} <- {d['prm']['authorization_servers']}")
    a = d["asm"]
    row("the issuer's metadata: PKCE S256, registration, token exchange, iss in responses",
        a["issuer"] == run["issuer"] and a["code_challenge_methods_supported"] == ["S256"]
        and a.get("registration_endpoint") and TOKEN_EXCHANGE in a["grant_types_supported"]
        and a["authorization_response_iss_parameter_supported"] and d["jwks"]["keys"],
        f"{len(a['grant_types_supported'])} grants, {len(d['jwks']['keys'])} key(s)")

    p = run["priya"]
    who = p["calls"][0]
    row("priya: one consent for mcp:tools, at the gateway only",
        not who["error"] and who["data"]["sub"] == "priya" and who["data"]["scopes"] == ["mcp:tools"]
        and who["data"]["aud"] == run["resource"] and len(p["consent"]) >= 1
        and p["consent"][0]["scope"] == "mcp:tools",
        f"{who['data']['sub']} {who['data']['scopes']} aud {who['data']['aud']}"
        if not who["error"] else who["text"])
    hops = [f"{w['method']} {urlparse(w['url']).path} {w['status']}"
            for w in p["wire"][:p["stages"]["connected"]]]
    paths = " ".join(hops)
    row("the host found everything from one URL",
        all(k in paths for k in ("oauth-protected-resource", "oauth-authorization-server",
                                 "/register", "/token")) and hops[0].endswith("401"),
        f"{len(hops)} requests before the first answer")
    t = p["calls"][1]
    ex = (t["data"] or {}).get("exchanged", {})
    row("on her behalf: an exchanged token, for the ticket API, saying who acts",
        not t["error"] and t["data"]["ticket"]["read_for"] == "priya"
        and ex.get("aud") == run["tickets_url"] and ex.get("act", {}).get("sub", "").startswith("rs-")
        and ex.get("scope") == "tickets:read" and ex.get("expires_in", 999) <= 120,
        f"aud {ex.get('aud')}, act {ex.get('act')}, {ex.get('expires_in')}s" if not t["error"]
        else t["text"][:100])
    row("a restricted ticket: not there, for her", p["calls"][2]["error"],
        p["calls"][2]["text"][:80])
    up = p["calls"][3]
    asked = [w for w in p["wire"][p["stages"]["before_step_up"]:] if w["status"] == 403]
    row("step-up: 403 insufficient_scope, asked for mcp:actions",
        asked and 'error="insufficient_scope"' in asked[0]["www_authenticate"]
        and "mcp:actions" in asked[0]["www_authenticate"]
        and p["consent"][-1]["scope"].split() == ["mcp:tools", "mcp:actions"],
        asked[0]["www_authenticate"][:90] if asked else "no 403")
    row("priya can't approve it: refused, nothing isolated",
        up["error"] and "access_denied" in up["text"]
        and not [i for i in run["isolations"] if i["requested_by"] == "priya"], up["text"][:100])

    m = run["marc"]
    done = m["calls"][3]
    row("marc approves the step-up: isolated, as marc",
        not done["error"] and done["data"]["requested_by"] == "marc"
        and m["calls"][4]["data"]["scopes"] == ["mcp:tools", "mcp:actions"],
        f"scopes now {m['calls'][4]['data']['scopes']}" if len(m["calls"]) > 4 else done["text"])
    row("each consent named the host's own redirect and the resource",
        all(c["resource"] == run["resource"] and "127.0.0.1:33418" in c["page"]
            for c in p["consent"] + m["consent"]), f"{len(p['consent'] + m['consent'])} pages")

    f = run["refresh"]
    row("refresh: a new token, no new consent, the refresh token rotated",
        not f["after"]["error"] and f["after"]["data"]["jti"] != f["before"]["data"]["jti"]
        and f["pages"] == 1 and f["rotated"],
        f"{f['before']['data']['jti']} -> {f['after']['data']['jti']}")
    row("the old refresh token replayed: refused, and the family revoked",
        f["replay"].get("error") == "invalid_grant"
        and f["current_after_replay"].get("error") == "invalid_grant"
        and any(e["event"] == "family.revoked" and "twice" in e["why"] for e in run["events"]),
        f"replay {f['replay'].get('error')}, then current {f['current_after_replay'].get('error')}")

    au = run["audiences"]
    row("her gateway token at the ticket API: refused (wrong audience)",
        au["gateway_token_at_ticket_api"]["status"] == 401,
        au["gateway_token_at_ticket_api"]["body"].get("error_description", "")[:80])
    row("an exchanged token at the gateway: refused",
        au["exchanged_token_at_gateway"]["status"] == 401, "401")
    row("a host can't exchange tokens; the gateway can't act elsewhere",
        au["host_tries_exchange"]["body"].get("error") == "unauthorized_client"
        and au["gateway_exchanges_elsewhere"]["body"].get("error") == "invalid_target",
        f"{au['host_tries_exchange']['body'].get('error')}, "
        f"{au['gateway_exchanges_elsewhere']['body'].get('error')}")
    row("the ticket API saw a person and an actor, every time",
        run["ticket_log"] and all(e.get("act") for e in run["ticket_log"] if e["outcome"] in
                                  ("ok", "not_found")),
        f"{len(run['ticket_log'])} requests")

    for s in run["by_hand"]:
        got = s["got"]
        ok = (s["expect"] in got) if s["expect"] in ("client_id", "access_token", "code") else (
            got.get("error") == s["expect"])
        row(f"by hand: {s['step']}", ok, got.get("error_description") or got.get("error")
            or s["expect"])
    tokens = [e for e in run["events"] if e["event"] == "token.issued"]
    row("no token in the audit log, only its jti", tokens and not any(
        "eyJ" in json.dumps(e) for e in run["events"]), f"{len(run['events'])} events")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="E3: the OAuth flow, end to end.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    run = asyncio.run(run_all())
    rows = check(run)
    if args.json:
        print(json.dumps({"run": run, "checks": rows}, indent=1, default=str))
    else:
        for name, ok, detail in rows:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    sys.exit(0 if all(ok for _, ok, _ in rows) else 1)


if __name__ == "__main__":
    main()
