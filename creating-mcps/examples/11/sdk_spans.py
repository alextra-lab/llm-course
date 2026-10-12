"""
Unit 11: the spans the SDK already makes, made visible.

MCP's Python SDK v2 opens an OpenTelemetry span for every request it sends (CLIENT, named
"MCP send tools/call get_asset") and every request it answers (SERVER, "tools/call get_asset"),
and carries the trace across the boundary in `_meta.traceparent` (SEP-414). With only
opentelemetry-api installed, those spans are no-ops. Configure a tracer provider and they're
real. Here the provider keeps them in memory, so we can print them next to the joinable
record the server logged for the same call and check the three agree:

    one trace       the host's investigation span, the SDK's client span, its server span and
                    the joinable record all carry the same trace id
    parent          the server span's parent is the client span, and so is the record's
                    parent_span_id (the record read it from the same traceparent)
    attributes      the server span's attributes are what telemetry.to_span() writes

No estate needed: the server is a one-tool stand-in, served over HTTP in this process.

    python examples/11/sdk_spans.py
    python examples/11/sdk_spans.py --check     # exit 1 if they disagree (CI)

In production you'd export to a collector instead (opentelemetry-exporter-otlp), and point
the collector at Jaeger, Tempo, or OpenSearch's trace analytics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import uuid
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from common_mcp import RECORD_SINKS, joinable_log  # noqa: E402
from telemetry import to_span  # noqa: E402

EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


def server():
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("demo")

    @mcp.tool()
    def get_asset(hostname: str) -> dict:
        """One asset by hostname (a stand-in: no database)."""
        return {"hostname": hostname, "environment": "prod"}

    mcp.middleware.append(joinable_log("demo"))
    return mcp


def _span(s) -> dict:
    ctx, parent = s.get_span_context(), s.parent
    return {"name": s.name, "kind": s.kind.name, "trace_id": f"{ctx.trace_id:032x}",
            "span_id": f"{ctx.span_id:016x}",
            "parent_span_id": f"{parent.span_id:016x}" if parent else None,
            "attributes": dict(s.attributes or {})}


async def run() -> dict:
    import uvicorn
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    records: list = []
    RECORD_SINKS.append(records.append)
    tracer = trace.get_tracer("unit11-host")
    # A real transport (HTTP, in this process), so the trace has to cross the wire in _meta:
    # an in-memory client would hand the server its context directly.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = server().streamable_http_app(stateless_http=True, json_response=True)
    web = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(web.serve())
    while not web.started:
        await asyncio.sleep(0.05)
    try:
        async with Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp")) as client:
            # The host's own span: one investigation. Inside it, the SDK's client span picks
            # it up as parent and puts the trace in _meta; we add only the course's own keys.
            with tracer.start_as_current_span("investigate INC-0042"):
                await client.call_tool("get_asset", {"hostname": "jump-prod-02"},
                                       meta={"session_id": f"s-{uuid.uuid4().hex[:6]}",
                                             "step": 1})
    finally:
        RECORD_SINKS.remove(records.append)
        web.should_exit = True
        await serving
    spans = [_span(s) for s in EXPORTER.get_finished_spans()]
    record = next(r for r in records if r["method"] == "tools/call")
    return {"spans": spans, "record": record, "as_span": to_span(record)}


def checks(out: dict) -> list[tuple[str, bool]]:
    spans = {s["kind"]: s for s in out["spans"] if "tools/call" in s["name"]}
    host = next(s for s in out["spans"] if s["name"].startswith("investigate"))
    client, srv, rec, mine = spans["CLIENT"], spans["SERVER"], out["record"], out["as_span"]
    shared = {k: v for k, v in srv["attributes"].items() if k in mine["attributes"]}
    return [
        ("one trace: host, client, server and record",
         len({host["trace_id"], client["trace_id"], srv["trace_id"], rec["trace_id"]}) == 1),
        ("the client span's parent is the host's span",
         client["parent_span_id"] == host["span_id"]),
        ("the server span's parent is the client span",
         srv["parent_span_id"] == client["span_id"]),
        ("the record's parent_span_id is the client span", rec.get("parent_span_id")
         == client["span_id"]),
        ("to_span() names it as the SDK does", mine["name"] == srv["name"]),
        ("to_span() agrees on every attribute both set",
         all(mine["attributes"][k] == v for k, v in shared.items()) and len(shared) >= 3),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if anything disagrees")
    args = parser.parse_args()
    out = asyncio.run(run())
    for s in out["spans"]:
        print(json.dumps(s))
    print("record:", json.dumps(out["record"]))
    failed = 0
    for what, ok in checks(out):
        print(f"  [{'PASS' if ok else 'FAIL'}] {what}")
        failed += not ok
    if args.check and failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
