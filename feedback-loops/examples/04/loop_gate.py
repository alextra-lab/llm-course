"""Unit 4 - The First Closed Loop: a Runtime Gate

What this shows:
- a per-tool finite-state machine that watches an agent's tool calls (the shape of
  personal_agent's orchestrator/loop_gate.py);
- three signals, escalating: same-args call identity, consecutive repeats (advisory), and
  identical output (terminal — "identical output is pathological");
- the closed loop: sense (the call) -> decide (the policy) -> act (BLOCK) -> emit a verdict;
- the Unit 0 runaway, prevented: a tool stuck returning the same output is stopped at the
  second identical result instead of looping to an iteration limit.

Run (pure Python, no endpoint needed):
    python examples/04/loop_gate.py
"""

import hashlib
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402


class Decision(str, Enum):
    ALLOW = "allow"
    WARN_CONSECUTIVE = "warn_consecutive"  # advisory: execute, but the loop is suspicious
    BLOCK_IDENTITY = "block_identity"  # terminal: same args called too many times
    BLOCK_OUTPUT = "block_output"  # terminal: identical output, the loop is pathological


@dataclass
class GateResult:
    decision: Decision
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.decision in (Decision.BLOCK_IDENTITY, Decision.BLOCK_OUTPUT)


def stable_hash(value: object) -> str:
    """Order-independent hash of a tool's args or output (personal_agent's stable_hash)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


@dataclass
class ToolFSM:
    """One state machine per tool name."""

    signature_counts: dict[str, int] = field(default_factory=dict)  # same (args) seen how often
    output_counts: dict[str, int] = field(default_factory=dict)  # same output seen how often
    consecutive: int = 0
    blocked: bool = False  # terminal: all further calls return blocked


@dataclass
class Policy:
    max_per_signature: int = 3  # same args more than this -> terminal BLOCK_IDENTITY
    max_consecutive: int = 2  # same tool this many times in a row -> advisory WARN


class LoopGate:
    """Holds one ToolFSM per tool for a request. All decisions are returned as GateResults."""

    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy or Policy()
        self.fsms: dict[str, ToolFSM] = {}
        self._last_tool: str | None = None

    def _fsm(self, tool: str) -> ToolFSM:
        return self.fsms.setdefault(tool, ToolFSM())

    def check_before(self, tool: str, args: object) -> GateResult:
        """Pre-execution: call-identity and consecutiveness. First match wins."""
        fsm = self._fsm(tool)
        if fsm.blocked:
            return GateResult(Decision.BLOCK_IDENTITY, "tool already blocked this turn")
        fsm.consecutive = fsm.consecutive + 1 if self._last_tool == tool else 1
        self._last_tool = tool
        sig = stable_hash(args)
        fsm.signature_counts[sig] = fsm.signature_counts.get(sig, 0) + 1
        if fsm.signature_counts[sig] > self.policy.max_per_signature:
            fsm.blocked = True
            return GateResult(Decision.BLOCK_IDENTITY, f"same args {fsm.signature_counts[sig]}x")
        if fsm.consecutive >= self.policy.max_consecutive:
            return GateResult(Decision.WARN_CONSECUTIVE, f"{tool} {fsm.consecutive}x in a row")
        return GateResult(Decision.ALLOW)

    def observe(self, tool: str, output: object) -> GateResult:
        """Post-execution: identical output is pathological -> terminal BLOCK_OUTPUT."""
        fsm = self._fsm(tool)
        h = stable_hash(output)
        fsm.output_counts[h] = fsm.output_counts.get(h, 0) + 1
        if fsm.output_counts[h] >= 2:
            fsm.blocked = True
            return GateResult(Decision.BLOCK_OUTPUT, f"identical output seen {fsm.output_counts[h]}x")
        return GateResult(Decision.ALLOW)


def main() -> None:
    # A broken tool that returns the SAME output every call — the Unit 0 runaway in miniature.
    def stuck_tool(args: dict) -> str:
        return "the same unhelpful result"

    gate = LoopGate()
    trace = Trace.new()
    iterations = 0
    MAX = 10  # without a gate, the agent would spin until this hard limit

    for _ in range(MAX):
        iterations += 1
        before = gate.check_before("search", {"q": "token stats"})
        if before.blocked:
            trace = log_event(trace, "gate_blocked", tool="search", reason=before.reason)
            print(f"iter {iterations}: BLOCKED before execution — {before.reason}")
            break
        if before.decision is Decision.WARN_CONSECUTIVE:
            print(f"iter {iterations}: warn — {before.reason} (executing, with a hint)")

        output = stuck_tool({"q": "token stats"})  # execute the tool

        after = gate.observe("search", output)
        if after.blocked:
            trace = log_event(trace, "gate_blocked", tool="search", reason=after.reason)
            print(f"iter {iterations}: BLOCKED after execution — {after.reason}")
            break
        print(f"iter {iterations}: allowed")

    print(f"\nstopped after {iterations} iterations (hard limit was {MAX}).")
    print("the gate closed the loop on identical output — sense, decide, act, emit.")


if __name__ == "__main__":
    main()
