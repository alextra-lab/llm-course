"""Unit 9 - Human in the Loop, Async

What this shows:
- a promoted proposal becomes a ticket awaiting a human verdict — the async approval channel
  (personal_agent uses Linear: labels, states, comments, read from a phone);
- a poller reads the verdict back and routes it (the shape of captains_log/feedback.py):
  Approved -> act, Rejected -> suppress the fingerprint, Re-evaluate -> send back for refinement;
- the human's verdict is itself SIGNAL: a rejection is recorded so the same proposal cannot
  re-promote later — the loop stays *open* until the human closes it, by design.

Run (pure Python, no endpoint needed):
    python examples/09/human_in_the_loop.py
"""

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402


@dataclass
class Ticket:
    fingerprint: str
    what: str
    verdict: str | None = None  # set asynchronously by a human: Approved / Rejected / Re-evaluate


# Two proposals were promoted (Unit 8) and filed as tickets. A human triages them later, from
# wherever they are — the channel is asynchronous on purpose.
TICKETS = [
    Ticket("a1b2", "Add a retry budget for Elasticsearch queries"),
    Ticket("c3d4", "Rewrite the orchestrator to be fully event-driven"),
    Ticket("e5f6", "Tune the summarizer temperature"),
]
HUMAN_VERDICTS = {"a1b2": "Approved", "c3d4": "Rejected", "e5f6": "Re-evaluate"}  # arrives later

suppressed: set[str] = set()  # fingerprints a human has rejected — recorded as signal
approved_for_action: list[str] = []
needs_refinement: list[str] = []  # sent back for the agent to sharpen and resurface


def poll_feedback(tickets: list[Ticket], trace: Trace) -> Trace:
    """Read each ticket's human verdict and route it (handle_approved/rejected/deepen)."""
    for t in tickets:
        t.verdict = HUMAN_VERDICTS.get(t.fingerprint)
        if t.verdict is None:
            continue  # still awaiting a human — the loop is not closed yet
        if t.verdict == "Approved":
            approved_for_action.append(t.fingerprint)
            trace = log_event(trace, "feedback_polled", fingerprint=t.fingerprint, verdict="Approved")
        elif t.verdict == "Rejected":
            suppressed.add(t.fingerprint)  # the rejection persists as signal (suppression)
            trace = log_event(trace, "feedback_polled", fingerprint=t.fingerprint, verdict="Rejected")
        elif t.verdict == "Re-evaluate":
            needs_refinement.append(t.fingerprint)  # route back: refine and resurface
            trace = log_event(trace, "feedback_polled", fingerprint=t.fingerprint, verdict="Re-evaluate")
    return trace


def main() -> None:
    trace = Trace.new(kind="system:feedback")

    trace = poll_feedback(TICKETS, trace)
    for t in TICKETS:
        print(f"  [{t.fingerprint}] {t.verdict or 'awaiting human'}: {t.what}")

    print(f"\napproved    -> the harness may now act on: {approved_for_action}")
    print(f"rejected    -> suppressed fingerprints: {sorted(suppressed)}")
    print(f"re-evaluate -> sent back for refinement: {needs_refinement}")

    # The verdict is signal: a rejected idea, when it recurs, is suppressed — not re-promoted.
    recurring = "c3d4"
    if recurring in suppressed:
        trace = log_event(trace, "proposal_suppressed", fingerprint=recurring)
        print(f"\n'{recurring}' recurred, but the human rejected it before -> suppressed, not re-promoted.")

    print("\nclosed vs human-closed: this loop only closes when a person decides. That is the point —")
    print("changing the agent itself is too high-stakes to auto-close (ADR-0040: the missing piece is feedback).")


if __name__ == "__main__":
    main()
