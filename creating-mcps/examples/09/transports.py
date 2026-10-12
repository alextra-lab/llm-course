"""
Unit 9: the same server over stdio and over Streamable HTTP, measured.

One server (Unit 7's gateway, CMDB side, no pools, so only the transport differs), one caller
(priya), the same call (`get_asset`) many times:

    start     from nothing to a tools/list answer: a stdio server is a new process per host
              (per agent), an HTTP server is already running
    per call  the median and p95 of N sequential calls on one connection
    reconnect HTTP with a new client per call: what a host that doesn't keep its client pays
    memory    the server process's resident memory: per agent over stdio, once over HTTP

    python examples/09/transports.py --calls 200
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNIT7 = HERE.parent / "07"
sys.path[:0] = [str(UNIT7), str(HERE.parent)]
import issuer as idp  # noqa: E402
import refusals  # noqa: E402
from common_mcp import load_env  # noqa: E402

ARGS = {"hostname": "jump-prod-02"}


def _ms(values: list[float]) -> dict:
    values = sorted(values)
    return {"p50_ms": round(statistics.median(values) * 1000, 2),
            "p95_ms": round(values[int(0.95 * (len(values) - 1))] * 1000, 2)}


def _rss_mb(pid: int) -> float:
    import psutil

    return round(psutil.Process(pid).memory_info().rss / 2**20, 1)


def _children() -> set[int]:
    import psutil

    return {p.pid for p in psutil.Process().children(recursive=True)}


async def over_stdio(calls: int) -> dict:
    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "MCP_ACCESS_TOKEN": idp.issue("priya", "urn:creating-mcps:cmdb")}
    params = StdioServerParameters(command=sys.executable, env=env, args=[
        str(UNIT7 / "gateway_server.py"), "--kind", "cmdb", "--stdio"])
    before = _children()
    t0 = time.perf_counter()
    with open(os.devnull, "w") as quiet:               # the server's log, not ours
        return await _stdio_calls(Client(stdio_client(params, errlog=quiet)), before, t0,
                                  calls)


async def _stdio_calls(connection, before: set[int], t0: float, calls: int) -> dict:
    async with connection as client:
        await client.list_tools()
        start = time.perf_counter() - t0
        pid = next(iter(_children() - before))
        took = []
        for _ in range(calls):
            t = time.perf_counter()
            r = await client.call_tool("get_asset", ARGS)
            took.append(time.perf_counter() - t)
            assert not r.is_error, r.content
        rss = _rss_mb(pid)
    return {"transport": "stdio", "start_ms": round(start * 1000), **_ms(took), "rss_mb": rss,
            "processes for 20 agents": 20, "memory for 20 agents (MB)": round(20 * rss)}


async def over_http(url: str, pid: int, calls: int) -> list[dict]:
    token = idp.issue("priya", url)
    t0 = time.perf_counter()
    async with refusals.connect(url, token) as client:
        await client.list_tools()
        start = time.perf_counter() - t0
        took = []
        for _ in range(calls):
            t = time.perf_counter()
            r = await client.call_tool("get_asset", ARGS)
            took.append(time.perf_counter() - t)
            assert not r.is_error, r.content
    fresh = []
    for _ in range(max(10, calls // 5)):
        t = time.perf_counter()
        async with refusals.connect(url, token) as client:
            await client.call_tool("get_asset", ARGS)
        fresh.append(time.perf_counter() - t)
    rss = _rss_mb(pid)
    return [{"transport": "HTTP, one client", "start_ms": round(start * 1000), **_ms(took),
             "rss_mb": rss, "processes for 20 agents": 1, "memory for 20 agents (MB)": rss},
            {"transport": "HTTP, a new client per call", "start_ms": None, **_ms(fresh),
             "rss_mb": rss, "processes for 20 agents": 1, "memory for 20 agents (MB)": rss}]


async def run(calls: int = 200) -> list[dict]:
    load_env()
    rows = [await over_stdio(calls)]
    before = _children()
    async with refusals.servers() as urls:          # Unit 7's servers over HTTP
        await asyncio.sleep(0.5)
        pids = _children() - before
        import psutil

        pid = next(p for p in pids if "cmdb" in " ".join(psutil.Process(p).cmdline()))
        rows += await over_http(urls["cmdb"], pid, calls)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--calls", type=int, default=200)
    args = parser.parse_args()
    for row in asyncio.run(run(args.calls)):
        print(row)


if __name__ == "__main__":
    main()
