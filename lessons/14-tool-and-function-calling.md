---
title: 'Tool / Function Calling'
linkTitle: '14. Tool / Function Calling'
weight: 14
---

**Goal:** let the model call *your* code. You'll define a tool, watch the model ask to
use it (`tool_calls`), run the matching Python function, feed the result back as a `tool`
message, and get a final answer that uses it. This is the mechanic behind every "agent."

**Where this fits:** this is where the `tool` role from Section 1 finally appears, and
where Pydantic-style schemas from Section 7 pay off (tools are described with JSON
schemas). It's one round trip here; Section 15 turns it into a loop.

> **Your endpoint must support tool calling** (the OpenAI-style `tools` / `tool_choice`
> parameters). Not every server does — run `python scripts/preflight.py` to check yours. If
> `tool_calls` comes back empty in the examples, that's the likely cause — note it and read
> on; the mechanics are the same everywhere.

---

## The idea

A model can't run code or look things up — but it *can* tell you it wants to. Tool
calling is a structured handshake:

1. You send the question **plus a list of tools** (each a name + JSON-schema parameters).
2. The model either answers normally, or replies with a **`tool_calls`** request
   (`finish_reason == "tool_calls"`) naming a tool and arguments.
3. **You** run the real function and send the result back as a **`tool`** message.
4. The model answers using your result.

The model never runs anything. It only ever *asks*; you stay in control of what actually
executes.

---

## Build one round trip

We'll give the model a calculator. First, the real function — note we use a **safe**
arithmetic evaluator, not `eval` (Section 21 explains why that distinction matters once
the model is choosing the input). Create **`work/tool_call.py`**:

```python
import ast, json, operator
from common import get_client, MODEL

client = get_client()

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}

def calculate(expression: str) -> str:
    """Safely evaluate arithmetic: parse to AST, allow only numbers + operators."""
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported")
    return str(ev(ast.parse(expression, mode="eval").body))
```

Now describe the tool to the model — this is the JSON schema, just like Section 7:

```python
tools = [{
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "Evaluate a basic arithmetic expression and return the number.",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string",
                                          "description": "e.g. '2 * (3 + 4)'"}},
            "required": ["expression"],
        },
    },
}]
```

Send the question with the tools and see what comes back:

```python
messages = [{"role": "user", "content": "What is 23 * 17 + 5? Use the calculator."}]

first = client.chat.completions.create(
    model=MODEL, messages=messages, tools=tools, tool_choice="auto",
)
msg = first.choices[0].message
print("finish_reason:", first.choices[0].finish_reason)   # 'tool_calls'
print("tool_calls:", msg.tool_calls)
```

Run it:

```bash
python work/tool_call.py
```

The reply isn't an answer — it's a **request**. Each item in
`msg.tool_calls` has an `id`, the `function.name`, and `function.arguments` (a **JSON
string**, not a dict). Now complete the handshake — append the assistant's request, run
each tool, append a `tool` result for each, and ask again:

```python
messages.append({
    "role": "assistant",
    "content": msg.content,
    "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
})

for tc in msg.tool_calls:
    args = json.loads(tc.function.arguments)        # arguments are a JSON string
    result = calculate(**args)
    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

second = client.chat.completions.create(model=MODEL, messages=messages, tools=tools)
print("final answer:", second.choices[0].message.content)
```

Run again — now you get a sentence with the correct number. *(Reference:
[`examples/14/tool_call.py`](../examples/14/tool_call.py).)*

---

## The message sequence is the whole point

The conversation you build must contain, in order:

```
user        "What is 23 * 17 + 5?"
assistant   (content maybe empty)  + tool_calls=[{id: "call_1", name: "calculate", ...}]
tool        tool_call_id="call_1"  content="396"
assistant   "23 * 17 + 5 = 396."
```

Two rules people trip on:

- You **must append the assistant message that carried the `tool_calls`** before the
  `tool` results. The `tool` message is a *reply* to a specific request and is matched by
  `tool_call_id`.
- `function.arguments` is a **string of JSON** — always `json.loads` it before use, and
  validate it (Section 7) since the model chose it.

> **Generate schemas from Pydantic.** Instead of hand-writing the `parameters` schema,
> define a `BaseModel` and use `Model.model_json_schema()` (Section 7) as the tool's
> `parameters`. One definition gives you the schema *and* a validator for the arguments.

---

> **Security:** Tool arguments are chosen by the model, so treat them as untrusted: validate every one, and **never `eval`/`exec`** them. Running model-picked input is exactly what the sandboxing in Sections 16–17 makes safe.

## Challenges

1. **A second tool.** Add a `get_length(text: str)` tool that returns `len(text)`. Ask a
   question that needs it ("How many characters in 'hello world'?"). *Success:* the model
   calls the right tool.
2. **Validate the arguments.** Wrap the parsed `args` in a Pydantic model before calling
   `calculate`. *Success:* a malformed tool call is rejected by your code, not crashed on.
3. **Make it refuse.** Ask a question that needs *no* tool ("Who wrote Hamlet?").
   *Success:* `tool_calls` is empty and the model answers directly.

---

## Recap

- Tool calling is a handshake: you send `tools`; the model replies with **`tool_calls`**;
  you run the function and return a **`tool`** message; the model answers using it.
- The model only *asks* — your code decides what runs.
- Build the message sequence correctly: append the assistant's `tool_calls` message, then
  one `tool` message per call (matched by `tool_call_id`).
- `function.arguments` is a JSON **string** — `json.loads` and validate it (Section 7).

## Next

**Section 15 — The Tool-Use Loop:** one round trip becomes a loop — call, run tools,
feed results, repeat until the model is done. That loop is a mini-agent.
