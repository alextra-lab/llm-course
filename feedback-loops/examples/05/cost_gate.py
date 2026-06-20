"""Unit 5 - Budget as Feedforward Control

What this shows:
- a transactional reserve / commit / refund budget gate (the shape of personal_agent's
  cost_gate/gate.py), acting on a *projected* cost before the call, not measured overspend after;
- BudgetDenied: the gate refuses a call that *would* breach the cap — feedforward control;
- commit() settles the difference (usually refunds the over-estimate); refund() returns a
  reservation when a call fails; both keep the running total honest.

Run (pure Python, no endpoint needed):
    python examples/05/cost_gate.py
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402


class BudgetDenied(Exception):
    """Raised when a reservation would exceed the cap. Callers decide how to handle it."""


@dataclass
class Reservation:
    id: str
    estimate: float
    status: str = "active"  # active -> committed | refunded


@dataclass
class CostGate:
    """Reserve against the cap *before* spending; settle the actual cost after."""

    cap_usd: float
    reserved_usd: float = 0.0  # active reservations + committed actuals
    reservations: dict[str, Reservation] = field(default_factory=dict)

    def reserve(self, estimate: float) -> str:
        """Open a reservation for a projected cost, or raise BudgetDenied if it won't fit."""
        if self.reserved_usd + estimate > self.cap_usd:
            raise BudgetDenied(
                f"reserve ${estimate:.4f} would exceed cap ${self.cap_usd:.2f} "
                f"(already reserved ${self.reserved_usd:.4f})"
            )
        rid = str(uuid4())
        self.reservations[rid] = Reservation(rid, estimate)
        self.reserved_usd += estimate
        return rid

    def commit(self, rid: str, actual: float) -> None:
        """Settle the difference between estimate and actual; usually refunds the over-estimate."""
        r = self.reservations[rid]
        self.reserved_usd += actual - r.estimate
        r.status = "committed"

    def refund(self, rid: str) -> None:
        """Return a reservation when the call failed. Idempotent — safe for the reaper to retry."""
        r = self.reservations[rid]
        if r.status != "active":
            return
        self.reserved_usd -= r.estimate
        r.status = "refunded"


def main() -> None:
    gate = CostGate(cap_usd=0.10)
    trace = Trace.new()
    ESTIMATE = 0.03  # what we project each call will cost, before making it

    for i in range(1, 8):
        try:
            rid = gate.reserve(ESTIMATE)  # FEEDFORWARD: decide before spending
        except BudgetDenied as e:
            trace = log_event(trace, "budget_denied", call=i, reason=str(e))
            print(f"call {i}: DENIED before spending — {e}")
            break

        # The call happens here; the real cost comes back smaller than the estimate.
        actual = 0.02 if i != 3 else 0.0  # pretend call 3 fails and is refunded
        if actual == 0.0:
            gate.refund(rid)
            trace = log_event(trace, "budget_refunded", call=i)
            print(f"call {i}: failed -> refunded; reserved now ${gate.reserved_usd:.4f}")
            continue

        gate.commit(rid, actual)
        trace = log_event(trace, "budget_committed", call=i, estimate=ESTIMATE, actual=actual)
        print(f"call {i}: spent ${actual:.4f} (est ${ESTIMATE:.4f}); reserved now ${gate.reserved_usd:.4f}")

    print(f"\ncap ${gate.cap_usd:.2f}; reserved ${gate.reserved_usd:.4f}.")
    print("feedforward: the over-budget call was denied BEFORE it spent, not detected after.")


if __name__ == "__main__":
    main()
