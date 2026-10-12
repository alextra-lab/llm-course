"""
Unit 8: Unit 7's gateway server, inside an operating envelope.

Same tools, same identity, same policy. Around them, everything that keeps many callers from
exhausting finite backends:

    pools        one bounded Postgres pool and one OpenSearch client per persona, shared by
                 every caller (common_mcp.use_pools), instead of a connection per call
    limits       per-caller rate, result-token quota and concurrency, and a server-wide cap
                 (limits.py); a refusal says which limit and when to retry
    timeouts     each persona's own statement_timeout (restored: Unit 7 lost it), and a time
                 budget on every OpenSearch search that the cluster enforces
    cost gates   a log query whose window covers too many documents is refused before it's
                 sent (cost_gates.py)
    tool caps    an HTTP gate in front of MCP: at most N calls in flight per tool, decided from
                 the Mcp-Method / Mcp-Name headers alone

Every knob is an environment variable, so the load generator (loadgen.py) can start the
server with different settings:

    MCP_POOL=0                  no pools: one connection per call, as in Unit 7
    MCP_PG_POOL=10 MCP_PG_WAIT=2 MCP_OS_POOL=10 MCP_SEARCH_BUDGET=10s
    MCP_LIMITS=0                no per-caller limits
    MCP_RATE=5 MCP_BURST=10 MCP_QUOTA=50000 MCP_PER_CALLER=2 MCP_TOTAL=16 MCP_QUEUE_WAIT=1
    MCP_CURSORS=10 MCP_CURSORS_TOTAL=100 MCP_CURSOR_HOLD=120
    MCP_MAX_SCAN=5000000        documents a log query's window may cover
    MCP_TOOL_CAPS=search_events=4,event_histogram=6

    python examples/08/envelope_server.py --kind siem --port 8718
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "07"), str(EXAMPLES)]
import common_mcp  # noqa: E402
import cost_gates  # noqa: E402
from common_mcp import load_env, log_note, os_client, persona  # noqa: E402
from limits import Busy, Limiter, Settings  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway = _load("unit7_gateway", EXAMPLES / "07" / "gateway_server.py")


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).lower() not in ("0", "false", "no", "off")


# =========================================================================================
# Per-caller limits and cost gates (MCP middleware, inside Unit 7's identity layer)
# =========================================================================================

LIMITER = Limiter(Settings.from_env()) if _flag("MCP_LIMITS") else None
MAX_SCAN = int(os.environ.get("MCP_MAX_SCAN", cost_gates.OS_MAX_DOCS))


def busy_result(message: str, retry_after: int | None = None) -> dict:
    """A tool error the model reads, with the wait also in `_meta` for a host to act on."""
    out = {"resultType": "complete", "isError": True,
           "content": [{"type": "text", "text": message}]}
    if retry_after:
        out["_meta"] = {"io.creating-mcps/retry_after_s": retry_after}
    return out


async def _done(maybe) -> None:
    """Await it if it's awaitable: Unit 9's shared limiter is async, this unit's isn't."""
    if inspect.isawaitable(maybe):
        await maybe


def _opens_cursor(params: dict) -> bool:
    """A first-page search_events call: it may leave a point in time open on the cluster."""
    return params.get("name") == "search_events" and not (params.get("arguments") or {}).get(
        "cursor")


def _left_cursor(result) -> bool:
    content = result.get("content") if isinstance(result, dict) else None
    return any('"next_cursor"' in (c.get("text") or "") for c in content or [])


def _result_tokens(result) -> int:
    content = result.get("content") if isinstance(result, dict) else None
    return len(json.dumps(content or [])) // 4


async def _scan_gate(params: dict) -> str | None:
    args = params.get("arguments") or {}
    if not (args.get("dataset") in cost_gates.PREFIX and isinstance(args.get("start"), str)):
        return None
    try:
        est = await asyncio.to_thread(cost_gates.os_gate, os_client(persona()), args["dataset"],
                                      args["start"], args.get("end", "now"),
                                      args.get("tz", "UTC"), MAX_SCAN)
    except cost_gates.TooExpensive as e:
        return str(e)
    except Exception:  # noqa: BLE001 -- a bad window: the tool itself says what's wrong
        return None
    log_note(scan_docs=est["docs"])
    return None


async def envelope(ctx, call_next):
    if ctx.method != "tools/call":
        return await call_next(ctx)
    caller = gateway.current_caller()
    params = ctx.params or {}
    refused = await _scan_gate(params)
    if refused:
        log_note(gate="scan")
        return busy_result(refused)
    if LIMITER is None:
        return await call_next(ctx)
    try:
        if _opens_cursor(params):
            await _done(LIMITER.may_open_cursor(caller.sub))
        async with LIMITER.admit(caller.sub) as waited:
            log_note(queued_ms=round(waited * 1000, 1))
            result = await call_next(ctx)
    except Busy as b:
        log_note(busy=b.kind, retry_after=b.retry_after)
        return busy_result(str(b), b.retry_after)
    tokens = _result_tokens(result)
    await _done(LIMITER.charge(caller.sub, tokens))
    log_note(result_tokens=tokens)
    if _opens_cursor(params) and _left_cursor(result):
        await _done(LIMITER.opened_cursor(caller.sub))
        log_note(cursor_opened=True)
    return result


# =========================================================================================
# Per-tool caps at the HTTP layer, from the routing headers
# =========================================================================================

class ToolCaps:
    """ASGI middleware: at most caps[tool] tools/call requests in flight per tool, for everyone
    together. The decision reads only the Mcp-Method and Mcp-Name headers, so requests under
    their cap pass through without this layer reading the body. The SDK server checks that the
    headers match the body, so a caller can't dodge a cap by lying in a header.

    A refused call gets a normal tool result saying "busy", not an HTTP 429: the SDK client
    treats a 429 as a failed request (an exception, no retry), which a model never sees as
    "wait and retry". So the refusal reads the request body, only to answer its JSON-RPC id."""

    def __init__(self, app, caps: dict[str, int]):
        self.app, self.caps, self.in_flight = app, caps, defaultdict(int)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        method = headers.get(b"mcp-method", b"").decode()
        name = headers.get(b"mcp-name", b"").decode()
        cap = self.caps.get(name) if method == "tools/call" else None
        if cap is None:
            return await self.app(scope, receive, send)
        if self.in_flight[name] >= cap:
            return await self._refuse(receive, send, name, cap)
        self.in_flight[name] += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.in_flight[name] -= 1

    async def _refuse(self, receive, send, name: str, cap: int) -> None:
        raw = b""
        while True:
            message = await receive()
            raw += message.get("body", b"")
            if not message.get("more_body"):
                break
        try:
            request_id = json.loads(raw).get("id")
        except (ValueError, AttributeError):
            request_id = None
        body = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": busy_result(
            f"busy: {name} is at its limit of {cap} calls in flight for everyone. Retry the "
            "same call after 1 s; don't retry sooner.", 1)}).encode()
        print(json.dumps({"operation": "toolcaps.refused", "name": name, "cap": cap}),
              file=sys.stderr, flush=True)
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def tool_caps() -> dict[str, int]:
    raw = os.environ.get("MCP_TOOL_CAPS", "")
    return {k.strip(): int(v) for k, v in (p.split("=") for p in raw.split(",") if "=" in p)}


# =========================================================================================

def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Unit 8: the gateway in an envelope.")
    parser.add_argument("--kind", choices=("cmdb", "siem"), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8717)
    args = parser.parse_args()
    load_env()
    gateway.hold_only_my_secret()
    if _flag("MCP_POOL"):
        common_mcp.use_pools(pg_size=int(os.environ.get("MCP_PG_POOL", 10)),
                             pg_wait=float(os.environ.get("MCP_PG_WAIT", 2)),
                             os_size=int(os.environ.get("MCP_OS_POOL", 10)),
                             search_budget=os.environ.get("MCP_SEARCH_BUDGET", "10s") or None)
    resource = f"http://{args.host}:{args.port}/mcp"
    mcp = gateway.build(args.kind, resource)
    mcp.middleware.append(envelope)            # inside identity: knows the caller
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host=args.host)
    caps = tool_caps()
    print(json.dumps({"operation": "envelope.start", "kind": args.kind,
                      "pool": _flag("MCP_POOL"), "limits": LIMITER and LIMITER.settings.as_dict(),
                      "tool_caps": caps, "max_scan": MAX_SCAN}), file=sys.stderr, flush=True)
    uvicorn.run(ToolCaps(app, caps) if caps else app, host=args.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
