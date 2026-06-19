"""
Unit 3 - Drop & Window: the safe baseline.

When the meter (Unit 1) crosses the threshold (Unit 2), the cheapest action that actually frees
tokens is to drop the oldest turns. This builds the safe baseline two ways:

  - sliding_window(): ANCHOR the head (system messages + the first user message -- the task) so
    it is never evicted, drop the oldest middle turns until back under budget, keep the recent
    tail by construction, and leave a marker where the gap is.
  - trim_priority(): when whole components must be shed, drop them in production's order --
    history -> memory -> tool definitions.

Every drop emits a `compaction` record (strategy=drop) so the loss is visible, never silent.

Runs fully offline -- no endpoint needed.

    python context-compression/examples/03/drop_and_window.py
"""

import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common_context import estimate_tokens, log_event

BUDGET = 8000
MARKER = {"role": "system", "content": "[Earlier messages truncated]"}

# A long session that is OVER budget: a system prompt + the first user message (the task -- the
# head we must never lose), then many turns, with a big tool output sitting in the OLD middle.
SYSTEM = {"role": "system", "content": "You are a coding assistant. Keep answers short and correct."}
TASK = {"role": "user", "content": "Refactor utils.py and keep every public function working."}
OLD_BIG_READ = {"role": "tool", "tool_call_id": "c1",
                "content": "def f(x):\n    return x * 2\n" * 1400}   # ~8k tokens, read early on

MESSAGES = [SYSTEM, TASK]
MESSAGES += [{"role": "assistant", "content": "I'll read utils.py first."}, OLD_BIG_READ]
for i in range(6):   # later back-and-forth -- the recent, relevant tail
    MESSAGES += [{"role": "user", "content": f"Also rename helper_{i} everywhere it is used."},
                 {"role": "assistant", "content": f"Renamed helper_{i}; tests still pass."}]


def _head_end(messages):
    """The head = leading system messages + the first user message (the task). Index one past it."""
    i = 0
    while i < len(messages) and messages[i]["role"] == "system":
        i += 1
    if i < len(messages) and messages[i]["role"] == "user":
        i += 1
    return i


def sliding_window(messages, budget):
    """Drop the oldest non-head messages until the prompt fits. The head is anchored (never
    dropped) and the recent tail survives by construction, since we only ever pop from the front
    of the middle. Returns (new_messages, dropped_count)."""
    head_end = _head_end(messages)
    head, middle = messages[:head_end], list(messages[head_end:])
    dropped = 0
    while middle and estimate_tokens(head + [MARKER] + middle) > budget:
        middle.pop(0)          # evict the OLDEST middle turn -- least likely to matter now
        dropped += 1
    return (head + [MARKER] + middle if dropped else head + middle), dropped


def trim_priority(components, budget):
    """Shed whole components in production's order -- history -> memory -> tool_defs -- until the
    total fits. `components` is {name: tokens}. Returns (dropped_names, remaining_total)."""
    total = sum(components.values())
    dropped = []
    for name in ("history", "memory", "tool_defs"):
        if total <= budget:
            break
        total -= components.get(name, 0)
        dropped.append(name)
    return dropped, total


def main():
    session_id, trace_id = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    before = estimate_tokens(MESSAGES)
    print(f"before: {len(MESSAGES)} messages, {before} tokens, {before / BUDGET:.0%} of budget "
          f"({'OVER' if before > BUDGET else 'under'})")

    kept, dropped = sliding_window(MESSAGES, BUDGET)
    after = estimate_tokens(kept)
    print(f"after sliding window: {len(kept)} messages, {after} tokens, {after / BUDGET:.0%} "
          f"of budget -- dropped {dropped} oldest middle turn(s)")
    print(f"head preserved? system + task still present: "
          f"{kept[0] is SYSTEM and kept[1] is TASK}")

    # The compaction record -- strategy=drop. This is the OBSERVABILITY.md compaction line; the
    # `referenced_later` quality flag is added in Unit 11.
    log_event(session_id, trace_id, 0, "compaction", strategy="drop", trigger="hard",
              tokens_before=before, tokens_after=after, dropped=dropped, kept=len(kept))

    # The other shape: shed whole components in priority order when even windowing is not enough.
    comps = {"history": 7000, "memory": 1800, "tool_defs": 900, "system": 60}
    order, remaining = trim_priority(comps, BUDGET)
    print(f"\ntrim priority (budget {BUDGET}): would drop {order or 'nothing'} "
          f"-> {remaining} tokens remain")


if __name__ == "__main__":
    main()
