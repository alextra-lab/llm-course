"""
Unit 7: the CMDB and SIEM tools behind identity, least privilege and enterprise rules.

The tools are Units 3, 4 and 5's restrictive ones, imported unchanged (plus get_contact, new:
personal data, so something to protect). What changes is everything around them:

  * Who is calling. Over HTTP, every request carries a bearer token, verified before the MCP
    request is even read (identity.py); no token or a bad one is a 401. Over stdio, the one
    caller is the process: MCP_ACCESS_TOKEN, verified once at start.
  * What they see. tools/list is filtered by the policy's grants, and the `dataset` choices
    are narrowed to the logs the caller may search. A tool you aren't granted doesn't exist
    for you: calling it gets "Unknown tool", the same as a typo.
  * Who the backends see. The server logs in to Postgres and OpenSearch as the gateway
    (mcp_service), which holds nothing, and acts AS the caller's persona: SET ROLE, and
    OpenSearch impersonation (common_mcp.acting_for). Row-level security, field-level
    security and masking apply to the person, not to the server.
  * What a call may do. Every call is evaluated against policy.toml; deny, or approve (the
    user must confirm with a ticket, through elicitation); every verdict is logged.
  * Secrets. The server holds one: its own backend login. It never sees a persona's
    password, nor the issuer's private key, and never logs or forwards a token.

    python examples/07/gateway_server.py --kind cmdb --port 8707     # HTTP, many callers
    python examples/07/gateway_server.py --kind siem --port 8708
    MCP_POLICY=my_policy.toml python examples/07/gateway_server.py ...  # other rules
    MCP_ACCESS_TOKEN=... python examples/07/gateway_server.py --kind cmdb --stdio   # one caller

refusals.py drives both over HTTP as different people and shows which layer stops what.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import importlib.util
import inspect
import os
import re
import sys
from pathlib import Path
from typing import TypedDict, Union

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import (ElicitRequest, ElicitRequestFormParams, InputRequiredResult,
                       ToolAnnotations)

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "04"), str(EXAMPLES / "03"), str(EXAMPLES)]
import identity  # noqa: E402
import issuer as idp  # noqa: E402
from common_mcp import (PERSONAS, acting_for, joinable_log, load_env, log_note,  # noqa: E402
                        persona, pg_connect)
from policy import POLICY_FILE, Caller, Policy  # noqa: E402

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)
ASKED = "asked-for-a-ticket"
TICKET = r"(INC|CHG)-[0-9]{4}"             # the form asks for this; the server checks it too


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =========================================================================================
# The one new tool: personal data, so there is something worth protecting
# =========================================================================================

class Contact(TypedDict):
    hostname: str
    owner: str
    email: str
    phone: str
    team: str
    oncall_slack: str


def get_contact(hostname: str) -> Contact:
    """How to reach the person who owns a host: their e-mail and phone, and their team's
    on-call channel. Personal data: the user must confirm it with a ticket. For the owner's
    name and team only, use get_asset."""
    with pg_connect(persona()) as conn:
        row = conn.execute(
            "SELECT a.hostname, p.full_name, p.email, p.phone, t.name, t.oncall_slack "
            "FROM cmdb.assets a JOIN cmdb.people p ON p.id = a.owner_id "
            "JOIN cmdb.teams t ON t.id = p.team_id WHERE a.hostname = %s",
            (hostname,)).fetchone()
    if row is None:
        raise ToolError(f"no asset named {hostname!r}. Hostnames look like jump-prod-02; "
                        "find_assets lists them.")
    return Contact(**dict(zip(Contact.__annotations__, row)))


def tools_for(kind: str) -> tuple[dict, str]:
    """The functions this server offers, and its instructions."""
    if kind == "cmdb":
        tasks = _load("unit5_cmdb_tasks", EXAMPLES / "05" / "cmdb_tasks_server.py")
        fns = {"get_asset": tasks.unit3.get_asset, "find_assets": tasks.unit3.find_assets,
               "service_dependencies": tasks.service_dependencies,
               "hosts_with_vulnerability": tasks.hosts_with_vulnerability,
               "count_assets": tasks.count_assets, "get_contact": get_contact}
        return fns, tasks.mcp.instructions
    siem = _load("unit4_siem", EXAMPLES / "04" / "siem_server.py")
    fns = {n: getattr(siem, n) for n in ("list_incidents", "get_incident", "event_histogram",
                                         "top_values", "search_events")}
    # The Unit 4 instructions list ONE persona's datasets; here each caller's tools/list says.
    return fns, siem.mcp.instructions.split(" Log datasets you may search")[0]


# =========================================================================================
# Who is calling
# =========================================================================================

POLICY = Policy.load(Path(os.environ.get("MCP_POLICY", POLICY_FILE)))   # rules as data
_STDIO_CALLER: Caller | None = None


def current_caller() -> Caller | None:
    """The verified caller of this request (HTTP), or of this process (stdio)."""
    token = get_access_token()
    if token is None:
        return _STDIO_CALLER
    claims = token.claims or {}
    return POLICY.caller(token.subject or "", claims.get("groups"), token.client_id,
                         claims.get("jti", ""))


def host_visible(host: str) -> bool:
    """A fact for the policy, looked up AS the caller: row-level security answers it."""
    with pg_connect(persona()) as conn:
        return conn.execute("SELECT 1 FROM cmdb.assets WHERE hostname = %s",
                            (host,)).fetchone() is not None


# =========================================================================================
# The per-request identity layer (MCP middleware)
# =========================================================================================

def datasets_for(who: str) -> list[str]:
    siem = sys.modules.get("unit4_siem")
    if siem is None:
        return []
    with acting_for(who):
        return siem.mine()


async def identity_layer(ctx, call_next):
    caller = current_caller()
    if caller is None:                     # HTTP refuses these before here; stdio: no token
        raise ToolError("no caller identity")
    log_note(caller=caller.sub, groups=list(caller.groups), persona=caller.persona,
             token_id=caller.jti)
    params = ctx.params or {}

    if ctx.method == "tools/call" and not POLICY.granted(caller, params.get("name", "")):
        log_note(verdict="deny", rule="not-granted")
        # Exactly what the SDK says for a tool that doesn't exist: no hint that it does.
        return {"resultType": "complete", "isError": True, "content": [
            {"type": "text", "text": f"Unknown tool: {params.get('name')}"}]}
    if caller.persona is None:             # e.g. tools/list for a group with no grants
        result = await call_next(ctx)
    else:
        with acting_for(caller.persona):
            result = await call_next(ctx)

    if ctx.method == "tools/list" and isinstance(result, dict):
        mine = datasets_for(caller.persona) if caller.persona else []
        tools = []
        for tool in result.get("tools", []):
            if not POLICY.granted(caller, tool["name"]):
                continue
            prop = tool.get("inputSchema", {}).get("properties", {}).get("dataset")
            if prop and "enum" in prop:    # only the logs this caller may search
                tool = {**tool, "inputSchema": {**tool["inputSchema"], "properties": {
                    **tool["inputSchema"]["properties"],
                    "dataset": {**prop, "enum": [d for d in prop["enum"] if d in mine]}}}}
            tools.append(tool)
        result = {**result, "tools": tools}
        log_note(tools_visible=len(tools))
    return result


# =========================================================================================
# The policy layer, around every tool
# =========================================================================================

def with_policy(name: str, fn, annotations: ToolAnnotations):
    """The same tool, with every call evaluated against the policy first. Adds a hidden
    `ctx` parameter (for the approval round) and InputRequiredResult to the return type."""
    sig = inspect.signature(fn, eval_str=True)
    ann = annotations.model_dump(by_alias=True, exclude_none=True)

    @functools.wraps(fn)
    async def guarded(*args, ctx: Context, **kwargs):
        call = sig.bind(*args, **kwargs).arguments
        caller = current_caller()
        verdict = POLICY.evaluate(caller, name, call, ann, host_visible)
        log_note(verdict=verdict.effect, rule=verdict.rule)
        if verdict.effect == "deny":
            raise ToolError(f"refused by policy ({verdict.rule}): {verdict.message}")
        if verdict.effect == "approve":
            ticket = _ticket(ctx)
            if ticket is None:
                return _ask_for_ticket(ctx, verdict)
            log_note(approved_with=ticket)
        # The tools are synchronous; run them off the event loop (the context, and so the
        # caller's identity, goes with them).
        return await asyncio.to_thread(fn, *args, **kwargs)

    del guarded.__wrapped__                # so the SDK reads the signature below, not fn's
    ctx_param = inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context)
    returns = Union[sig.return_annotation, InputRequiredResult]
    guarded.__signature__ = sig.replace(parameters=[*sig.parameters.values(), ctx_param],
                                        return_annotation=returns)
    guarded.__annotations__ = {p.name: p.annotation for p in sig.parameters.values()} | {
        "ctx": Context, "return": returns}
    return guarded


def _ticket(ctx: Context) -> str | None:
    answer = (ctx.input_responses or {}).get("ticket")
    if answer is None or ctx.request_state != ASKED:
        return None
    if answer.action != "accept":
        log_note(approval=answer.action)
        raise ToolError("the user didn't confirm this. Don't retry it; answer without it, or "
                        "say what you couldn't do.")
    ticket = str((answer.content or {}).get("ticket", "")).strip()
    if not re.fullmatch(TICKET, ticket):
        raise ToolError("the user confirmed without a valid ticket (like INC-0042), so it "
                        "wasn't run.")
    return ticket


def _ask_for_ticket(ctx: Context, verdict) -> InputRequiredResult:
    caps = ctx.client_capabilities
    if caps is None or caps.elicitation is None:
        raise ToolError(f"this needs the user's confirmation ({verdict.rule}), and this host "
                        "can't ask them. Answer without it, or say it needs a person.")
    log_note(approval="asked")
    return InputRequiredResult(request_state=ASKED, input_requests={"ticket": ElicitRequest(
        params=ElicitRequestFormParams(
            message=verdict.message,
            requested_schema={"type": "object", "required": ["ticket"], "properties": {
                "ticket": {"type": "string", "title": "Ticket",
                           "pattern": f"^{TICKET}$",
                           "description": "The incident or change it's for, e.g. INC-0042"}}},
        ))})


# =========================================================================================
# The server
# =========================================================================================

def build(kind: str, resource: str | None) -> MCPServer:
    fns, instructions = tools_for(kind)
    options = {}
    if resource:                           # HTTP: tokens checked on every request
        options = dict(token_verifier=identity.JwtVerifier(audience=resource),
                       auth=AuthSettings(issuer_url=idp.ISSUER, resource_server_url=resource,
                                         required_scopes=["mcp:tools"],
                                         validate_token_resource=True))
    mcp = MCPServer(f"{kind}", version="0.7.0", instructions=instructions,
                    # Each caller gets a different list: never let a cache share it.
                    cache_hints={"tools/list": CacheHint(ttl_ms=60_000, scope="private")},
                    **options)
    mcp.middleware.append(joinable_log(f"{kind}_gateway"))
    mcp.middleware.append(identity_layer)
    for name, fn in fns.items():
        mcp.add_tool(with_policy(name, fn, READ_ONLY), name=name, annotations=READ_ONLY)
    return mcp


def hold_only_my_secret() -> None:
    """The server needs exactly one secret: its own backend login. Drop the rest."""
    for persona_name, var in PERSONAS.items():
        if persona_name != "mcp_service":
            os.environ.pop(var, None)
    for var in ("PG_ADMIN_PASSWORD", "OS_ADMIN_PASSWORD"):
        os.environ.pop(var, None)


def main() -> None:
    global _STDIO_CALLER
    parser = argparse.ArgumentParser(description="Unit 7's identity-aware CMDB / SIEM server.")
    parser.add_argument("--kind", choices=("cmdb", "siem"), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8707)
    parser.add_argument("--stdio", action="store_true", help="one caller: MCP_ACCESS_TOKEN")
    args = parser.parse_args()
    load_env()
    hold_only_my_secret()
    if args.stdio:
        resource = f"urn:creating-mcps:{args.kind}"
        token = os.environ.pop("MCP_ACCESS_TOKEN", "")
        try:
            claims = identity.JwtVerifier(audience=resource).check(token)
        except Exception as e:  # noqa: BLE001 -- refuse to start without a caller
            sys.exit(f"MCP_ACCESS_TOKEN is missing or invalid for {resource}: "
                     f"{type(e).__name__}")
        _STDIO_CALLER = POLICY.caller(claims["sub"], claims.get("groups"),
                                      claims.get("client_id", ""), claims["jti"])
        build(args.kind, None).run()
        return
    resource = f"http://{args.host}:{args.port}/mcp"
    build(args.kind, resource).run("streamable-http", host=args.host, port=args.port,
                                   stateless_http=True, json_response=True)


if __name__ == "__main__":
    main()
