"""
Section 20 - Capstone: a small support assistant that ties the course together.

It composes:
  - retrieval (Section 15/16): a search_kb tool, using embeddings if EMBED_MODEL
    is set, otherwise a keyword fallback so it always runs;
  - tools + agent loop (Sections 13/14/18): search_kb + a safe calculator;
  - guardrails (Section 17): a tool registry, a safe (no-eval) calculator, a step cap;
  - observability + cost (Sections 9/10): per-call logging and a running cost total;
  - evaluation (Section 19): a tiny golden check at the end.

    python examples/20/capstone.py
"""

import ast
import json
import operator
import sys
import time
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL, EMBED_MODEL

client = get_client()

# --- cost + observability (Sections 9, 10) -------------------------------------
PRICE_INPUT, PRICE_OUTPUT = 0.15, 0.60        # USD per 1M tokens; set yours
TOTALS = {"calls": 0, "cost": 0.0}


def chat(**kwargs):
    start = time.perf_counter()
    r = client.chat.completions.create(**kwargs)
    u = r.usage
    TOTALS["calls"] += 1
    TOTALS["cost"] += u.prompt_tokens / 1e6 * PRICE_INPUT + u.completion_tokens / 1e6 * PRICE_OUTPUT
    print(f"  [llm] {u.total_tokens} tok, {round((time.perf_counter() - start) * 1000)}ms")
    return r


# --- knowledge base + retrieval tool (Sections 15, 16) -------------------------
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

    def search_kb(query: str) -> str:
        q = _embed([query])[0]
        sims = sorted(
            ((float(q @ _DOC_VECS[i] / (np.linalg.norm(q) * np.linalg.norm(_DOC_VECS[i]))), DOCS[i])
             for i in range(len(DOCS))), reverse=True)
        return "\n".join(d for _, d in sims[:2])
else:
    def search_kb(query: str) -> str:                 # keyword fallback
        words = query.lower().split()
        hits = [d for d in DOCS if any(w in d.lower() for w in words)]
        return "\n".join(hits[:2]) if hits else "no results"


# --- safe calculator tool (Section 17: no eval) --------------------------------
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


TOOLS = {"search_kb": search_kb, "calculate": calculate}
SCHEMAS = [
    {"type": "function", "function": {"name": "search_kb",
        "description": "Search the Acme knowledge base for company facts.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {"name": "calculate",
        "description": "Evaluate an arithmetic expression.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                       "required": ["expression"]}}},
]
SYSTEM = ("You are Acme's support assistant. Use search_kb for company facts and "
          "calculate for math. Rely only on tool results; if the answer isn't found, "
          "say you don't know. Keep answers to 1-2 sentences.")


# --- the agent loop (Sections 14, 18) ------------------------------------------
def agent(question: str, max_steps: int = 6) -> str:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": question}]
    for step in range(max_steps):
        msg = chat(model=MODEL, messages=messages, tools=SCHEMAS,
                   tool_choice="auto").choices[0].message
        if not msg.tool_calls:
            return msg.content
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            fn = TOOLS.get(tc.function.name)
            args = tc.function.arguments             # raw JSON string until parsed below
            try:
                args = json.loads(tc.function.arguments)
                result = fn(**args) if fn else f"error: unknown tool {tc.function.name}"
            except Exception as err:
                result = f"error: {err}"
            print(f"  [step {step}] {tc.function.name}({args}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return "(stopped: reached max_steps)"


if __name__ == "__main__":
    print("retrieval mode:", "embeddings" if EMBED_MODEL else "keyword fallback")

    for q in ["What's the return window, and how many weeks is that?",
              "Who founded Acme and where?"]:
        print(f"\nQ: {q}")
        print("A:", agent(q))

    # --- tiny evaluation (Section 19) ------------------------------------------
    print("\n--- eval ---")
    cases = [("How long is the warranty?", "2 year"),
             ("Which countries does Acme ship to?", "Canada")]
    for question, expected in cases:
        ok = expected.lower() in agent(question).lower()
        print(f"[{'PASS' if ok else 'FAIL'}] {question}")

    print(f"\nTotals: {TOTALS['calls']} model calls, ${TOTALS['cost']:.5f}")
