"""
Section 13 - Tool calling: let the model call YOUR code.

You describe a tool (name + JSON-schema parameters). The model, instead of
answering, can reply with a `tool_calls` request. You run the matching Python
function, hand the result back as a `tool` message, and ask again -- now the
model answers using your result.

This is ONE round trip done by hand. Section 14 turns it into a loop.

    python examples/13/tool_call.py

Requires the endpoint to have tool-calling enabled (vLLM auto tool choice). If
tool_calls comes back empty, your endpoint may not support it.
"""

import ast
import json
import operator
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


# --- 1. The actual Python function the model may call --------------------------
def calculate(expression: str) -> str:
    # A SAFE arithmetic evaluator: we parse to an AST and only allow numbers and
    # math operators -- no names, calls, or attributes. We deliberately avoid
    # eval() on model-provided text; Section 17 explains why that matters.
    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("unsupported expression")

    return str(ev(ast.parse(expression, mode="eval").body))


# --- 2. The tool's description the model sees ----------------------------------
tools = [{
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "Evaluate a basic arithmetic expression and return the number.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "e.g. '2 * (3 + 4)'"}
            },
            "required": ["expression"],
        },
    },
}]

messages = [{"role": "user", "content": "What is 23 * 17 + 5? Use the calculator."}]

# --- 3. First call: the model decides to call the tool -------------------------
first = client.chat.completions.create(
    model=MODEL, messages=messages, tools=tools, tool_choice="auto"
)
msg = first.choices[0].message
print("finish_reason:", first.choices[0].finish_reason)  # likely 'tool_calls'

if not msg.tool_calls:
    print("No tool call. Model answered directly:", msg.content)
    raise SystemExit

# --- 4. Record the assistant's request, then run each tool ---------------------
messages.append({
    "role": "assistant",
    "content": msg.content,
    "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
})

for tc in msg.tool_calls:
    args = json.loads(tc.function.arguments)        # arguments arrive as a JSON string
    result = calculate(**args)
    print(f"  tool {tc.function.name}({args}) -> {result}")
    messages.append({
        "role": "tool",
        "tool_call_id": tc.id,                      # ties the result to the request
        "content": result,
    })

# --- 5. Second call: the model answers using the tool result -------------------
second = client.chat.completions.create(model=MODEL, messages=messages, tools=tools)
print("\nfinal answer:", second.choices[0].message.content)
