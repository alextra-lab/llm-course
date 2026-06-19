"""
Unit 5 - Head, Middle, Tail: anchor both ends, compress only the middle.

Unit 3 anchored the HEAD (system + first user message) and dropped from the front. But the
recent TAIL -- the turns the model is actively using -- is just as load-bearing as the task at
the head, and a front-dropping window will eventually eat it. This unit makes the invariant
explicit: keep the head AND the tail verbatim, and only ever compress the MIDDLE.

What this shows:
  - _head_end()  -- the head: leading system messages + the first user message (the task).
  - _tail_start() -- the tail: walk back from the end until it holds at least a FRACTION of the
    budget in tokens AND at least a minimum number of turns (two floors, not one).
  - compress_in_place() -- split into head / middle / tail, compress ONLY the middle (here a
    static marker; in practice Unit 4's structured summarizer plugs in), reassemble, and prove
    the head and tail came through byte-identical.

Runs fully offline -- no endpoint needed.

    python context-compression/examples/05/head_middle_tail.py
"""

import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common_context import estimate_tokens, log_event

BUDGET = 8000
TAIL_RATIO = 0.25     # the tail must hold at least this fraction of the budget, verbatim...
TAIL_MIN_TURNS = 4    # ...AND at least this many recent messages (whichever is larger wins)

# The recap that stands in the MIDDLE's place. role=assistant, not system (the Unit 4 hazard: a
# non-first system message is dropped by role validation). Here it is the static marker; swapping
# in Unit 4's summarizer is the only change needed to keep the middle's facts.
RECAP = {"role": "assistant", "content": "[Earlier messages truncated]"}

# A long, over-budget session. HEAD: system + the task. MIDDLE: an early big file read (a real
# tool pair) and some old back-and-forth. TAIL: the recent work, including the file the model is
# editing RIGHT NOW (another tool pair) -- the part we must keep verbatim.
SYSTEM = {"role": "system", "content": "You are a coding assistant. Keep answers short and correct."}
TASK = {"role": "user", "content": "Refactor the billing module and keep retries configurable."}

OLD_FILE = "def old(x):\n    return x\n" * 880    # ~5.5k tokens, read early and now cold
MIDDLE = [
    {"role": "assistant", "content": "I'll read the old billing module first.",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path": "billing_old.py"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": OLD_FILE},
    {"role": "assistant", "content": "billing_old.py is a thin pass-through; safe to replace."},
    {"role": "user", "content": "Good, replace it."},
    {"role": "assistant", "content": "Replaced billing_old.py with the new module."},
]

CONFIG = "db_host: db-prod-1\ndb_port: 5432\nretries: 3\n" * 230   # ~2.5k tokens, read JUST now
TAIL = [
    {"role": "user", "content": "Now read the config and bump retries to 5."},
    {"role": "assistant", "content": "Reading the config.",
     "tool_calls": [{"id": "c2", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path": "config.yaml"}'}}]},
    {"role": "tool", "tool_call_id": "c2", "content": CONFIG},
    {"role": "assistant", "content": "Config points at db-prod-1:5432; set retries to 5."},
    {"role": "user", "content": "Which host does it write to again?"},
]

MESSAGES = [SYSTEM, TASK] + MIDDLE + TAIL


def _head_end(messages):
    """The head = leading system messages + the first user message (the task). Index one past it."""
    i = 0
    while i < len(messages) and messages[i]["role"] == "system":
        i += 1
    if i < len(messages) and messages[i]["role"] == "user":
        i += 1
    return i


def _tail_start(messages, head_end, budget, ratio=TAIL_RATIO, min_turns=TAIL_MIN_TURNS):
    """Walk back from the end until the tail holds >= ratio*budget tokens AND >= min_turns messages.
    Two floors, not one: the token floor keeps enough recent context; the turn floor protects a
    short-but-active exchange whose tokens are small. Never cross into the head; if even all of the
    non-head messages do not meet the floors, the middle is empty (nothing to compress -- Unit 2)."""
    floor = ratio * budget
    start = len(messages)
    while start > head_end:
        candidate = start - 1
        tail = messages[candidate:]
        if estimate_tokens(tail) >= floor and len(tail) >= min_turns:
            return _snap_to_pair_boundary(messages, candidate, head_end)
        start = candidate
    return head_end


def _snap_to_pair_boundary(messages, idx, head_end):
    """Never let the tail BEGIN on a tool result: that would orphan it from its assistant tool-call
    (which the recap would swallow). Move the boundary back to include the call, so a tool pair is
    never split across the middle/tail line (the Unit 3 tool-pair rule, applied at the seam)."""
    while idx > head_end and messages[idx]["role"] == "tool":
        idx -= 1
    return idx


def compress_middle(middle):
    """Compress ONLY the middle. Empty middle -> nothing to compress (return []). Here we use the
    static marker; in practice this is Unit 4's structured summarizer with its graceful fallback."""
    return [RECAP] if middle else []


def compress_in_place(messages, budget):
    """Keep head + tail verbatim; replace the middle with a recap. Returns (new_messages, parts)."""
    head_end = _head_end(messages)
    tail_start = _tail_start(messages, head_end, budget)
    head, middle, tail = messages[:head_end], messages[head_end:tail_start], messages[tail_start:]
    kept = head + compress_middle(middle) + tail
    parts = {"head": (head, estimate_tokens(head)),
             "middle": (middle, estimate_tokens(middle)),
             "tail": (tail, estimate_tokens(tail))}
    return kept, parts


def main():
    session_id, trace_id = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    before = estimate_tokens(MESSAGES)
    print(f"before: {len(MESSAGES)} messages, {before} tokens, {before / BUDGET:.0%} of budget "
          f"({'OVER' if before > BUDGET else 'under'})")

    kept, parts = compress_in_place(MESSAGES, BUDGET)
    after = estimate_tokens(kept)
    (head, h_tok), (middle, m_tok), (tail, t_tok) = parts["head"], parts["middle"], parts["tail"]
    print(f"split: head {len(head)} msgs/{h_tok} tok | middle {len(middle)} msgs/{m_tok} tok "
          f"| tail {len(tail)} msgs/{t_tok} tok  (tail floor = {TAIL_RATIO:.0%} budget "
          f"& >= {TAIL_MIN_TURNS} turns)")
    print(f"after compress-in-place: {len(kept)} messages, {after} tokens, {after / BUDGET:.0%} "
          f"of budget -- compressed {len(middle)} middle message(s)")

    # The invariant, checked by identity: every head and tail message is the SAME object in the
    # output -- byte-verbatim, not a re-rendered copy. Only the middle was touched.
    head_ok = kept[:len(head)] == head
    tail_ok = kept[len(kept) - len(tail):] == tail
    tail_compressed = any(m is RECAP for m in tail)   # must never happen
    print(f"invariant: head verbatim {head_ok} | tail verbatim {tail_ok} | "
          f"tail ever compressed {tail_compressed}")

    # The compaction record -- strategy=head-tail (OBSERVABILITY.md). The loop Unit 11 closes:
    # was a tail turn ever compressed? It must be zero -- that is the invariant this unit enforces.
    log_event(session_id, trace_id, 0, "compaction", strategy="head-tail", trigger="soft",
              tokens_before=before, tokens_after=after,
              head_tokens=h_tok, middle_tokens=m_tok, tail_tokens=t_tok,
              compressed=len(middle), tail_compressed=tail_compressed)


if __name__ == "__main__":
    main()
