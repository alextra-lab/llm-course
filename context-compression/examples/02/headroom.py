"""
Unit 2 - The Cheapest Compression Is None: decide whether to compress at all.

The opening rule of the course is "under budget, do nothing." This script makes that rule
concrete: a `decide()` function that reads the meter (Unit 1) and returns SKIP or COMPRESS
against a soft threshold that leaves headroom, and a `cache_cost_of_compacting()` estimate
that puts a number on what a NEEDLESS compaction throws away -- the cached prefix that would
have to re-prefill at full price. It logs a `compaction_decision` line every time, including
when it decides to do nothing: the do-nothing is a measured choice, not a missing one.

The decision math runs with no endpoint at all. The exact-count cross-check is OPT-IN: set
OPENAI_BASE_URL (your foundations .env) or that part skips cleanly.

    python context-compression/examples/02/headroom.py
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common import get_client, MODEL
from common_context import estimate_tokens, server_prompt_tokens, log_event

BUDGET = 8000        # the model's working window, in tokens (ask YOUR server for the real number)
SOFT = 0.65          # below this fraction of budget: do nothing (a real harness's soft trigger)
RESERVED = 1000      # headroom kept for the next response -- never spend the last tokens

# A modest session: a system prompt, a couple of turns, one file read. Comfortably under budget
# -- which is the whole point. This is the state an agent spends most of its life in.
SYSTEM = ("You are a coding assistant. Plan before you act, use the tools provided, and keep "
          "your answers short and correct.")
FILE = "def f(x):\n    return x * 2\n" * 120   # a file the agent read -- the biggest single thing

MESSAGES = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "What does utils.py do? Read it and summarize."},
    {"role": "assistant", "content": "I'll read it.",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path": "utils.py"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": FILE},
    {"role": "assistant", "content": "utils.py defines a doubling helper used across the codebase."},
    {"role": "user", "content": "Thanks. Now also check the tests."},
]


def decide(messages, budget, soft=SOFT, reserved=RESERVED):
    """Return ('skip'|'compress', reason, fraction). The opening rule of the course lives here:
    if we are below the soft threshold AND have headroom for the next response, do nothing."""
    used = estimate_tokens(messages)
    fraction = used / budget
    spendable = budget - reserved
    if used < soft * budget and used < spendable:
        return "skip", f"under soft threshold ({fraction:.0%} < {soft:.0%}); headroom intact", fraction
    return "compress", f"crossed soft threshold ({fraction:.0%} >= {soft:.0%})", fraction


def cache_cost_of_compacting(messages, edit_index):
    """Estimate the prompt-cache tokens a compaction would throw away (§10).

    A normal turn APPENDS to the end, so the prefix is byte-identical and the whole thing is a
    cache hit (cheap). Rewriting history at `edit_index` changes the bytes from there on, so
    every cached token at or after that point is invalidated and must re-prefill at full price.
    The cost of a needless compaction is everything below -- paid to solve a problem you do not
    have yet."""
    invalidated = estimate_tokens(messages[edit_index:])
    return invalidated


def main():
    session_id, trace_id = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    used = estimate_tokens(MESSAGES)
    decision, reason, fraction = decide(MESSAGES, BUDGET)

    print(f"context: {used} tokens, {fraction:.0%} of a {BUDGET}-token budget")
    print(f"decision: {decision.upper()}  --  {reason}\n")

    # What a compaction HERE would cost the cache: collapse the middle (everything after the
    # first user turn), so the cache is invalidated from index 1 onward.
    edit_index = 1
    cache_loss = cache_cost_of_compacting(MESSAGES, edit_index)
    print(f"if you compacted anyway: ~{cache_loss} cached prefix tokens invalidated "
          f"(re-prefill at full price instead of a ~0.1x cache read), plus the summarizer's own "
          f"tokens, plus whatever the summary drops. All to fit a window that is {fraction:.0%} full.")

    # Observability: log the decision -- INCLUDING the skip. A do-nothing you did not record is
    # indistinguishable from one you forgot to make.
    log_event(session_id, trace_id, 0, "compaction_decision",
              decision=decision, budget=BUDGET, used=used, fraction=round(fraction, 3),
              soft=SOFT, cache_tokens_at_risk=cache_loss)

    # OPT-IN: would the exact server count flip the decision near the threshold?
    if not os.environ.get("OPENAI_BASE_URL"):
        print("\n(OPENAI_BASE_URL not set -- skipping the exact-count cross-check; the heuristic "
              "decision above stands on its own.)")
        return
    exact = server_prompt_tokens(get_client(), MESSAGES, MODEL)
    exact_decision, _, exact_fraction = decide(
        [{"role": "user", "content": "x" * (exact * 4)}], BUDGET)  # size a stand-in to `exact` tokens
    flip = " (decision UNCHANGED)" if exact_decision == decision else " (decision FLIPS -- you were near the line)"
    print(f"\nheuristic: {used} tokens ({fraction:.0%}); server exact: {exact} tokens "
          f"({exact_fraction:.0%}){flip}")


if __name__ == "__main__":
    main()
