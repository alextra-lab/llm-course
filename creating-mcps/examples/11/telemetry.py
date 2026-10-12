"""
Unit 11: where the joinable records go once there's more than a terminal to read them.

Since Unit 1 every server has written one JSON record per request to stderr. That's enough
for one process on a laptop. With replicas (Unit 9) and an auditor who wants answers, the
records need to land somewhere shared and queryable. This module adds two things:

    to_span(record)      the same record as an OpenTelemetry span: trace and span ids, the
                         caller's span as parent, a name like "tools/call get_asset", and the
                         attributes OTel's MCP semantic conventions define (mcp.method.name,
                         gen_ai.tool.name, ...), exactly as the SDK's own span sets them.
                         (SDK v2 opens that span itself, around every request, and reads the
                         caller's `traceparent` from `_meta`; it goes nowhere until a tracer
                         provider is configured: sdk_spans.py does that)
    OpenSearchSink       ships every record to a daily index, mcp-telemetry-YYYY.MM.DD, as the
                         gateway's own login, which may write there and nowhere else. It
                         batches in a background thread and never blocks or fails a request:
                         when the queue is full it drops and counts what it dropped

    MCP_TELEMETRY=opensearch     ship records (the observed server turns it on)
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import GATEWAY, RECORD_SINKS, os_client  # noqa: E402

INDEX_PREFIX = "mcp-telemetry"


def stamp(record: dict, now: datetime | None = None) -> dict:
    """The record with when it started (`@timestamp`) and which process wrote it."""
    end = now or datetime.now(UTC)
    start = end - timedelta(milliseconds=record.get("ms") or 0)
    return {"@timestamp": start.isoformat(timespec="milliseconds"), "pid": os.getpid(),
            **record}


def to_span(record: dict) -> dict:
    """One joinable record as an OpenTelemetry span (a SERVER span: this process answered).

    The attribute names follow OTel's semantic conventions for MCP, which are at Development
    stability: expect renames, and keep the mapping in one place, like here."""
    tool = record.get("name") if record.get("method") == "tools/call" else None
    attributes = {
        "mcp.method.name": record.get("method"),
        "mcp.protocol.version": record.get("protocol_version"),
        "gen_ai.tool.name": tool,
        "gen_ai.operation.name": "execute_tool" if tool else None,
        # This course's own keys ride along as custom attributes: still joinable.
        "session.id": record.get("session_id"),
        "app.step": record.get("step"),
        "app.caller": record.get("caller"),
        "app.persona": record.get("persona"),
        "app.verdict": record.get("verdict"),
        "app.outcome": record.get("outcome"),
    }
    # error.type as the SDK's own span sets it (mcp/server/_otel.py): "tool_error" for a
    # result with isError, the JSON-RPC code for a protocol error, the exception's class for
    # a crash.
    error = {"tool_error": "tool_error", "error": str(record.get("code")),
             "crash": record.get("error")}.get(record.get("outcome"))
    if error:
        attributes["error.type"] = error
    end = datetime.fromisoformat(stamp(record)["@timestamp"]) + timedelta(
        milliseconds=record.get("ms") or 0)
    return {
        "name": f"{record.get('method')} {tool}" if tool else record.get("method"),
        "kind": "SERVER",
        "trace_id": record.get("trace_id"),
        "span_id": secrets.token_hex(8),
        "parent_span_id": record.get("parent_span_id"),
        "start": stamp(record)["@timestamp"],
        "end": end.isoformat(timespec="milliseconds"),
        "status": "ERROR" if "error.type" in attributes else "OK",
        "attributes": {k: v for k, v in attributes.items() if v is not None},
    }


class OpenSearchSink:
    """Queue records, ship them in bulk from a background thread."""

    def __init__(self, max_queue: int = 10_000, batch: int = 500, every: float = 1.0):
        self.queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self.batch, self.every = batch, every
        self.sent = self.dropped = self.failed = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="telemetry", daemon=True)
        self._thread.start()

    def __call__(self, record: dict) -> None:
        try:
            self.queue.put_nowait(stamp(record))
        except queue.Full:
            self.dropped += 1

    def _drain(self) -> list[dict]:
        out = []
        while len(out) < self.batch:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return out

    def _ship(self, docs: list[dict]) -> None:
        from opensearchpy import helpers

        actions = [{"_index": f"{INDEX_PREFIX}-{d['@timestamp'][:10].replace('-', '.')}",
                    "_source": d} for d in docs]
        try:
            ok, errors = helpers.bulk(os_client(GATEWAY), actions, raise_on_error=False)
            self.sent += ok
            if errors:
                self._failed(len(errors), str(next(iter(errors[0].values())).get("error")))
        except Exception as e:  # noqa: BLE001  the cluster is away: count it, keep serving
            self._failed(len(docs), f"{type(e).__name__}: {e}")

    def _failed(self, n: int, reason: str) -> None:
        """Count it, and say so on stderr the first time and every 1000 after: telemetry that
        fails silently looks exactly like a quiet day."""
        before, self.failed = self.failed, self.failed + n
        if before == 0 or before // 1000 != self.failed // 1000:
            print(json.dumps({"operation": "telemetry.failed", "failed": self.failed,
                              "reason": reason[:300]}), file=sys.stderr, flush=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            docs = self._drain()
            if docs:
                self._ship(docs)
            if len(docs) < self.batch:
                self._stop.wait(self.every)

    def flush(self, timeout: float = 10.0) -> None:
        """Ship what's queued now (tests, and shutdown)."""
        deadline = time.monotonic() + timeout
        while not self.queue.empty() and time.monotonic() < deadline:
            self._ship(self._drain())

    def stats(self) -> dict:
        return {"sent": self.sent, "dropped": self.dropped, "failed": self.failed,
                "queued": self.queue.qsize()}


_SINK: OpenSearchSink | None = None


def install() -> OpenSearchSink | None:
    """Turn shipping on for this process, once, if MCP_TELEMETRY=opensearch."""
    global _SINK
    if _SINK is None and os.environ.get("MCP_TELEMETRY", "off") == "opensearch":
        _SINK = OpenSearchSink()
        RECORD_SINKS.append(_SINK)
        import atexit

        atexit.register(_SINK.flush)
    return _SINK


if __name__ == "__main__":
    example = {"session_id": "s-1", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
               "parent_span_id": "00f067aa0ba902b7", "step": 3, "operation": "mcp.request",
               "server": "cmdb_gateway", "method": "tools/call", "name": "get_asset",
               "protocol_version": "2026-07-28", "caller": "priya", "persona": "l1_analyst",
               "verdict": "allow", "outcome": "ok", "ms": 12.5}
    print(json.dumps(to_span(example), indent=2))
