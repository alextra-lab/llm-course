"""Shared helpers for the Feedback Loops course.

Builds on foundations §10 (the joinable session_id/trace_id/step log line) and is reused
across units. Two pieces:

  - Trace: a tiny, by-hand correlation primitive (Unit 1). One ``session_id`` for the whole
    conversation, one ``trace_id`` per logical operation (a turn / an agent run), and an
    incrementing ``step``. Frozen and propagated by value — an OpenTelemetry-shaped context
    without the OTel SDK. Unit 11 meets the standard.
  - log_event: writes one JSONL record per operation, stamped with that tuple, so every line
    this course emits is joinable.

Pure standard library; no LLM endpoint required.
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass, replace
from typing import Any, TextIO


@dataclass(frozen=True)
class Trace:
    """The joinable tuple, carried by value (foundations §10).

    A loop is only as good as its signal: stamp every record with this tuple so a whole
    run can be reconstructed later. Server ids (response.id, x-request-id) identify *one*
    call; this tuple is what ties a run together — the missing-foreign-key point from §10.
    """

    session_id: str  # the whole conversation / user session (stable across turns)
    trace_id: str  # one logical operation: a turn, an agent run
    step: int = 0  # integer ordering within the trace
    kind: str = "user"  # "user" for organic traffic, "system:<source>" for background loops

    @staticmethod
    def new(session_id: str | None = None, kind: str = "user") -> "Trace":
        """Mint a new trace. Reuse an existing session_id to stay in the same conversation."""
        return Trace(
            session_id=session_id or str(uuid.uuid4()),
            trace_id=str(uuid.uuid4()),
            step=0,
            kind=kind,
        )

    def tick(self) -> "Trace":
        """The next step in the same trace (a new frozen Trace; nothing is mutated)."""
        return replace(self, step=self.step + 1)

    def child(self) -> "Trace":
        """A new trace_id under the same session — a nested operation (e.g. a sub-task)."""
        return Trace(session_id=self.session_id, trace_id=str(uuid.uuid4()), kind=self.kind)

    @property
    def is_system(self) -> bool:
        """True for background-loop traffic (kind 'system:<source>'), so it stays separable."""
        return self.kind.startswith("system:")


def log_event(trace: Trace, operation: str, *, stream: TextIO = sys.stderr, **fields: Any) -> Trace:
    """Emit one JSONL record stamped with the joinable tuple; return the advanced trace.

    Use it as ``trace = log_event(trace, "...")`` so steps stay monotonic without bookkeeping.
    The record is the foundations §10 line plus an ``operation`` and domain fields, exactly
    like the Agent Memory course's log_event.
    """
    record = {
        "session_id": trace.session_id,
        "trace_id": trace.trace_id,
        "step": trace.step,
        "kind": trace.kind,
        "operation": operation,
        **fields,
    }
    print(json.dumps(record), file=stream)
    return trace.tick()
