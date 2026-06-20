"""Unit 2 - An Event Vocabulary, Not Log Lines

What this shows:
- a small catalog of semantic event names (constants, not magic strings) so telemetry
  is queryable and aggregatable — the shape of personal_agent's telemetry/events.py;
- "one event name, one shape": a required-field guard so the same event always carries
  the same fields (the ambiguous-Kibana-query trap, avoided);
- separating organic (kind="user") from background (kind="system:<source>") traffic, so
  the agent's own feedback loops don't look like user activity.

Run (pure Python, no endpoint needed):
    python examples/02/event_vocabulary.py
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402

# The vocabulary: named events, grouped by subsystem. Querying "all TOOL_CALL_FAILED in
# the last hour" is only possible because the name is a shared constant, not a free string.
REQUEST_RECEIVED = "request_received"
REPLY_READY = "reply_ready"
TOOL_CALL_COMPLETED = "tool_call_completed"
TOOL_CALL_FAILED = "tool_call_failed"
GATE_BLOCKED = "gate_blocked"  # a feedback-loop event (Unit 4)

# One event, one shape: the required fields for an event are its contract. Adding a field
# here forces every emit site to provide it — the single source of truth personal_agent
# enforces with CANONICAL_MODEL_CALL_*_FIELDS frozensets.
REQUIRED_FIELDS: dict[str, set[str]] = {
    TOOL_CALL_COMPLETED: {"tool", "latency_ms"},
    TOOL_CALL_FAILED: {"tool", "error"},
}


def emit(trace: Trace, operation: str, **fields: object) -> Trace:
    """Guarded emit: reject an event whose shape violates its contract."""
    required = REQUIRED_FIELDS.get(operation, set())
    missing = required - fields.keys()
    if missing:
        raise ValueError(f"event {operation!r} missing required fields: {sorted(missing)}")
    return log_event(trace, operation, **fields)


def main() -> None:
    import io

    sink = io.StringIO()

    # Organic user turn.
    user = Trace.new()
    user = emit(user, REQUEST_RECEIVED, stream=sink)
    user = emit(user, TOOL_CALL_COMPLETED, tool="search", latency_ms=42, stream=sink)
    user = emit(user, TOOL_CALL_FAILED, tool="fetch", error="timeout", stream=sink)
    user = emit(user, REPLY_READY, stream=sink)

    # A background feedback loop runs under a SYSTEM trace — separable from user traffic.
    bg = Trace.new(kind="system:loop_monitor")
    bg = emit(bg, GATE_BLOCKED, tool="search", reason="identical_output", stream=sink)

    import json

    records = [json.loads(line) for line in sink.getvalue().splitlines()]

    # Aggregate by event name — trivial because names are a shared vocabulary.
    counts = Counter(r["operation"] for r in records)
    print("events by name:")
    for name, n in counts.most_common():
        print(f"  {n}x {name}")

    # Separate organic from background traffic by kind.
    user_only = [r for r in records if not r["kind"].startswith("system:")]
    system_only = [r for r in records if r["kind"].startswith("system:")]
    print(f"\nuser-trace events: {len(user_only)}   system-trace events: {len(system_only)}")

    # The contract catches a malformed event before it pollutes the index.
    try:
        emit(Trace.new(), TOOL_CALL_COMPLETED, tool="search", stream=sink)  # no latency_ms
    except ValueError as e:
        print(f"\nrejected bad event: {e}")


if __name__ == "__main__":
    main()
