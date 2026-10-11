"""
Unit 7: four layers of refusal -- the same questions, asked as different people.

Starts both gateway servers over HTTP (on free ports), issues each person a token per server,
and asks. For every probe it reports what came back and which layer decided:

    0  the door      no token, a token for another server, an expired or forged one: 401
    1  tool list     a tool you aren't granted isn't listed and "doesn't exist"; the dataset
                     choices are only the logs you may search
    2  rows/indexes  Postgres row-level security, OpenSearch index permissions
    3  fields        column privileges, field-level security, masking
    4  policy        the rules in policy.toml: deny, or ask the user for a ticket

Then it proves the layers are independent: with the layer above taken away (the call made
straight to the backend, as the same person), the layer below still holds -- except where
only the policy stands, which is exactly why it's there.

    python examples/07/refusals.py              # all of it; exits 1 if any probe surprises
    python examples/07/refusals.py --keep       # leave the servers running, print their URLs
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

import httpx
import jwt

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "05"), str(HERE.parent)]
import issuer as idp  # noqa: E402
from common_mcp import acting_for, load_env, os_client, pg_connect  # noqa: E402

RESTRICTED_HOST = "db-prod-01"     # classification 'restricted': RLS hides it from L1
HOST = "jump-prod-02"              # an ordinary production host (INC-0042 is about it)
PERSON = "svc_backup"              # the account INC-0042 is about


# =========================================================================================
# Run the servers
# =========================================================================================

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def servers(log_dir: Path | None = None, policy: Path | None = None,
                  script: Path | None = None, env: dict | None = None):
    """Both gateway servers over HTTP. Yields {"cmdb": url, "siem": url, "logs": {kind: path}}.
    `policy` runs them with another policy file (default: policy.toml); `script` and `env`
    run another server (Unit 8's envelope_server.py) with extra settings."""
    idp.keys()
    env = {**os.environ, **(env or {}), **({"MCP_POLICY": str(policy)} if policy else {})}
    log_dir = Path(log_dir or tempfile.mkdtemp(prefix="unit7-"))
    log_dir.mkdir(parents=True, exist_ok=True)
    procs, out = [], {"logs": {}}
    try:
        for kind in ("cmdb", "siem"):
            port = _free_port()
            log = log_dir / f"{kind}.jsonl"
            procs.append(subprocess.Popen(
                [sys.executable, str(script or HERE / "gateway_server.py"), "--kind", kind,
                 "--port", str(port)],
                stdout=subprocess.DEVNULL, stderr=log.open("w"), env=env))
            out[kind], out["logs"][kind] = f"http://127.0.0.1:{port}/mcp", log
        deadline = time.time() + 30
        async with httpx.AsyncClient() as http:
            for kind in ("cmdb", "siem"):
                base = out[kind].removesuffix("/mcp")
                while True:
                    try:
                        r = await http.get(f"{base}/.well-known/oauth-protected-resource/mcp")
                        if r.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.time() > deadline or any(p.poll() is not None for p in procs):
                        raise RuntimeError(f"the {kind} server didn't start; see "
                                           f"{out['logs'][kind]}")
                    await asyncio.sleep(0.2)
        yield out
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=5)


def connect(url: str, token: str | None, on_elicit=None):
    """An SDK client that sends `token` as a bearer token on every request."""
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return Client(streamable_http_client(url, http_client=create_mcp_http_client(headers)),
                  elicitation_callback=on_elicit)


# =========================================================================================
# Layer 0: the door
# =========================================================================================

def _forged(sub: str, audience: str) -> str:
    """A token with the right claims, signed with a key that isn't the issuer's."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    now = int(time.time())
    return jwt.encode({"iss": idp.ISSUER, "sub": sub, "groups": ["soc-l3"], "aud": audience,
                       "iat": now, "exp": now + 600, "jti": "forged", "scope": "mcp:tools"},
                      Ed25519PrivateKey.generate(), algorithm="EdDSA")


def _unsigned(sub: str, audience: str) -> str:
    now = int(time.time())
    return jwt.encode({"iss": idp.ISSUER, "sub": sub, "groups": ["soc-l3"], "aud": audience,
                       "iat": now, "exp": now + 600, "jti": "unsigned", "scope": "mcp:tools"},
                      None, algorithm="none")


async def door(urls: dict) -> list[dict]:
    cmdb, siem = urls["cmdb"], urls["siem"]
    attempts = [
        ("no token", None, "401"),
        ("priya's token for the SIEM server, replayed here", idp.issue("priya", siem), "401"),
        ("priya's token, expired", idp.issue("priya", cmdb, ttl=-60), "401"),
        ("a token signed with someone else's key", _forged("priya", cmdb), "401"),
        ("an unsigned token (alg: none)", _unsigned("priya", cmdb), "401"),
        ("sam's valid token (contractors: no grants)", idp.issue("sam", cmdb), "no tools"),
        ("priya's valid token", idp.issue("priya", cmdb), "tools"),
    ]
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "door-probe", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {}}}}
    rows = []
    async with httpx.AsyncClient() as http:
        for label, token, expect in attempts:
            headers = {"Accept": "application/json, text/event-stream",
                       "Content-Type": "application/json", "Mcp-Method": "tools/list"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            r = await http.post(cmdb, json=body, headers=headers)
            if r.status_code == 401:
                got = "401"
                says = r.headers.get("www-authenticate", "")[:110]
            else:
                tools = r.json().get("result", {}).get("tools", [])
                got = "tools" if tools else "no tools"
                says = f"{len(tools)} tools"
            rows.append({"layer": "0 door", "who": label, "expect": expect, "got": got,
                         "says": says, "ok": got == expect})
    return rows


# =========================================================================================
# Layers 1-4: the same questions, as priya (L1) and marc (L3)
# =========================================================================================

def probes(day) -> list[dict]:
    """Each: layer, who, server, tool, args, expect (allowed | refused | hidden | asked:accept |
    asked:decline), and `says`: a fragment the answer must contain."""
    window = {"start": f"{day}T00:00:00Z", "end": f"{day + timedelta(days=1)}T00:00:00Z"}
    weeks = {"start": "now-21d"}
    return [
        # 1: the tool list
        dict(layer="1 tool list", who="priya", server="cmdb", tool="(tools/list)", args={},
             expect="hidden", says="get_contact"),
        dict(layer="1 tool list", who="priya", server="cmdb", tool="get_contact",
             args={"hostname": HOST}, expect="refused", says="Unknown tool"),
        dict(layer="1 tool list", who="priya", server="siem", tool="(dataset choices)", args={},
             expect="hidden", says="edr"),
        # 2: rows and indexes
        dict(layer="2 rows/indexes", who="priya", server="cmdb", tool="get_asset",
             args={"hostname": RESTRICTED_HOST}, expect="refused", says="no asset named"),
        dict(layer="2 rows/indexes", who="marc", server="cmdb", tool="get_asset",
             args={"hostname": RESTRICTED_HOST}, expect="allowed", says="restricted"),
        dict(layer="2 rows/indexes", who="priya", server="cmdb", tool="count_assets",
             args={"classification": "restricted"}, expect="allowed", says='"total": 0'),
        dict(layer="2 rows/indexes", who="priya", server="siem", tool="search_events",
             args={"dataset": "edr", **window, "limit": 1}, expect="refused",
             says="aren't available to your role"),
        # 3: fields
        dict(layer="3 fields", who="priya", server="siem", tool="search_events",
             args={"dataset": "web", **window, "limit": 1,
                   "fields": ["@timestamp", "user.name", "user.email"]},
             expect="refused", says="user.email"),
        dict(layer="3 fields", who="marc", server="siem", tool="search_events",
             args={"dataset": "web", **window, "limit": 1,
                   "filters": {"user.name": "nina.h"},
                   "fields": ["@timestamp", "user.name", "user.email"]},
             expect="asked:accept", says='"user.email"'),
        # 4: the policy
        dict(layer="4 policy", who="priya", server="siem", tool="event_histogram",
             args={"dataset": "syslog", **weeks, "filters": {"host.name": RESTRICTED_HOST}},
             expect="refused", says="restricted-hosts-stay-restricted"),
        dict(layer="4 policy", who="marc", server="siem", tool="event_histogram",
             args={"dataset": "syslog", **weeks, "filters": {"host.name": RESTRICTED_HOST}},
             expect="allowed", says='"total"'),
        dict(layer="4 policy", who="priya", server="siem", tool="top_values",
             args={"dataset": "auth", "field": "source.ip", **weeks,
                   "filters": {"user.name": PERSON}},
             expect="refused", says="no-person-lookups-at-l1"),
        dict(layer="4 policy", who="marc", server="siem", tool="top_values",
             args={"dataset": "auth", "field": "source.ip", **weeks,
                   "filters": {"user.name": PERSON}},
             expect="asked:accept", says="203.0.113.77"),
        dict(layer="4 policy", who="marc", server="cmdb", tool="get_contact",
             args={"hostname": HOST}, expect="asked:decline", says="didn't confirm"),
        dict(layer="4 policy", who="marc", server="cmdb", tool="get_contact",
             args={"hostname": HOST}, expect="asked:accept", says="@example.com"),
    ]


async def call_as(url: str, who: str, tool: str, args: dict, answer: str = "accept",
                  ticket: str = "INC-0042") -> tuple[str, str]:
    """One call as `who`. If the server asks for a ticket, `answer` it (accept | decline).
    Returns (got, what came back): got is allowed | refused | asked:accept | asked:decline."""
    from mcp.types import ElicitResult

    asked = []

    async def on_elicit(_ctx, params):
        asked.append(params.message)
        if answer == "accept":
            return ElicitResult(action="accept", content={"ticket": ticket})
        return ElicitResult(action="decline")

    async with connect(url, idp.issue(who, url), on_elicit) as client:
        r = await client.call_tool(tool, args)
    says = json.dumps(r.structured_content) if r.structured_content else "\n".join(
        c.text for c in r.content if getattr(c, "text", None))
    got = f"asked:{answer}" if asked else ("refused" if r.is_error else "allowed")
    return got, says


async def listed(url: str, who: str) -> list:
    async with connect(url, idp.issue(who, url)) as client:
        return (await client.list_tools()).tools


async def ask(urls: dict, day, ticket: str = "INC-0042") -> list[dict]:
    rows = []
    for p in probes(day):
        url = urls[p["server"]]
        if p["tool"] == "(tools/list)":
            names = [t.name for t in await listed(url, p["who"])]
            got, says = ("hidden" if p["says"] not in names else "allowed"), ", ".join(names)
        elif p["tool"] == "(dataset choices)":
            tool = next(t for t in await listed(url, p["who"]) if t.name == "search_events")
            choices = tool.input_schema["properties"]["dataset"]["enum"]
            got, says = ("hidden" if p["says"] not in choices else "allowed"), ", ".join(choices)
        else:
            answer = p["expect"].removeprefix("asked:") if ":" in p["expect"] else "decline"
            got, says = await call_as(url, p["who"], p["tool"], p["args"], answer, ticket)
        ok = got == p["expect"] and (p["tool"].startswith("(") or p["says"] in says)
        rows.append({"layer": p["layer"], "who": p["who"], "call": _call(p), "expect":
                     p["expect"], "got": got, "says": _short(says), "ok": ok})
    return rows


def _call(p: dict) -> str:
    if p["tool"].startswith("("):
        return f"{p['server']} {p['tool']}"
    args = {k: v for k, v in p["args"].items() if k not in ("start", "end", "limit")}
    return f"{p['tool']}({json.dumps(args)[1:-1]})"


def _short(text: str, n: int = 140) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


# =========================================================================================
# The layers are independent: take one away, the next still holds
# =========================================================================================

def bypass(day) -> list[dict]:
    """Straight to the backends, as the same person, through the same gateway login: what
    the server's own checks were standing in front of."""
    rows = []

    def row(layer, what, result, holds):
        rows.append({"without": layer, "tried": what, "backend says": _short(result, 110),
                     "next layer holds": holds})

    with acting_for("l1_analyst"):
        try:
            with pg_connect("l1_analyst") as conn:
                conn.execute("SELECT p.email FROM cmdb.people p LIMIT 1").fetchone()
            row("tool grant", "get_contact's SQL as priya", "returned e-mail", False)
        except Exception as e:  # noqa: BLE001
            row("tool grant", "get_contact's SQL as priya", f"{type(e).__name__}: {e}", True)
        try:
            os_client("l1_analyst").count(index="edr-events-*")
            row("dataset check", "count edr-events-* as priya", "counted", False)
        except Exception as e:  # noqa: BLE001
            row("dataset check", "count edr-events-* as priya", f"{type(e).__name__}", True)
        hit = os_client("l1_analyst").search(index="logs-web-*", body={
            "size": 1, "query": {"exists": {"field": "user.name"}}})["hits"]["hits"]
        user = hit[0]["_source"].get("user", {}) if hit else {}
        row("field check", "read a web log's user.* as priya", json.dumps(user),
            "email" not in user)
        n = os_client("l1_analyst").count(index="logs-*", body={"query": {"term": {
            "host.name": RESTRICTED_HOST}}})["count"]
        row("policy", f"count {RESTRICTED_HOST}'s logs as priya",
            f"{n} documents: OpenSearch has no idea the host is restricted", n == 0)
    with acting_for("l3_responder"):
        hit = os_client("l3_responder").search(index="logs-web-*", body={
            "size": 1, "query": {"exists": {"field": "user.name"}}})["hits"]["hits"]
        user = hit[0]["_source"].get("user", {}) if hit else {}
        row("field check", "read a web log's user.* as marc", json.dumps(user),
            "@" not in json.dumps(user))
    return rows


# =========================================================================================

def audit(log_paths: dict) -> list[dict]:
    """Every tools/call verdict both servers logged: allow and deny alike."""
    out = []
    for kind, path in log_paths.items():
        for line in Path(path).read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("operation") == "auth.refused":
                out.append({"server": kind, "caller": "-", "tool": "-", "verdict": "401",
                            "rule": r.get("reason"), "outcome": "-", "ticket": "",
                            "token_id": ""})
            elif r.get("method") == "tools/call":
                out.append({"server": kind, "caller": r.get("caller"), "tool": r.get("name"),
                            "verdict": r.get("verdict", "-"), "rule": r.get("rule", ""),
                            "outcome": r.get("outcome"), "ticket": r.get("approved_with", ""),
                            "token_id": r.get("token_id", "")})
    return out


async def run_all(log_dir: Path | None = None, policy: Path | None = None) -> dict:
    load_env()
    with acting_for("l3_responder"):
        from grid import incident_day  # Unit 5
        day = incident_day(os_client("l3_responder"))
    async with servers(log_dir, policy) as urls:
        result = {"door": await door(urls), "probes": await ask(urls, day)}
        await asyncio.sleep(0.3)               # let the servers flush their logs
        result["audit"] = audit(urls["logs"])
        tokens_logged = [p for p in urls["logs"].values()
                         if "eyJ" in Path(p).read_text()]
        result["tokens_in_logs"] = len(tokens_logged)
    result["bypass"] = bypass(day)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Four layers of refusal, probed.")
    parser.add_argument("--keep", action="store_true", help="start the servers and wait")
    args = parser.parse_args()
    if args.keep:
        async def keep():
            async with servers() as urls:
                print(json.dumps({k: str(v) for k, v in urls.items()}, indent=1))
                print("Ctrl-C to stop. Tokens: python examples/07/issuer.py issue priya <url>")
                await asyncio.Event().wait()
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(keep())
        return

    result = asyncio.run(run_all())
    bad = 0
    for section in ("door", "probes"):
        print(f"\n{section}")
        for r in result[section]:
            bad += not r["ok"]
            print(f"  {'ok ' if r['ok'] else 'BAD'} {r['layer']:<15} "
                  f"{r.get('call', r['who']):<62.62} {r['got']:<14} {r['says'][:70]}")
            if section == "probes" and not r["ok"]:
                print(f"      expected {r['expect']}")
    print("\nwithout the layer above")
    for r in result["bypass"]:
        bad += not r["next layer holds"] and r["without"] != "policy"
        print(f"  {r['without']:<15} {r['tried']:<38} -> {r['backend says']}")
    print(f"\naudit log: {len(result['audit'])} verdicts recorded "
          f"({sum(r['verdict'] == 'deny' for r in result['audit'])} deny, "
          f"{sum(r['verdict'] == '401' for r in result['audit'])} at the door); "
          f"tokens found in the logs: {result['tokens_in_logs']}")
    bad += result["tokens_in_logs"]
    if bad:
        raise SystemExit(f"{bad} surprise(s)")


if __name__ == "__main__":
    main()
