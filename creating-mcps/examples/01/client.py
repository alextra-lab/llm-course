"""
Unit 1: drive the CMDB server with the SDK v2 Client, over stdio.

The Client starts the server as a child process, asks it `server/discover`, and then sends
self-contained requests: every one carries the protocol version, client info and client
capabilities in `_meta`. There is no handshake and no session. This script also stamps each
tool call with this course's joinable tuple (a W3C `traceparent`, `session_id`, `step`) so the
server's log lines can be joined back to the run that caused them.

    python examples/01/client.py                    # server log lines appear on stderr
    python examples/01/client.py --tap frames.jsonl # also record every frame (frame_tap.py)
"""

import argparse
import asyncio
import json
import secrets
import sys
import uuid
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.types import Implementation

HERE = Path(__file__).resolve().parent


def trace_meta(session_id: str, trace_id: str, step: int) -> dict:
    """The joinable tuple, carried in `_meta`. traceparent is the W3C standard the spec adopts;
    session_id and step are this course's own keys (Foundations §10)."""
    return {"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01",
            "session_id": session_id, "step": step}


async def main(tap: str | None) -> None:
    command = [sys.executable, str(HERE / "cmdb_server.py")]
    if tap:
        command = [sys.executable, str(HERE / "frame_tap.py"), tap, "--", *command]
    server = StdioServerParameters(command=command[0], args=command[1:])
    session_id, trace_id = uuid.uuid4().hex, uuid.uuid4().hex
    step = 0

    def meta():
        nonlocal step
        step += 1
        return trace_meta(session_id, trace_id, step)

    async with Client(server, client_info=Implementation(name="unit1-client", version="0.1")) as client:
        info = client.server_info
        print(f"protocol {client.protocol_version}, server {info.name} {info.version}")
        print(f"instructions: {client.instructions}")

        tools = await client.list_tools()
        print(f"tools: {[t.name for t in tools.tools]} "
              f"(ttlMs={tools.ttl_ms}, cacheScope={tools.cache_scope})")
        await client.list_tools()        # inside the TTL: answered from the client's cache

        r = await client.call_tool("get_asset", {"hostname": "jump-prod-02"}, meta=meta())
        print(f"get_asset -> {json.dumps(r.structured_content)}")

        r = await client.call_tool("get_asset", {"hostname": "jump-prod-2"}, meta=meta())
        print(f"get_asset (typo) -> isError={r.is_error}: {r.content[0].text}")

        r = await client.call_tool("find_assets", {"service": "customer-db", "environment": "prod",
                                                   "limit": 3}, meta=meta())
        page = r.structured_content
        print(f"find_assets -> {page['returned']} of {page['total']}: "
              f"{[a['hostname'] for a in page['assets']]}")

        res = await client.read_resource("cmdb://services/remote-access", meta=meta())
        print(f"cmdb://services/remote-access -> {res.contents[0].text}")

        prompt = await client.get_prompt("triage_host", {"hostname": "jump-prod-02"}, meta=meta())
        print(f"prompt triage_host -> {prompt.messages[0].content.text[:70]}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--tap", metavar="FRAMES.jsonl", help="record every frame to this file")
    asyncio.run(main(parser.parse_args().tap))
