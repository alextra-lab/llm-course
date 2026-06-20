"""Unit 1 - Joinable Signal: Trace & Session IDs by Hand

What this shows:
- minting the by-hand correlation primitive: one session_id per conversation, one
  trace_id per operation, an incrementing step (foundations §10);
- emitting one joinable JSONL record per operation with that tuple stamped on it;
- the payoff: you can reconstruct a whole run by filtering on trace_id;
- the missing-foreign-key failure (ADR-0074): a record without the tuple cannot be
  joined to anything, so the loop built on it is blind.

Run (pure Python, no endpoint needed):
    python examples/01/joinable_signal.py

Telemetry here is collected into an in-memory sink so the script can show the *join* in one
place. In a real service you would write each record to a log file or index instead — see
common_loops.log_event, which writes JSONL to stderr by default.
"""

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402


def handle_turn(trace: Trace, user_text: str, sink: io.StringIO) -> Trace:
    """Simulate one agent turn: receive, call a tool, reply — each step joinable."""
    trace = log_event(trace, "request_received", chars=len(user_text), stream=sink)
    trace = log_event(trace, "tool_call", tool="search", args={"q": user_text[:20]}, stream=sink)
    trace = log_event(trace, "reply_ready", ok=True, stream=sink)
    return trace


def main() -> None:
    sink = io.StringIO()  # stand-in for your log file / ES index

    # One session (a whole conversation); each turn is its own trace under it.
    session = Trace.new()
    turn1 = Trace.new(session_id=session.session_id)
    handle_turn(turn1, "find the latency budget", sink)
    turn2 = Trace.new(session_id=session.session_id)
    handle_turn(turn2, "now compare it to last week", sink)

    # A record written WITHOUT the tuple — the missing-foreign-key bug (ADR-0074).
    print(json.dumps({"operation": "cost", "usd": 0.0123}), file=sink)

    records = [json.loads(line) for line in sink.getvalue().splitlines()]

    # Join: reconstruct turn 1 by its trace_id. This is the whole point of the tuple.
    print(f"all records: {len(records)}")
    run = [r for r in records if r.get("trace_id") == turn1.trace_id]
    print(f"\nrun for trace_id={turn1.trace_id[:8]}… ({len(run)} steps):")
    for r in sorted(run, key=lambda r: r["step"]):
        print(f"  step {r['step']}: {r['operation']}")

    # The orphan: no tuple, so it joins to nothing — money you can't attribute to a run.
    orphans = [r for r in records if "trace_id" not in r]
    print(f"\norphaned records (no trace_id): {len(orphans)} -> {orphans}")
    print("garbage signal, garbage control: a loop can't act on what it can't join.")


if __name__ == "__main__":
    main()
