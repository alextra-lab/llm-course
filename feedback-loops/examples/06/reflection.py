"""Unit 6 - Reflection: Self-Critique from Traces

What this shows:
- mining a turn's own trace for a failure excerpt — deterministic, no model needed (the shape
  of personal_agent's _extract_failure_excerpt): find failed tool calls, and read the next
  event to see what the agent did about it (retry / give up);
- turning that into a structured self-critique — rationale + a proposed_change(what/why/how) —
  the way captains_log/reflection.py asks a model for a CaptainLogEntry;
- linking the reflection back to the trace it came from (a TelemetryRef), so the loop is joinable.

The failure mining runs with no endpoint. The model critique is OPT-IN: set OPENAI_BASE_URL
(your foundations .env) or that part skips cleanly.

    python examples/06/reflection.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # feedback-loops/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common_loops import Trace, log_event  # noqa: E402

# A turn that struggled: the same tool failed twice, then the agent gave up. (Events are the
# joinable records from Units 1–2 — one trace, ordered steps.)
CRITIQUED_TRACE_ID = "turn-3d3c505e"  # the trace_id of the turn this reflection is about
TRACE_EVENTS = [
    {"step": 0, "operation": "request_received"},
    {"step": 1, "operation": "tool_call_started", "tool": "query_elasticsearch"},
    {"step": 2, "operation": "tool_call_failed", "tool": "query_elasticsearch", "error": "connection timeout"},
    {"step": 3, "operation": "tool_call_started", "tool": "query_elasticsearch"},
    {"step": 4, "operation": "tool_call_failed", "tool": "query_elasticsearch", "error": "connection timeout"},
    {"step": 5, "operation": "reply_ready", "ok": False},
]
for _ev in TRACE_EVENTS:  # all events of one turn share its trace_id (Unit 1)
    _ev["trace_id"] = CRITIQUED_TRACE_ID

FAILURE_OPS = {"tool_call_failed"}


def extract_failure_excerpt(events: list[dict]) -> dict | None:
    """Deterministically mine the failure path from a trace. No model involved."""
    failed = []
    for i, ev in enumerate(events):
        is_failure = ev.get("operation") in FAILURE_OPS or ev.get("status") in {"error", "timeout"}
        if not is_failure:
            continue
        # Look at the next event to classify the recovery action.
        nxt = events[i + 1] if i + 1 < len(events) else {}
        if nxt.get("operation") == "tool_call_started" and nxt.get("tool") == ev.get("tool"):
            recovery = "retry"
        elif nxt.get("operation") == "reply_ready":
            recovery = "gave up"
        else:
            recovery = "other"
        failed.append({"tool": ev.get("tool"), "error": ev.get("error"), "recovery": recovery})
    if not failed:
        return None
    return {
        "failed_tool_calls": failed,
        "error_summary": (failed[-1]["error"] or "")[:200],
        "recovery_actions": [f["recovery"] for f in failed],
    }


def generate_reflection(events: list[dict], excerpt: dict | None) -> dict | None:
    """Ask the model to critique the turn and propose a change. Opt-in; skips with no endpoint."""
    if not (os.environ.get("OPENAI_BASE_URL") and os.environ.get("OPENAI_API_KEY")):
        print("(no OPENAI_BASE_URL/OPENAI_API_KEY — skipping the model critique; the excerpt above is what feeds it)")
        return None
    from common import MODEL, get_client  # noqa: E402

    prompt = (
        "You are reviewing an agent's own turn. Critique it and propose one improvement.\n"
        f"Trace events: {json.dumps(events)}\n"
        f"Failure excerpt: {json.dumps(excerpt)}\n"
        'Respond with ONLY JSON: {"rationale": str, '
        '"proposed_change": {"what": str, "why": str, "how": str}, "impact_assessment": str}'
    )
    resp = get_client().chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": prompt}], temperature=0.2
    )
    try:
        return json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        return {"rationale": resp.choices[0].message.content, "proposed_change": None}


def main() -> None:
    trace = Trace.new(kind="system:reflection")  # reflection is background work, not a user turn

    excerpt = extract_failure_excerpt(TRACE_EVENTS)
    print("failure excerpt (mined deterministically, no model):")
    print(json.dumps(excerpt, indent=2))

    reflection = generate_reflection(TRACE_EVENTS, excerpt)
    if reflection:
        print("\nmodel self-critique:")
        print(json.dumps(reflection, indent=2))

    # Link the reflection back to the turn it critiques — the loop must stay joinable.
    log_event(
        trace,
        "reflection_created",
        about_trace=CRITIQUED_TRACE_ID,  # the TelemetryRef back to the critiqued turn
        has_proposal=bool(reflection and reflection.get("proposed_change")),
        failures=len(excerpt["failed_tool_calls"]) if excerpt else 0,
    )


if __name__ == "__main__":
    main()
