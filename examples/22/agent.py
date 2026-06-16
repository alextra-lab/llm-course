"""
Section 22 - An agent: the tool loop + a goal + planning + multiple tools.

This is Section 14's loop, given a system prompt that tells the model to plan and
use tools, plus a second tool (a document search). The model decides which tools
to call, in what order, across several steps, then answers. We keep the safety
habits from Section 20: a tool registry (only known tools run), validated/typed
arguments, tool errors returned to the model, and a hard step cap.

It also carries Section 9's joinable telemetry into the agent: every model call
and tool result is logged with a shared trace_id (one per run) and a step index,
and the caller passes a session_id that can span several runs. One grep on a
trace_id replays the whole run, in order. Hitting the step cap is logged as a
loud "run_degraded" event -- a silent fallback that looks like success is the
failure mode you can't debug.

    python examples/22/agent.py
"""

import ast
import json
import logging
import operator
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

# JSONL telemetry to stdout (redirect it to a file); the final answer to stderr.
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
log = logging.getLogger("agent")

client = get_client()

# A small knowledge base. (A real agent would search this with RAG -- Section 19.
# We use a simple keyword search so this runs without an embedding model.)
DOCS = [
    "The Acme widget weighs 1.2 kilograms.",
    "Acme Corp ships to the US and Canada only.",
    "Acme Corp's warranty covers defects for 2 years.",
]

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}


def calculate(expression: str) -> str:
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported")
    return str(ev(ast.parse(expression, mode="eval").body))


def search_docs(query: str) -> str:
    words = query.lower().split()
    hits = [d for d in DOCS if any(w in d.lower() for w in words)]
    return "\n".join(hits) if hits else "no results"


TOOLS = {"calculate": calculate, "search_docs": search_docs}
SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_docs", "description": "Search the company knowledge base.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "calculate", "description": "Evaluate an arithmetic expression.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                       "required": ["expression"]}}},
]

SYSTEM = (
    "You are a research agent. Break the task into steps. Use search_docs to look up "
    "facts and calculate for arithmetic. Rely only on tool results -- do not invent "
    "facts. When you have enough information, give a short final answer."
)


def run_agent(task: str, session_id: str, max_steps: int = 6) -> str:
    trace_id = uuid.uuid4().hex[:8]                 # one trace per agent run
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": task}]
    for step in range(max_steps):
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=SCHEMAS, tool_choice="auto"
        )
        msg = response.choices[0].message
        log.info(json.dumps({
            "event": "model_call", "session_id": session_id, "trace_id": trace_id,
            "step": step,
            "tool_calls": [tc.function.name for tc in (msg.tool_calls or [])],
            "completion_tokens": response.usage.completion_tokens if response.usage else None,
        }))
        if not msg.tool_calls:
            return msg.content

        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            fn = TOOLS.get(tc.function.name)        # registry => only known tools run
            args = tc.function.arguments            # raw JSON string until parsed below
            try:
                args = json.loads(tc.function.arguments)
                result = fn(**args) if fn else f"error: unknown tool {tc.function.name}"
            except Exception as err:
                result = f"error: {err}"
            log.info(json.dumps({
                "event": "tool_call", "session_id": session_id, "trace_id": trace_id,
                "step": step, "tool": tc.function.name, "args": args,
                "result": str(result)[:120],
            }))
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    # Hit the cap -- log the degradation loudly; don't return a "fine" answer silently.
    log.info(json.dumps({
        "event": "run_degraded", "session_id": session_id, "trace_id": trace_id,
        "reason": "max_steps", "max_steps": max_steps,
    }))
    return "(stopped: reached max_steps)"


# Needs TWO tools in sequence: search for the widget weight, then multiply by 3.
session_id = uuid.uuid4().hex[:8]
answer = run_agent("How much do 3 Acme widgets weigh in total, in kilograms?", session_id)
print(answer, file=sys.stderr)
