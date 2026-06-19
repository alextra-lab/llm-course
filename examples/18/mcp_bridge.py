"""
Section 18 - Bridge MCP tools into the Section 15 tool-use loop.

The server advertises tools; here we translate that list into the OpenAI tool
schema the model expects, run the Section 15 loop, and -- when the model asks for
a tool -- dispatch the call over MCP instead of to a local function. The model is
the only part that needs the endpoint, so without OPENAI_* set this skips cleanly.

    python examples/18/mcp_bridge.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
sys.path.append(str(Path(__file__).resolve().parent))      # this folder, for mcp_client
from mcp_client import MCPClient

SERVER = str(Path(__file__).resolve().parent / "mcp_server.py")


def to_openai_schema(mcp_tool):
    """An MCP tool advertisement -> the OpenAI function-tool schema (Section 14)."""
    return {"type": "function", "function": {
        "name": mcp_tool["name"],
        "description": mcp_tool["description"],
        "parameters": mcp_tool["inputSchema"],
    }}


def run_agent(client, model, mcp, question, max_steps=5):
    advertised = mcp.call("tools/list")["result"]["tools"]
    tool_schemas = [to_openai_schema(t) for t in advertised]
    names = {t["name"] for t in advertised}        # what the server actually offers
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
            if name not in names:        # never dispatch a tool the server didn't offer
                result = f"error: tool '{name}' not advertised by the server"
            else:
                args = json.loads(tc.function.arguments)
                reply = mcp.call("tools/call", {"name": name, "arguments": args})
                result = reply["result"]["content"][0]["text"]
            print(f"  [step {step}] {name} -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return "(stopped: reached max_steps)"


def main():
    if not (os.environ.get("OPENAI_BASE_URL") and os.environ.get("OPENAI_API_KEY")):
        print("OPENAI_BASE_URL / OPENAI_API_KEY not set -- skipping the model bridge.")
        print("The server and client (mcp_client.py) still run with no endpoint.")
        return
    from common import get_client, MODEL
    client = get_client()
    mcp = MCPClient(SERVER)
    try:
        answer = run_agent(client, MODEL, mcp,
                           "What is (12 + 5) * 3? Use the calculator tool.")
        print("\nfinal answer:", answer)
    finally:
        mcp.close()


if __name__ == "__main__":
    main()
