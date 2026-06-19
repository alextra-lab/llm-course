"""
Unit 1 - Measuring the Window: read the budget before you manage it.

Builds a context meter that attributes every token to where it came from -- system prompt,
tool definitions, conversation history, and (the dangerous one) tool outputs -- so you can see
WHAT is filling the window, not just that it is full. It then checks the cheap heuristic
against the server's EXACT count, and emits one joinable telemetry line: the first record in
this course's observability through-line.

The heuristic meter runs with no endpoint at all. The exact-count check is OPT-IN: set
OPENAI_BASE_URL (your foundations .env) or that part skips cleanly.

    python context-compression/examples/01/meter.py
"""

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common import get_client, MODEL
from common_context import estimate_tokens, server_prompt_tokens, log_event

BUDGET = 8000   # the model's working window, in tokens (ask YOUR server for the real number)

SYSTEM = ("You are a coding assistant. Plan before you act, use the tools provided, and keep "
          "your answers short and correct.")

# Tool schemas count too -- they are resent every turn (foundations §23).
TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a file from disk.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "run_bash", "description": "Run a shell command.",
     "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
]

# A short conversation -- then one big tool output (a file the agent read). Tool outputs are
# usually the largest single thing in the window; the meter makes that obvious.
BIG_FILE = "def f(x):\n    return x * 2\n" * 400   # stand-in for a real file dump

MESSAGES = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "What does utils.py do? Read it and summarize."},
    {"role": "assistant", "content": "I'll read it.",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path": "utils.py"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": BIG_FILE},
    {"role": "assistant", "content": "utils.py defines a doubling helper used across the codebase."},
    {"role": "user", "content": "Thanks. Now also check the tests."},
]


def meter(messages, tools, budget):
    """Attribute tokens to where they come from, then print the breakdown as a budget bar."""
    breakdown = {
        "system":       estimate_tokens([m for m in messages if m["role"] == "system"]),
        "tools":        estimate_tokens(json.dumps(tools)),
        "history":      estimate_tokens([m for m in messages if m["role"] in ("user", "assistant")]),
        "tool_outputs": estimate_tokens([m for m in messages if m["role"] == "tool"]),
    }
    total = sum(breakdown.values())
    print(f"context meter  (budget {budget} tokens)")
    for part, toks in breakdown.items():
        bar = "#" * round(40 * toks / max(total, 1))
        print(f"  {part:<13} {toks:>6}  {toks / total:>4.0%}  {bar}")
    print(f"  {'TOTAL':<13} {total:>6}  {total / budget:>4.0%} of budget"
          + ("   <- OVER BUDGET" if total > budget else ""))
    return breakdown, total


def main():
    breakdown, total = meter(MESSAGES, TOOLS, BUDGET)

    # The observability through-line starts here: one joinable line per measurement (§10 shape).
    session_id, trace_id = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    log_event(session_id, trace_id, 0, "context_meter", budget=BUDGET, total=total,
              fraction=round(total / BUDGET, 3), **breakdown)

    # OPT-IN: check the heuristic against the server's exact count.
    if not os.environ.get("OPENAI_BASE_URL"):
        print("\n(OPENAI_BASE_URL not set -- skipping the exact-count check; the heuristic above "
              "stands on its own.)")
        return
    exact = server_prompt_tokens(get_client(), MESSAGES, MODEL)
    err = (total - exact) / exact
    print(f"\nheuristic total: {total}   server exact: {exact}   off by {err:+.0%}")
    print("Use the heuristic for budgeting (fast, no API call); reach for the server when a "
          "decision must be precise.")


if __name__ == "__main__":
    main()
