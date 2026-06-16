# Section 24 — Capstone

**Goal:** build one small, real application that ties the whole course together — a
company support assistant that **retrieves** facts, **uses tools**, runs an **agent
loop**, stays within **guardrails**, **logs** itself, **tracks cost**, and is checked by
an **eval**. Nothing here is new; it's assembly. By the end you'll have a program that
exercises every section.

**Where this fits:** the finish line. Sections 1–23 each added one capability; this
section composes them into something you'd actually ship a v0 of.

> **Reminder — endpoint features.** The capstone uses **tool calling** (Section 13; vLLM
> auto tool choice) and, for retrieval, an **embedding model** (`EMBED_MODEL`, Section 18)
> — it falls back to keyword search if that's unset. See the README's "What your endpoint
> needs to support" for the full list.

---

## What we're building

An "Acme support assistant" that answers customer questions about a (made-up) company:

```
question ─▶ agent loop ─▶ [ search_kb tool ]  (retrieval, §18/19)
                         [ calculate tool ]   (tools, §13/14)
                              │
                         every model call is logged (§9) and costed (§10)
                              │
                         final answer (grounded; "I don't know" if unknown, §19/20)

then: a tiny eval suite scores it (§23)
```

Every arrow is something you already built. The capstone is wiring them together.

---

## Build it, piece by piece

Create **`work/capstone.py`**. Bring in each capability in turn — this is a tour of the
course in one file.

**1. Cost + observability (§9, §10)** — wrap the chat call so every call is logged and
its cost accumulated:

```python
import ast, json, operator, time
import numpy as np
from common import get_client, MODEL, EMBED_MODEL

client = get_client()
PRICE_INPUT, PRICE_OUTPUT = 0.15, 0.60        # USD per 1M tokens; set yours
TOTALS = {"calls": 0, "cost": 0.0}

def chat(**kwargs):
    start = time.perf_counter()
    r = client.chat.completions.create(**kwargs)
    u = r.usage
    TOTALS["calls"] += 1
    TOTALS["cost"] += u.prompt_tokens/1e6*PRICE_INPUT + u.completion_tokens/1e6*PRICE_OUTPUT
    print(f"  [llm] {u.total_tokens} tok, {round((time.perf_counter()-start)*1000)}ms")
    return r
```

**2. Retrieval tool (§18, §19)** — search the knowledge base. Use embeddings if
`EMBED_MODEL` is set, otherwise fall back to keyword search so it always runs:

```python
DOCS = [
    "Acme Corp's return policy allows returns within 30 days with a receipt.",
    "Acme Corp was founded in 1987 in Portland, Oregon.",
    "Acme Corp's warranty covers manufacturing defects for 2 years.",
    "Acme Corp ships to the US and Canada only.",
    "The Acme widget weighs 1.2 kilograms and comes in blue or red.",
]

if EMBED_MODEL:
    def _embed(texts):
        r = client.embeddings.create(model=EMBED_MODEL, input=texts)
        return np.array([d.embedding for d in r.data])
    _DOC_VECS = _embed(DOCS)
    def search_kb(query):
        q = _embed([query])[0]
        sims = sorted(((float(q @ _DOC_VECS[i] / (np.linalg.norm(q)*np.linalg.norm(_DOC_VECS[i]))), DOCS[i])
                       for i in range(len(DOCS))), reverse=True)
        return "\n".join(d for _, d in sims[:2])
else:
    def search_kb(query):
        words = query.lower().split()
        hits = [d for d in DOCS if any(w in d.lower() for w in words)]
        return "\n".join(hits[:2]) if hits else "no results"
```

**3. A safe tool + registry (§13, §20)** — the calculator, with the no-`eval` parser, and
a registry so only known tools run:

```python
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}
def calculate(expression):
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported")
    return str(ev(ast.parse(expression, mode="eval").body))

TOOLS = {"search_kb": search_kb, "calculate": calculate}
```

**4. The agent loop (§14, §22)** — the same engine, now using `chat()` so every step is
logged and costed, with a `max_steps` guard:

```python
SCHEMAS = [
    {"type": "function", "function": {"name": "search_kb",
        "description": "Search the Acme knowledge base for company facts.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "calculate",
        "description": "Evaluate an arithmetic expression.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
]
SYSTEM = ("You are Acme's support assistant. Use search_kb for company facts and calculate "
          "for math. Rely only on tool results; if the answer isn't found, say you don't "
          "know. Keep answers to 1-2 sentences.")

def agent(question, max_steps=6):
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    for step in range(max_steps):
        msg = chat(model=MODEL, messages=messages, tools=SCHEMAS, tool_choice="auto").choices[0].message
        if not msg.tool_calls:
            return msg.content
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            fn = TOOLS.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments)
                result = fn(**args) if fn else f"error: unknown tool {tc.function.name}"
            except Exception as err:
                result = f"error: {err}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return "(stopped: reached max_steps)"
```

**5. Run it, then evaluate it (§23)**:

```python
for q in ["What's the return window, and how many weeks is that?", "Who founded Acme and where?"]:
    print(f"\nQ: {q}\nA: {agent(q)}")

cases = [("How long is the warranty?", "2 year"), ("Which countries does Acme ship to?", "Canada")]
for question, expected in cases:
    ok = expected.lower() in agent(question).lower()
    print(f"[{'PASS' if ok else 'FAIL'}] {question}")

print(f"\nTotals: {TOTALS['calls']} model calls, ${TOTALS['cost']:.5f}")
```

```bash
python work/capstone.py
```

You'll watch it search, calculate, answer, pass its eval, and report what it cost — a
complete loop from question to grounded, measured answer. *(Reference:
[`examples/24/capstone.py`](../examples/24/capstone.py).)*

---

> **Security:** This is where it all lands — validated tool arguments, sandboxed execution (Sections 15–16), delimited untrusted content, and an audit trail. A capstone that's correct but unsafe isn't done.

## Make it yours — extension challenges

This v0 is deliberately minimal. Pick a few:

1. **Real RAG with chunking.** Replace `DOCS` with a real document split into chunks
   (Section 19), and set `EMBED_MODEL` so retrieval is semantic.
2. **Memory.** Wrap `agent()` in a conversation loop (Section 12) that keeps history and
   windows/summarizes it, so it handles follow-up questions.
3. **A guardrail with teeth.** Add a `create_ticket(summary)` tool that requires
   confirmation before "acting" (Section 20), and validate its arguments with Pydantic
   (Section 6).
4. **A budget stop.** Halt the agent when `TOTALS["cost"]` exceeds a cap (Section 10).
5. **Grow the eval.** Turn the two cases into a real golden suite plus an LLM-as-judge for
   answer quality (Section 23), and run it before/after each change.

---

## You made it — what you can now do

Across twenty-four sections you built, from primitives, the ability to:

- **Talk to a model** over raw HTTP and the SDK, and understand the messages↔template
  layer (§1–2).
- **Control and understand output** — tokens, the context window, sampling, reasoning
  (§3–5).
- **Make output trustworthy** — structured output with validation, streaming, robustness
  (§6–8).
- **Run it responsibly** — observability and cost/caching (§9–10).
- **Build real applications** — prompt engineering, conversation memory, tools, the tool
  loop, embeddings, RAG, security, agents, and evaluation (§11–23).
- **Compose it all** into a working app — and you wrote every line yourself.

From here, the same primitives scale: swap the toy knowledge base for a vector database,
the two tools for a dozen, the keyword search for production RAG, and the print-logging
for a real observability stack. The shapes don't change — you already understand them.

> **Reference:** the complete app is
> [`examples/24/capstone.py`](../examples/24/capstone.py). Read it once you've built your
> own — then make it yours.
