"""
Section 18 - A tiny MCP server over stdio (JSON-RPC 2.0).

MCP isn't magic: a server reads JSON-RPC requests on stdin and writes responses
on stdout, one JSON object per line. This server advertises two tools -- the
Section 14/15 `calculate` and a small `doc_search` -- and runs them on request.
It needs no model and no endpoint; it's meant to be spawned by a client (see
mcp_client.py). Run it directly with empty stdin and it exits cleanly at EOF.

    python examples/18/mcp_client.py              # the normal way to drive it
    printf '' | python examples/18/mcp_server.py  # clean EOF exit
"""

import ast
import json
import operator
import sys

# --- the tools this server exposes (reused from Sections 14-15) ----------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}


def calculate(expression: str) -> str:
    """Safe arithmetic (numbers + operators only); no eval (Section 21)."""
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported expression")
    return str(ev(ast.parse(expression, mode="eval").body))


_DOCS = {
    "mcp": "MCP is a standard protocol for connecting models to tools and data.",
    "sandbox": "A sandbox isolates untrusted code so it can't harm the host.",
    "embeddings": "Embeddings turn text into vectors so you can measure meaning.",
}


def doc_search(query: str) -> str:
    """Return the first stored doc whose topic key appears in the query."""
    q = query.lower()
    for key, text in _DOCS.items():
        if key in q:
            return text
    return "no matching document"


# tool name -> (function, the JSON-schema advertisement the client/model sees)
TOOLS = {
    "calculate": (calculate, {
        "name": "calculate",
        "description": "Evaluate an arithmetic expression and return the number.",
        "inputSchema": {"type": "object",
                        "properties": {"expression": {"type": "string"}},
                        "required": ["expression"]},
    }),
    "doc_search": (doc_search, {
        "name": "doc_search",
        "description": "Look up a short definition for a topic.",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"]},
    }),
}


def handle(request):
    """Map one JSON-RPC request to a response dict (or None for a notification)."""
    method = request.get("method")
    req_id = request.get("id")
    if req_id is None:           # a notification (no id) -- nothing to reply
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": code, "message": message}}

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
        fn = entry[0]
        args = params.get("arguments") or {}
        try:                     # a tool error is a result, not a crashed server
            text = fn(**args)
        except Exception as e:
            return ok({"content": [{"type": "text", "text": f"error: {e}"}],
                       "isError": True})
        return ok({"content": [{"type": "text", "text": str(text)}]})
    return err(-32601, f"method not found: {method}")


def main():
    # One JSON object per line in, one per line out -- the stdio transport.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):   # ignore valid-but-non-object JSON
            continue
        response = handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
