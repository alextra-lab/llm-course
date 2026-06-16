# Section 22 — Agents

**Goal:** assemble the pieces you've built into an **agent** — the tool loop (Section 14)
given a goal, a system prompt that makes it plan, and several tools (including search) it
can use across multiple steps. You'll see that "agent" isn't a new technology; it's
composition.

**Where this fits:** this is where the advanced arc converges. Tools (13–14), retrieval
(19), memory (12), and guardrails (20) come together. After this you can read any "agent
framework" and recognize the engine underneath.

> **Reminder — needs tool calling.** The agent is built on the tool loop, so your endpoint
> must have **tool calling enabled** (vLLM auto tool choice). If `tool_calls` is always
> empty, that's why. See the README's "What your endpoint needs to support."

---

## What an agent actually is

Strip away the buzzword and an agent is:

> **a loop** (Section 14) **+ a goal + tools + a system prompt that says "plan, then act"
> + stop conditions.**

The model reasons about the task, picks a tool, sees the result, reasons again, picks the
next tool, and eventually decides it's done and answers. This reason→act→observe cycle is
often called **ReAct**. You already wrote the loop; an agent just gives it direction and
more capable tools.

---

## Build an agent

We'll give it two tools — a document **search** and the **calculator** — and a task that
needs both, in sequence. Create **`work/agent.py`**. Tools first (search is keyword-based
here so it runs without an embedding model; in production this would be RAG from
Section 19):

```python
import ast, json, operator
from common import get_client, MODEL

client = get_client()

DOCS = [
    "The Acme widget weighs 1.2 kilograms.",
    "Acme Corp ships to the US and Canada only.",
    "Acme Corp's warranty covers defects for 2 years.",
]
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}

def calculate(expression):
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported")
    return str(ev(ast.parse(expression, mode="eval").body))

def search_docs(query):
    words = query.lower().split()
    hits = [d for d in DOCS if any(w in d.lower() for w in words)]
    return "\n".join(hits) if hits else "no results"

TOOLS = {"calculate": calculate, "search_docs": search_docs}
```

The schemas and a **planning system prompt** — this is what turns the loop into an agent:

```python
SCHEMAS = [
    {"type": "function", "function": {"name": "search_docs",
        "description": "Search the company knowledge base.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {"name": "calculate",
        "description": "Evaluate an arithmetic expression.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                       "required": ["expression"]}}},
]

SYSTEM = ("You are a research agent. Break the task into steps. Use search_docs to look "
          "up facts and calculate for arithmetic. Rely only on tool results -- do not "
          "invent facts. When you have enough information, give a short final answer.")
```

Now the loop — the same engine from Section 14, with the system prompt and registry:

```python
def run_agent(task, max_steps=6):
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    for step in range(max_steps):
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=SCHEMAS, tool_choice="auto")
        msg = response.choices[0].message
        if not msg.tool_calls:
            return msg.content
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            fn = TOOLS.get(tc.function.name)             # only known tools run
            args = tc.function.arguments                 # raw JSON string until parsed below
            try:
                args = json.loads(tc.function.arguments)
                result = fn(**args) if fn else f"error: unknown tool {tc.function.name}"
            except Exception as err:
                result = f"error: {err}"
            print(f"  [step {step}] {tc.function.name}({args}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return "(stopped: reached max_steps)"

print(run_agent("How much do 3 Acme widgets weigh in total, in kilograms?"))
```

```bash
python work/agent.py
```

Watch the steps: the agent `search_docs`-es for the widget weight, reads `1.2 kg`,
`calculate`s `3 * 1.2`, and answers `3.6 kg`. It planned a two-step path you didn't
hard-code. *(Reference: [`examples/22/agent.py`](../examples/22/agent.py).)*

---

## What makes agents reliable (and what doesn't)

Agents are powerful but failure-prone — they take multiple model calls, and an early
mistake compounds. The habits that keep them sane are ones you've already met:

- **Stop conditions.** A `max_steps` cap (and ideally a token/cost budget, Section 10) so
  a confused agent can't loop forever.
- **Validated tools + least privilege** (Section 20). The agent chooses tool arguments —
  validate them, allowlist tools (the registry does this), and gate destructive actions.
- **Errors as feedback.** Returning tool errors to the model lets it recover; crashing
  doesn't.
- **Memory management** (Section 12). Long agent runs build long histories — window or
  summarize.
- **Observability** (Section 9). Log each step; agents are much easier to debug when you
  can see the tool calls.

> **Do you even need an agent?** Agents shine when the steps *aren't* known in advance. If
> you already know the sequence ("retrieve, then summarize"), a plain pipeline is cheaper,
> faster, and more predictable. Reach for an agent when the path genuinely depends on what
> the model finds along the way.

> **Frameworks.** LangChain, LlamaIndex, the OpenAI Agents SDK and others package this
> loop with extras. You now understand the engine they're wrapping — which makes them a
> convenience, not a black box.

---

> **Security:** An agent compounds every earlier risk. Give it least-privilege tools, keep a human in the loop for irreversible actions, and run all tool execution inside the sandbox (Sections 15–16).

## Challenges

1. **Add a tool, watch it plan.** Add a `convert_kg_to_lb` tool and ask "How much do 3
   widgets weigh in pounds?" *Success:* the agent chains search → calculate/convert.
2. **Make it decline.** Ask something the docs don't cover. *Success:* with the "rely
   only on tool results" instruction, it says it doesn't know rather than inventing.
3. **Budget it.** Add a running token counter (sum `usage`, Section 10) and stop the
   agent when it exceeds a cap. *Success:* the agent halts on budget, not just step count.

---

## Recap

- An **agent** = the tool loop + a goal + tools + a "plan then act" system prompt + stop
  conditions (the **ReAct** cycle).
- It composes everything: tools (13–14), retrieval (19), memory (12), guardrails (20),
  observability (9), cost control (10).
- Reliability comes from **caps, validated/least-privilege tools, errors-as-feedback, and
  logging** — not from trusting the model.
- Prefer a plain pipeline when the steps are known; use an agent when the path is
  data-dependent.

## Next

**Section 23 — Evaluation & Testing:** an agent that *sometimes* works isn't done. We'll
measure quality with golden tests and an LLM-as-judge so you can tell whether changes
help or hurt.
