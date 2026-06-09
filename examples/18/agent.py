"""
Section 18 - An agent: the tool loop + a goal + planning + multiple tools.

This is Section 14's loop, given a system prompt that tells the model to plan and
use tools, plus a second tool (a document search). The model decides which tools
to call, in what order, across several steps, then answers. We keep the safety
habits from Section 17: a tool registry (only known tools run), validated/typed
arguments, tool errors returned to the model, and a hard step cap.

    python examples/18/agent.py
"""

import ast
import json
import operator
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

# A small knowledge base. (A real agent would search this with RAG -- Section 16.
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


def run_agent(task: str, max_steps: int = 6) -> str:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": task}]
    for step in range(max_steps):
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=SCHEMAS, tool_choice="auto"
        )
        msg = response.choices[0].message
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
            print(f"  [step {step}] {tc.function.name}({args}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return "(stopped: reached max_steps)"


# Needs TWO tools in sequence: search for the widget weight, then multiply by 3.
print(run_agent("How much do 3 Acme widgets weigh in total, in kilograms?"))
