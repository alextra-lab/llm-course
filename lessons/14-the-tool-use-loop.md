# Section 14 — The Tool-Use Loop

**Goal:** turn the single tool round trip from Section 13 into a **loop** — call the
model, run whatever tools it asks for, feed the results back, and repeat until it's done.
You'll build a small driver that can use several tools across multiple steps. That driver
*is* a mini-agent.

**Where this fits:** Section 13 gave you the handshake; here you automate it. This is the
core machinery that Section 22 (Agents) dresses up with planning and more tools.

> **Reminder — needs tool calling.** Like Section 13, this needs your endpoint to have
> **tool calling enabled** (vLLM auto tool choice). If `tool_calls` comes back empty,
> that's the cause — the loop logic still applies. See the README's "What your endpoint
> needs to support."

---

## Why a loop?

One round trip handles "use this one tool, then answer." But real tasks need more: the
model might call a tool, look at the result, then call *another* tool, then answer. Or
call the same tool several times. You can't know in advance how many steps it'll take —
so you **loop until the model stops asking for tools.**

The control flow:

```
loop:
    response = model(messages, tools)
    if no tool_calls:        ->  return the answer   (the model is done)
    else:
        append the assistant tool_calls message
        run each tool, append a tool result for each
        (loop again)
```

Add a **step cap** so a confused model can't loop forever.

---

## Build it

We'll give the agent two tools and let it figure out the steps. Create
**`work/agent_loop.py`**. Start with the tools and a registry mapping names to functions:

```python
import ast, json, operator
from common import get_client, MODEL

client = get_client()
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}

def calculate(expression: str) -> str:
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported")
    return str(ev(ast.parse(expression, mode="eval").body))

def word_count(text: str) -> str:
    return str(len(text.split()))

TOOLS = {"calculate": calculate, "word_count": word_count}
```

Describe both tools for the model:

```python
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "calculate", "description": "Evaluate an arithmetic expression.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                       "required": ["expression"]}}},
    {"type": "function", "function": {
        "name": "word_count", "description": "Count the words in a piece of text.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                       "required": ["text"]}}},
]
```

Now the loop — the heart of this section:

```python
def run_agent(question, max_steps=5):
    messages = [{"role": "user", "content": question}]
    for step in range(max_steps):
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto")
        msg = response.choices[0].message

        if not msg.tool_calls:                 # the model is done
            return msg.content

        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            fn = TOOLS.get(tc.function.name)
            args = tc.function.arguments       # raw JSON string until parsed below
            try:
                args = json.loads(tc.function.arguments)
                result = fn(**args) if fn else f"error: unknown tool {tc.function.name}"
            except Exception as err:           # send tool errors BACK to the model
                result = f"error: {err}"
            print(f"  [step {step}] {tc.function.name}({args}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return "(stopped: reached max_steps)"

print(run_agent("What is (12 + 5) * 3, and how many words are in 'the quick brown fox jumps'?"))
```

```bash
python work/agent_loop.py
```

Watch the `[step N]` lines: the model calls `calculate`, sees the result, calls
`word_count`, then writes a final answer that uses both. *(Reference:
[`examples/14/agent_loop.py`](../examples/14/agent_loop.py).)*

---

## Three details that make it robust

- **Always cap the steps.** A model that keeps re-calling tools (or loops on a tool that
  errors) will run forever and burn tokens. `max_steps` is your safety net.
- **Feed tool errors back, don't crash.** When a tool raises, return the error *as the
  tool result*. The model can read "error: …" and try a different approach — that's the
  loop's superpower.
- **Parallel tool calls.** One assistant turn can request several tools at once;
  `msg.tool_calls` is a list, so the `for tc in msg.tool_calls` loop already handles it.
  You append one `tool` message per call.

> **This is already an agent.** "Agent" mostly means *this loop* plus good tools, a
> guiding system prompt, and stop conditions. Section 22 adds planning and composes it
> with retrieval and memory — but the engine is what you just wrote.

---

> **Security:** A loop multiplies the blast radius — one bad turn can call a tool many times. Cap the steps, require confirmation for destructive actions, and run tool execution behind the isolation from Sections 15–16.

## Challenges

1. **Add a real tool.** Add `now()` returning the current date/time (use Python's
   `datetime`). Ask "What's today's date, and what is 7 * 8?" *Success:* the agent uses
   both tools.
2. **Watch it recover.** Ask `calculate` to divide by zero ("what is 5/0?"). *Success:*
   the error goes back to the model and it responds gracefully instead of crashing.
3. **Trip the cap.** Lower `max_steps` to 1 on a two-tool question. *Success:* it returns
   the "max_steps" message — proving why the cap matters.

---

## Recap

- Loop: call the model → if it requests tools, run them and append results → repeat until
  it stops asking → return the answer.
- A **registry** maps tool names to Python functions; one assistant turn can request
  several tools (a list).
- **Cap the steps**, and **return tool errors to the model** instead of crashing.
- This loop, with good tools and a system prompt, is the engine of an agent (Section 22).

## Next

**Section 15 — Sandboxing I:** the loop just ran model-chosen tools. The moment a tool
does something real — runs code, a shell, SQL — validating arguments isn't enough; you
need *isolation*. We build a portable sandbox (timeouts, resource limits, an allow-listed
shell tool) so executing untrusted actions stops being dangerous.
