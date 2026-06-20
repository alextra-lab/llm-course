"""Unit 10 - Watching the Apparatus

What this shows:
- a meta-monitor: a loop whose job is to check that the OBSERVABILITY itself is intact — pick a
  session and walk every substrate, asserting the joining tuple (Unit 1) exists and matches
  (the shape of personal_agent's observability/joinability/walk.py);
- loud degradation: one substrate being down is a distinct signal (yellow) from "everything is
  fine" (green) — the monitor never goes silently green when it could not actually check;
- an aggregated outcome (green / yellow / red) over per-substrate checks and orphans.

Run (pure Python, no endpoint needed):
    python examples/10/joinability_walk.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402

SESSION = "sess-42"

# Three substrates, each storing records for the session. In the harness these are Postgres,
# Elasticsearch, Neo4j, Redis; here they are dicts. A record must carry the session's trace_id
# to be joinable. `None` means the substrate is unreachable this tick.
SUBSTRATES_HEALTHY = {
    "postgres": [{"trace_id": "t-1"}, {"trace_id": "t-2"}],
    "elasticsearch": [{"trace_id": "t-1"}, {"trace_id": "t-2"}],
    "neo4j": [{"trace_id": "t-1"}],
}


def walk(substrates: dict[str, list | None]) -> tuple[str, list[str]]:
    """Walk each substrate; return an aggregated outcome and the orphans found.

    Each substrate is checked independently (try/except in the real walker) so one being down
    does not blind the whole walk — it degrades loudly to 'yellow', not silently to 'green'.
    """
    checks: list[str] = []  # "ok" | "down"
    orphans: list[str] = []
    for name, records in substrates.items():
        if records is None:
            checks.append("down")  # unreachable — we could NOT verify it
            continue
        checks.append("ok")
        for i, rec in enumerate(records):
            if not rec.get("trace_id"):  # missing the join key (the 4,077-NULL bug, Unit 1)
                orphans.append(f"{name}[{i}] missing trace_id")

    # aggregate_outcome: worst-of. orphans -> red; any substrate down -> yellow; else green.
    if orphans:
        outcome = "red"
    elif "down" in checks:
        outcome = "yellow"
    else:
        outcome = "green"
    return outcome, orphans


def run_case(label: str, substrates: dict) -> None:
    trace = Trace.new(kind="system:joinability")
    outcome, orphans = walk(substrates)
    log_event(trace, "joinability_walk", session=SESSION, outcome=outcome, orphans=len(orphans))
    print(f"{label:28} -> {outcome.upper():6} ({len(orphans)} orphan(s))")
    for o in orphans:
        print(f"    orphan: {o}")


def main() -> None:
    run_case("healthy", SUBSTRATES_HEALTHY)

    # A record lost its join key — the cost-ledger bug from Unit 1, caught by the monitor.
    broken = {**SUBSTRATES_HEALTHY, "postgres": [{"trace_id": "t-1"}, {"trace_id": ""}]}
    run_case("a record lost its trace_id", broken)

    # A substrate is unreachable — loud degradation: yellow, not a false green.
    degraded = {**SUBSTRATES_HEALTHY, "neo4j": None}
    run_case("neo4j unreachable", degraded)

    print("\nthe observer, observed: a green run means the signal is still joinable;")
    print("yellow/red mean the apparatus itself needs attention — before the loops act on bad data.")


if __name__ == "__main__":
    main()
