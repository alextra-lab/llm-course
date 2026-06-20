"""Unit 12 - The Measured Default

What this shows:
- evals as the OUTERMOST loop, run as a hypothesis, not a gate (the FRE-453 stance): each
  expectation is compared to the actual result and reported as a MATCH/MISMATCH *finding*;
  nothing gates on it. The first run is the behavioural baseline — you run it to learn, not to pass;
- the ONE hard gate is instrument health: did every case actually produce telemetry? If a case
  emitted no trace, the harness exits non-zero — because an eval you cannot observe proves nothing;
- eval isolation: eval traffic is tagged (kind="system:eval", eval_mode) so it never pollutes the
  production telemetry the learning loops feed on.

Run (pure Python, no endpoint needed):
    python examples/12/eval_as_hypothesis.py   ; echo "exit=$?"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402

# Each case is a hypothesis about how the agent SHOULD route a turn.
CASES = [
    {"input": "what's 2+2?", "expected_route": "direct"},
    {"input": "summarize my notes from last week", "expected_route": "memory"},
    {"input": "research the latest on vLLM batching", "expected_route": "tools"},
]


def stub_agent(text: str) -> tuple[str, bool]:
    """A deterministic stand-in. Returns (route, emitted_telemetry?)."""
    if "research" in text:
        route = "tools"
    elif "notes" in text or "last week" in text:
        route = "memory"
    else:
        route = "direct"
    emitted = "music" not in text  # pretend one class of input fails to instrument
    return route, emitted


def main() -> None:
    findings: list[str] = []
    instrument_failures = 0

    for case in CASES:
        # eval traffic is ISOLATED: system:eval + eval_mode so it never feeds the learning loop.
        trace = Trace.new(kind="system:eval")
        actual, emitted = stub_agent(case["input"])

        if emitted:
            log_event(trace, "eval_turn", eval_mode=True, route=actual)
        else:
            instrument_failures += 1  # the only thing that can fail the run

        verdict = "MATCH" if actual == case["expected_route"] else "MISMATCH"
        findings.append(f"{verdict:8} {case['input']!r} -> {actual} (expected {case['expected_route']})")

    print("findings (hypotheses, never gates):")
    for f in findings:
        print(f"  {f}")
    matches = sum(1 for f in findings if f.startswith("MATCH"))
    print(f"\n{matches}/{len(CASES)} matched the hypothesis — a baseline to learn from, not a pass mark.")

    # The ONE hard gate: instrument health. A mismatch is fine; an unobservable case is not.
    if instrument_failures:
        print(f"\nINSTRUMENT HEALTH FAILED: {instrument_failures} case(s) emitted no telemetry.")
        sys.exit(1)
    print("\ninstrument health OK: every case was observable. Exit 0 regardless of match rate.")


if __name__ == "__main__":
    main()
