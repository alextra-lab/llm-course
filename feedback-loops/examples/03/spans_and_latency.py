"""Unit 3 - Spans & the Latency Breakdown

What this shows:
- a tiny RequestTimer: monotonic-clock spans recorded inline as a turn runs (the shape of
  personal_agent's telemetry/request_timer.py), with a context manager and sequence numbers;
- classifying spans into phases (setup / context / routing / llm_inference / tool_execution
  / synthesis) so you can see *where* a turn spends its time, not just that it was slow;
- a breakdown you can emit as one joinable telemetry record.

Run (pure Python, no endpoint needed):
    python examples/03/spans_and_latency.py
"""

import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402

# Prefix -> phase. Order matters: specific prefixes before generic ones.
PHASE_MAP: list[tuple[str, str]] = [
    ("llm_call", "llm_inference"),
    ("tool_execution", "tool_execution"),
    ("context", "context"),
    ("routing", "routing"),
    ("synthesis", "synthesis"),
    ("session", "setup"),
]


def classify(name: str) -> str:
    for prefix, phase in PHASE_MAP:
        if name.startswith(prefix):
            return phase
    return "other"


@dataclass
class Span:
    name: str
    sequence: int
    phase: str
    offset_ms: float
    duration_ms: float


class RequestTimer:
    """Monotonic-clock spans for one turn. Captures phases that emit no log of their own."""

    def __init__(self) -> None:
        self._start = time.monotonic_ns()
        self._spans: list[Span] = []
        self._seq = 0

    @contextmanager
    def span(self, name: str) -> Generator[None, None, None]:
        start = time.monotonic_ns()
        try:
            yield
        finally:
            end = time.monotonic_ns()
            self._seq += 1
            self._spans.append(
                Span(
                    name=name,
                    sequence=self._seq,
                    phase=classify(name),
                    offset_ms=round((start - self._start) / 1e6, 1),
                    duration_ms=round((end - start) / 1e6, 1),
                )
            )

    def total_ms(self) -> float:
        return round((time.monotonic_ns() - self._start) / 1e6, 1)

    def breakdown(self) -> list[dict[str, Any]]:
        rows = [vars(s) for s in sorted(self._spans, key=lambda s: s.offset_ms)]
        rows.append({"name": "TOTAL", "phase": "total", "offset_ms": 0.0, "duration_ms": self.total_ms()})
        return rows


def main() -> None:
    timer = RequestTimer()

    # A simulated turn. sleeps stand in for real work; llm_inference dominates, as it does live.
    with timer.span("session_lookup"):
        time.sleep(0.005)
    with timer.span("context_window"):
        time.sleep(0.010)
    with timer.span("routing_decision"):
        time.sleep(0.002)
    with timer.span("llm_call:primary"):
        time.sleep(0.080)
    with timer.span("tool_execution:search"):
        time.sleep(0.020)
    with timer.span("synthesis"):
        time.sleep(0.004)

    print(f"{'span':22} {'phase':16} {'offset_ms':>10} {'duration_ms':>12}")
    for row in timer.breakdown():
        print(f"{row['name']:22} {row['phase']:16} {row['offset_ms']:>10} {row['duration_ms']:>12}")

    # Where did the time go? Sum by phase — the signal a latency budget loop acts on.
    by_phase: dict[str, float] = {}
    for s in timer._spans:
        by_phase[s.phase] = by_phase.get(s.phase, 0.0) + s.duration_ms
    worst = max(by_phase, key=by_phase.get)
    print(f"\nslowest phase: {worst} ({by_phase[worst]} ms of {timer.total_ms()} ms)")

    # Emit the breakdown as one joinable record (one turn = one trace).
    log_event(Trace.new(), "request_timing", total_ms=timer.total_ms(), phases=by_phase)


if __name__ == "__main__":
    main()
