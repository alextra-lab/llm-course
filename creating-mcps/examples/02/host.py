"""
Unit 2 reference solution: a host -- the application that puts MCP servers in front of a model.

It starts two servers (Unit 1's CMDB server and Unit 2's ticketing server), each with its own
SDK v2 `Client`; namespaces their tools (`cmdb__get_asset`, `tickets__open_ticket`) into one
list for `gpt-oss-120b`'s tool calling; runs the tool-use loop (Foundations §15); stamps every
call with the joinable tuple; and, when a server asks for input (`input_required`), asks the
*person* at the terminal, never the model.

    python examples/02/host.py --list                         # the tool table; no model needed
    python examples/02/host.py                                # chat (needs the LLM endpoint)
    python examples/02/host.py --ask "who owns jump-prod-02?"
    python examples/02/host.py --ask "open a P2 ticket for jump-prod-02" --answer P2
"""

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.types import ElicitResult, Implementation

EXAMPLES = Path(__file__).resolve().parents[1]
sys.path.append(str(EXAMPLES))  # creating-mcps/examples/
from common_mcp import llm, trace_meta  # noqa: E402

SERVERS = {
    "cmdb": EXAMPLES / "01" / "cmdb_server.py",
    "tickets": EXAMPLES / "02" / "tickets_server.py",
}
SYSTEM = (
    "You are an assistant for a security operations team. Use the tools to look things up; "
    "never guess a hostname, owner or ticket id. Say plainly when a tool returns an error, "
    "and never claim a ticket was opened unless the tool returned its id."
)
_NOT_ALLOWED = re.compile(r"[^a-zA-Z0-9_-]")


def namespaced(server: str, tool: str) -> str:
    """`server__tool`, made safe for OpenAI tool names (^[a-zA-Z0-9_-]{1,64}$)."""
    return _NOT_ALLOWED.sub("_", f"{server}__{tool}")[:64]


@dataclass
class ToolEvent:
    """One tool call, as the host saw it: a row in the lab's side panel and the host's log."""
    session_id: str
    trace_id: str
    step: int
    server: str
    tool: str
    arg_keys: list
    outcome: str          # ok | tool_error | error | unknown_tool
    asked_user: bool      # did the server ask the person for input during this call?
    ms: float
    result_bytes: int


class Host:
    """Holds one Client per server, the merged tool table, and the elicitation route."""

    def __init__(self, servers: dict, ask_user, env: dict | None = None):
        self.servers = servers
        self.ask_user = ask_user          # async (server, ElicitRequestParams) -> ElicitResult
        self.env = env                    # extra env vars for the servers (Unit 3: MCP_PERSONA)
        self.clients: dict = {}
        self.tools: dict = {}             # namespaced name -> (server, mcp Tool)
        self._stack = AsyncExitStack()
        self._asked = False

    async def __aenter__(self):
        for name, path in self.servers.items():
            # A path is one of this course's servers; anything else (Unit 5: a third-party
            # server in its own environment) arrives as ready-made StdioServerParameters.
            params = path if isinstance(path, StdioServerParameters) else StdioServerParameters(
                command=sys.executable, args=[str(path)], env=self.env)
            client = Client(params,
                            client_info=Implementation(name="unit2-host", version="0.1"),
                            elicitation_callback=self._elicitation_for(name))
            self.clients[name] = await self._stack.enter_async_context(client)
            for tool in (await self.clients[name].list_tools()).tools:
                key = namespaced(name, tool.name)
                if key in self.tools:
                    raise RuntimeError(f"two tools map to the same name {key!r}")
                self.tools[key] = (name, tool)
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    def _elicitation_for(self, server):
        async def on_elicit(_ctx, params):
            self._asked = True
            return await self.ask_user(server, params)   # the person answers, not the model
        return on_elicit

    def openai_tools(self) -> list[dict]:
        return [{"type": "function", "function": {
                    "name": key,
                    "description": f"[{server}] {tool.description or ''}".strip(),
                    "parameters": tool.input_schema}}
                for key, (server, tool) in self.tools.items()]

    async def call(self, key: str, arguments: dict, session_id: str, trace_id: str,
                   step: int) -> tuple[str, ToolEvent]:
        """Run one tool call the model asked for. Returns (text for the model, event)."""
        self._asked = False
        def event(server, tool, outcome, ms, text):
            return ToolEvent(session_id, trace_id, step, server, tool, sorted(arguments),
                             outcome, self._asked, round(ms, 1), len(text.encode()))

        if key not in self.tools:          # never dispatch a tool no server offered
            text = f"error: there is no tool named {key!r}"
            return text, event("-", key, "unknown_tool", 0.0, text)
        server, tool = self.tools[key]
        t0 = time.perf_counter()
        try:
            result = await self.clients[server].call_tool(
                tool.name, arguments, meta=trace_meta(session_id, trace_id, step))
        except Exception as e:             # protocol or transport error: report it, keep going
            text, outcome = f"error: {type(e).__name__}: {e}", "error"
        else:
            if result.structured_content is not None:
                text = json.dumps(result.structured_content)
            else:
                text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
            outcome = "tool_error" if result.is_error else "ok"
            if result.is_error:
                text = f"error: {text}"
        return text, event(server, tool.name, outcome, (time.perf_counter() - t0) * 1000, text)


def log_event(ev: ToolEvent) -> None:
    print(json.dumps({"operation": "host.tool_call", **asdict(ev)}), file=sys.stderr, flush=True)


async def run_turn(host: Host, client, model: str, messages: list, session_id: str,
                   *, max_steps: int = 8, on_event=log_event) -> str:
    """One user turn: the Foundations §15 loop, dispatching tool calls over MCP."""
    trace_id = uuid.uuid4().hex                  # one trace per turn
    tools = host.openai_tools()
    step = 0
    for _ in range(max_steps):
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=model, messages=messages, tools=tools, tool_choice="auto")
        msg = response.choices[0].message
        if not msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            return msg.content or ""
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            step += 1
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                text = "error: the arguments were not valid JSON; send a JSON object"
            else:
                text, ev = await host.call(tc.function.name, arguments, session_id, trace_id, step)
                on_event(ev)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
    return "(stopped: reached max_steps)"


async def ask_in_terminal(server, params) -> ElicitResult:
    """Show a server's question to the person at the terminal and return their answer."""
    print(f"\n[{server} asks] {params.message}")
    if getattr(params, "mode", "form") != "form":
        print("  (not a form; declining)")
        return ElicitResult(action="decline")
    reply = await asyncio.to_thread(input, "  Accept? [y/N] ")
    if reply.strip().lower() not in ("y", "yes"):
        return ElicitResult(action="decline")
    content = {}
    for key, spec in params.requested_schema.get("properties", {}).items():
        choices = f" {spec['enum']}" if "enum" in spec else ""
        content[key] = (await asyncio.to_thread(input, f"  {key}{choices}: ")).strip()
    return ElicitResult(action="accept", content=content)


def scripted_answer(answer: str):
    """For non-interactive runs: 'decline', or a priority to accept with (e.g. 'P2')."""
    async def ask(server, params):
        print(f"\n[{server} asks] {params.message}\n  (scripted answer: {answer})")
        if answer == "decline":
            return ElicitResult(action="decline")
        return ElicitResult(action="accept", content={"priority": answer})
    return ask


async def main(args) -> None:
    ask = scripted_answer(args.answer) if args.answer else ask_in_terminal
    async with Host(SERVERS, ask) as host:
        if args.list or llm() is None:
            for key, (server, tool) in host.tools.items():
                print(f"{key:<26} {(tool.description or '').splitlines()[0][:70]}")
            if not args.list:
                print("\nOPENAI_BASE_URL / OPENAI_API_KEY not set -- skipping the chat. "
                      "The servers and the tool table above work without the model.")
            return
        client, model = llm()
        session_id = uuid.uuid4().hex
        messages = [{"role": "system", "content": SYSTEM}]
        questions = [args.ask] if args.ask else None
        while True:
            question = questions.pop(0) if questions else (
                None if args.ask else await asyncio.to_thread(input, "\nyou> "))
            if not question or question.strip() in ("/quit", "/exit"):
                return
            messages.append({"role": "user", "content": question})
            print(f"\nassistant> {await run_turn(host, client, model, messages, session_id)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A host for the Unit 1 and Unit 2 servers.")
    parser.add_argument("--list", action="store_true", help="print the namespaced tool table")
    parser.add_argument("--ask", help="ask one question and exit")
    parser.add_argument("--answer", choices=["decline", "P1", "P2", "P3"],
                        help="answer any confirmation form with this, without prompting")
    asyncio.run(main(parser.parse_args()))
