---
title: 'Model Context Protocol (MCP)'
linkTitle: '18. Model Context Protocol (MCP)'
weight: 18
---

**Goal:** understand MCP as the *standard* way to expose and consume tools — so a set of
tools (and data sources) can live behind a server and be reused across many apps and
models, instead of being hand-wired into one program.

**Where this fits:** Sections 14–15 taught raw tool calling; Sections 16–17 made tool
*execution* safe to isolate. MCP is the layer on top: a common protocol for *connecting*
models to tools and data — think of it as a standard port (often described as "USB-C for
AI") rather than a new capability.

> **Reminder — the model bridge needs tool calling.** The server and client we build run
> with no endpoint at all. Only the last step — letting the model drive the tools — needs
> your endpoint with **tool calling** (OpenAI-style `tools` / `tool_choice`), exactly as in
> Sections 14–15.

---

## What MCP actually is

In Section 14 you wrote a tool schema, sent it to the model, and ran the matching Python
function yourself. Everything lived in one program. MCP asks a simple question: what if the
*tools* lived somewhere else — behind a server — so any app or model could discover and use
them without re-wiring them by hand?

That's the whole idea. MCP has three parts:

- a **server** that owns some tools (and data sources) and advertises them,
- a **client** that connects to the server and relays calls, and
- a **transport** between them — here, **stdio**: the client launches the server as a
  subprocess and they exchange messages over stdin/stdout.

The messages are **JSON-RPC 2.0**, one JSON object per line. There are only three you need
to start. First the client says hello with `initialize`:

```json
{"jsonrpc": "2.0", "id": 1, "method": "initialize",
 "params": {"protocolVersion": "2024-11-05"}}
```

Then it asks what tools exist with `tools/list` — and the answer is *exactly* the kind of
tool schema you already wrote in Section 14:

```json
{"jsonrpc": "2.0", "id": 2, "result": {"tools": [
  {"name": "calculate",
   "description": "Evaluate an arithmetic expression and return the number.",
   "inputSchema": {"type": "object",
                   "properties": {"expression": {"type": "string"}},
                   "required": ["expression"]}}]}}
```

And it runs one with `tools/call` — which is just "run the matching function," the work you
did by hand in Section 14:

```json
{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
 "params": {"name": "calculate", "arguments": {"expression": "(12 + 5) * 3"}}}
```

So MCP isn't a new capability. `tools/list` ≈ the schemas you send the model; `tools/call`
≈ running the function. MCP just puts a standard envelope around them so the tools can live
behind a server and be reused.

---

## Build a tiny MCP server

Let's make this concrete by hand-rolling a server — no library, just stdlib. Create
**`work/mcp_server.py`**. It exposes two tools: the Section 14/14 `calculate`, and a small
`doc_search` over an in-memory dict.

```python
import ast, json, operator, sys

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}

def calculate(expression: str) -> str:
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported expression")
    return str(ev(ast.parse(expression, mode="eval").body))
```

A trivial second tool, plus the registry the server advertises — each entry pairs the
function with the schema a client (or model) will see:

```python
_DOCS = {"mcp": "MCP connects models to tools and data.",
         "sandbox": "A sandbox isolates untrusted code from the host."}

def doc_search(query: str) -> str:
    for key, text in _DOCS.items():
        if key in query.lower():
            return text
    return "no matching document"

TOOLS = {
    "calculate": (calculate, {
        "name": "calculate",
        "description": "Evaluate an arithmetic expression and return the number.",
        "inputSchema": {"type": "object",
                        "properties": {"expression": {"type": "string"}},
                        "required": ["expression"]}}),
    "doc_search": (doc_search, {
        "name": "doc_search",
        "description": "Look up a short definition for a topic.",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"]}}),
}
```

Now the heart: turn one JSON-RPC request into a response. This is just a dispatch on
`method` — the three we saw above:

```python
def handle(request):
    method, req_id = request.get("method"), request.get("id")
    if req_id is None:                 # a notification (no id) -- nothing to reply
        return None

    def ok(result): return {"jsonrpc": "2.0", "id": req_id, "result": result}
    def err(code, msg): return {"jsonrpc": "2.0", "id": req_id,
                                "error": {"code": code, "message": msg}}

    if method == "initialize":
        return ok({"protocolVersion": "2024-11-05",
                   "serverInfo": {"name": "course-mcp", "version": "0.1"},
                   "capabilities": {"tools": {}}})
    if method == "tools/list":
        return ok({"tools": [schema for _, schema in TOOLS.values()]})
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict):
            return err(-32602, "params must be an object")
        entry = TOOLS.get(params.get("name"))
        if entry is None:
            return err(-32602, f"unknown tool: {params.get('name')}")
        try:                           # a tool error is a result, not a crashed server
            text = entry[0](**(params.get("arguments") or {}))
        except Exception as e:
            return ok({"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
        return ok({"content": [{"type": "text", "text": str(text)}]})
    return err(-32601, f"method not found: {method}")
```

Finally the transport loop — read a line, dispatch, write a line:

```python
def main():
    for line in sys.stdin:             # one JSON object per line; EOF ends the loop
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue
        response = handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
```

It needs no model and no endpoint. Run it directly with empty stdin and it exits cleanly at
EOF:

```bash
printf '' | python work/mcp_server.py    # clean exit; it's meant to be spawned
```

> **What we left out.** This is *tools-only* MCP, kept minimal to show the shape. Real MCP
> also exposes **resources** and **prompts**, negotiates the protocol version, and returns
> proper JSON-RPC parse errors; our server silently skips malformed lines and treats a
> missing `id` as a notification. Enough to be honest about what MCP *is*, not a spec-complete
> implementation. *(Reference: [`examples/18/mcp_server.py`](../examples/18/mcp_server.py).)*

---

## Drive it with a client

A client launches the server as a subprocess and talks JSON-RPC to it. Create
**`work/mcp_client.py`** — the "consume an MCP server" half, still with no model in sight.

```python
import json, subprocess, sys
from pathlib import Path

SERVER = str(Path(__file__).resolve().parent / "mcp_server.py")

class MCPClient:
    def __init__(self, server_path):
        self.proc = subprocess.Popen([sys.executable, server_path],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        request = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            request["params"] = params
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)
```

Now use it — initialize, list, call:

```python
def main():
    client = MCPClient(SERVER)
    try:
        init = client.call("initialize", {"protocolVersion": "2024-11-05"})
        print("connected to:", init["result"]["serverInfo"])
        for t in client.call("tools/list")["result"]["tools"]:
            print(f"  - {t['name']}: {t['description']}")
        reply = client.call("tools/call",
                            {"name": "calculate", "arguments": {"expression": "(12 + 5) * 3"}})
        print("calculate ->", reply["result"]["content"][0]["text"])
    finally:
        client.close()

if __name__ == "__main__":
    main()
```

```bash
python work/mcp_client.py
```

You'll see the server's two tools listed and `calculate ->  51`. No endpoint touched —
discovering and calling MCP tools is pure plumbing. *(Reference:
[`examples/18/mcp_client.py`](../examples/18/mcp_client.py).)*

---

## Bridge MCP tools into the tool-use loop

The payoff: let the *model* use the server's tools. The Section 15 loop already does the
hard part — we only change two things. First, build the model's tool schemas from the
server's `tools/list` instead of writing them by hand. Second, when the model asks for a
tool, dispatch the call over MCP (`tools/call`) instead of to a local function.

Create **`work/mcp_bridge.py`**. Reuse the client we just built, and translate one MCP
advertisement into the OpenAI schema from Section 14:

```python
import json, os, sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
sys.path.append(str(Path(__file__).resolve().parent))      # this folder, for mcp_client
from mcp_client import MCPClient

SERVER = str(Path(__file__).resolve().parent / "mcp_server.py")

def to_openai_schema(mcp_tool):
    return {"type": "function", "function": {
        "name": mcp_tool["name"], "description": mcp_tool["description"],
        "parameters": mcp_tool["inputSchema"]}}
```

The loop is Section 15, with the tool list discovered over MCP and each call dispatched to
the server. Note the guard: we never dispatch a name the server didn't advertise — the
server is a trust boundary, and the model's chosen name is untrusted:

```python
def run_agent(client, model, mcp, question, max_steps=5):
    advertised = mcp.call("tools/list")["result"]["tools"]
    tool_schemas = [to_openai_schema(t) for t in advertised]
    names = {t["name"] for t in advertised}
    messages = [{"role": "user", "content": question}]
    for step in range(max_steps):
        response = client.chat.completions.create(
            model=model, messages=messages, tools=tool_schemas, tool_choice="auto")
        msg = response.choices[0].message
        if not msg.tool_calls:
            return msg.content
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            name = tc.function.name
            if name not in names:        # reject tools the server didn't offer
                result = f"error: tool '{name}' not advertised by the server"
            else:
                args = json.loads(tc.function.arguments)
                reply = mcp.call("tools/call", {"name": name, "arguments": args})
                result = reply["result"]["content"][0]["text"]
            print(f"  [step {step}] {name} -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return "(stopped: reached max_steps)"
```

The model is the only part that needs the endpoint, so guard it and degrade gracefully —
exactly the pattern from Section 17:

```python
def main():
    if not (os.environ.get("OPENAI_BASE_URL") and os.environ.get("OPENAI_API_KEY")):
        print("OPENAI_* not set -- skipping the model bridge (server + client still run).")
        return
    from common import get_client, MODEL
    client = get_client()
    mcp = MCPClient(SERVER)
    try:
        print(run_agent(client, MODEL, mcp, "What is (12 + 5) * 3? Use the calculator tool."))
    finally:
        mcp.close()

if __name__ == "__main__":
    main()
```

```bash
python work/mcp_bridge.py
```

With creds, the model calls `calculate` *over MCP* and answers `51`. Notice what didn't
change: the loop. Swap the server and the same loop drives a different set of tools — that's
the reuse MCP buys you. *(Reference: [`examples/18/mcp_bridge.py`](../examples/18/mcp_bridge.py).)*

---

## When MCP earns its keep

MCP is worth it when tools outlive one program: a calculator, a document search, a database
gateway you want **reused across many apps**; a **third-party ecosystem** of servers you can
plug in without writing glue; or a clean **separation of concerns** where one team owns the
tools and another owns the app. For a single script with two local functions (Section 15),
MCP is overkill — calling the functions directly is simpler and you should.

In production you wouldn't hand-roll the protocol: reach for the official **`mcp` Python
SDK**, which handles the transport, schema validation, auth, and the resources/prompts we
skipped. We built it by hand here for the same reason Section 1 built raw HTTP before the
SDK — so you know what the library is doing for you.

> **Security:** an MCP server is third-party code at the end of a connection. Treat its tool
> list, its arguments, and its outputs as untrusted (Section 21), scope its credentials
> narrowly, and execute anything it runs behind the isolation from Sections 16–17.

## Challenges

1. **A third tool.** Add a `get_length(text)` tool to the server (function + registry
   entry). Run `mcp_client.py` *without touching it*. *Success:* the new tool shows up in
   `tools/list` and a `tools/call` returns its result.
2. **Reject an unadvertised tool.** The bridge already guards against names the server never
   offered. Prove it: synthesize a fake tool call for a made-up name (e.g. feed
   `run_agent` a question that tempts a nonexistent tool, or call the dispatch path with a
   bogus name). *Success:* the bogus call returns an `error: … not advertised` result to the
   model instead of being dispatched over MCP.
3. **Sandbox a dangerous tool.** Add a `run_python(code)` tool to the server that runs the
   code behind the Section 16 sandbox (`run_untrusted`). Call it with an infinite loop.
   *Success:* the timeout kills it and the server returns an error result — the host is
   never at risk.

## Recap

- MCP standardizes *connecting* models to tools and data — `tools/list` ≈ the schemas you
  already send, `tools/call` ≈ running the function; it builds on tool calling (13–14), not
  replaces it.
- The transport is plain JSON-RPC over stdio: a server, a client, and line-delimited
  messages. The server and client run with no endpoint; only the model bridge needs one.
- A server is a trust boundary: scope credentials, reject tools it didn't advertise,
  validate I/O (Section 21), and sandbox anything it runs (Sections 16–17).
- Hand-rolling shows what MCP *is*; in production use the official `mcp` SDK.

## Next

**Section 19 — Embeddings:** we switch from *acting* to *meaning* — turning text into
vectors with the embeddings endpoint and measuring similarity by hand, the foundation for
retrieval.
