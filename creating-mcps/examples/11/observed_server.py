"""
Unit 11: Unit 9's scaled server, observed. The same replicas, with two additions:

    telemetry   every joinable record also goes to OpenSearch (mcp-telemetry-YYYY.MM.DD), as
                the gateway's own login; telemetry.py
    audit       every tools/call is a row in audit.tool_calls, written before the result goes
                back; audit.py

The middleware order, outermost first (Unit 9 showed the list runs outer to inner):

    [the SDK's own: OpenTelemetry, request state] -> joinable_log -> audit -> identity
        -> envelope -> [cache] -> tool (with the policy inside)

The audit sits inside the log, so its own success or failure is in the record, and outside
identity, so it sees the calls identity refuses before the policy runs.

    python examples/11/observed_server.py --kind cmdb --port 8731 --workers 2

Settings are Unit 9's, plus:

    MCP_TELEMETRY=opensearch   ship records (off: stderr only, as before)
    MCP_AUDIT=1                write audit rows
    MCP_AUDIT_FAIL=closed      no row, no result (open: return it, and log the failure)

Each replica's audit pool holds up to 2 more of the gateway login's connections: count them
in MCP_PG_BUDGET's headroom.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "09"), str(EXAMPLES / "08"), str(EXAMPLES / "07"),
                str(EXAMPLES)]

os.environ.setdefault("MCP_TELEMETRY", "opensearch")
os.environ.setdefault("MCP_AUDIT", "1")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observe(gateway, writer) -> None:
    """Make gateway.build put the audit writer just inside the joinable log."""
    build = gateway.build
    if getattr(build, "observed", False):
        return

    def observed_build(kind, resource):
        mcp = build(kind, resource)
        if writer is not None:      # just inside the joinable log (the SDK's own come first)
            at = next(i for i, m in enumerate(mcp.middleware)
                      if getattr(m, "__qualname__", "").startswith("joinable_log"))
            mcp.middleware.insert(at + 1, writer)
        return mcp

    observed_build.observed = True
    gateway.build = observed_build


class FlushOnShutdown:
    """ASGI: ship the telemetry still queued when the server is told to stop. (A uvicorn
    worker process ends without running atexit handlers, so this is the last chance.)"""

    def __init__(self, app, sink):
        self.app, self.sink = app, sink

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan" or self.sink is None:
            return await self.app(scope, receive, send)

        async def watched():
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                self.sink.flush()
            return message

        return await self.app(scope, watched, send)


def create_app():
    """One replica (uvicorn calls this in each worker, after the fork)."""
    import audit
    import scaled_server
    import telemetry
    from common_mcp import load_env

    load_env()
    envelope = scaled_server._load("unit8_envelope", EXAMPLES / "08" / "envelope_server.py")
    observe(envelope.gateway, audit.from_env())
    sink = telemetry.install()
    return FlushOnShutdown(scaled_server.create_app(), sink)


def main() -> None:
    import uvicorn

    scaled_server = _load("scaled_server", EXAMPLES / "09" / "scaled_server.py")
    parser = argparse.ArgumentParser(description="Unit 11: the scaled server, observed.")
    parser.add_argument("--kind", choices=("cmdb", "siem"), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--workers", type=int, default=int(os.environ.get("MCP_WORKERS", 1)))
    args = parser.parse_args()
    os.environ.update(MCP_KIND=args.kind, MCP_HOST=args.host, MCP_PORT=str(args.port),
                      MCP_WORKERS=str(args.workers))
    os.environ.setdefault("MCP_ROUND_NOW", "60")
    if scaled_server._flag("MCP_POOL"):
        from common_mcp import load_env

        load_env()
        scaled_server.check_budget(args.workers)
    uvicorn.run("observed_server:create_app", factory=True, app_dir=str(HERE), host=args.host,
                port=args.port, workers=args.workers, log_level="warning",
                timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
