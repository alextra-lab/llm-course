"""
Section 17 - An MCP client: spawn a server, list its tools, call one.

This drives mcp_server.py over stdio: we send JSON-RPC requests on its stdin and
read responses from its stdout, one JSON object per line. No model and no endpoint
required -- this is the "consume an MCP server" half of the protocol, by hand.

    python examples/17/mcp_client.py
"""

import json
import subprocess
import sys
from pathlib import Path

SERVER = str(Path(__file__).resolve().parent / "mcp_server.py")


class MCPClient:
    """A minimal stdio MCP client: one request out, one response in."""

    def __init__(self, server_path):
        self.proc = subprocess.Popen(
            [sys.executable, server_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
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


def main():
    client = MCPClient(SERVER)
    try:
        init = client.call("initialize", {"protocolVersion": "2024-11-05"})
        print("connected to:", init["result"]["serverInfo"])

        tools = client.call("tools/list")["result"]["tools"]
        print("\ntools the server advertises:")
        for t in tools:
            print(f"  - {t['name']}: {t['description']}")

        print("\ncalculate(expression='(12 + 5) * 3'):")
        reply = client.call("tools/call",
                            {"name": "calculate",
                             "arguments": {"expression": "(12 + 5) * 3"}})
        print("  ->", reply["result"]["content"][0]["text"])

        print("\ndoc_search(query='what is mcp?'):")
        reply = client.call("tools/call",
                            {"name": "doc_search",
                             "arguments": {"query": "what is mcp?"}})
        print("  ->", reply["result"]["content"][0]["text"])
    finally:
        client.close()


if __name__ == "__main__":
    main()
