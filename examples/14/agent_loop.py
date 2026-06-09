"""
Section 14 - The tool-use loop: one round trip becomes a loop.

Section 13 ran a tool once, by hand. Here we loop: call the model, run any tools
it asks for, feed the results back, and repeat until it stops asking for tools
(or we hit a step cap). That loop, with a few tools, is a mini-agent.

    python examples/14/agent_loop.py
"""

import ast
import json
import operator
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}


def calculate(expression: str) -> str:
    """Safe arithmetic (numbers + operators only); no eval (Section 17)."""
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported")
    return str(ev(ast.parse(expression, mode="eval").body))


def word_count(text: str) -> str:
    return str(len(text.split()))


# A registry: tool name -> the Python function that implements it.
TOOLS = {"calculate": calculate, "word_count": word_count}

# The schemas the model sees.
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "calculate", "description": "Evaluate an arithmetic expression.",
        "parameters": {"type": "object",
                       "properties": {"expression": {"type": "string"}},
                       "required": ["expression"]}}},
    {"type": "function", "function": {
        "name": "word_count", "description": "Count the words in a piece of text.",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string"}},
                       "required": ["text"]}}},
]


def run_agent(question: str, max_steps: int = 5) -> str:
    messages = [{"role": "user", "content": question}]
    for step in range(max_steps):
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto"
        )
        msg = response.choices[0].message

        if not msg.tool_calls:          # no tools requested -> the model is done
            return msg.content

        messages.append({
            "role": "assistant", "content": msg.content,
            "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            fn = TOOLS.get(tc.function.name)
            args = tc.function.arguments             # raw JSON string until parsed below
            try:
                args = json.loads(tc.function.arguments)
                result = fn(**args) if fn else f"error: unknown tool {tc.function.name}"
            except Exception as err:                 # tool errors go BACK to the model
                result = f"error: {err}"
            print(f"  [step {step}] {tc.function.name}({args}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    return "(stopped: reached max_steps)"


print(run_agent(
    "What is (12 + 5) * 3, and how many words are in 'the quick brown fox jumps'?"
))
