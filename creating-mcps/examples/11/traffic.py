"""
Unit 11: a day at the SOC, compressed. Starts the observed servers (observed_server.py) and runs
investigations against them, as the directory's people and a team of agents:

    priya    L1 analyst: triages hosts, including one she isn't allowed to see
    marc     L3 responder: works the planted incident
    sam      a contractor with no grants, who tries anyway
    agent-NN L3 agents running the same playbooks in a loop

Unlike Unit 8's load generator, every call carries the joinable tuple in `_meta`: one session
per person or agent, one trace per investigation, a step number per call. That's what lets
telemetry, the audit log and the client's own record be joined afterwards.

    python examples/11/traffic.py --agents 6 --seconds 20
    python examples/11/traffic.py --env MCP_AUDIT_FAIL=open --out calls.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "08"), str(EXAMPLES / "07"), str(EXAMPLES)]
import issuer as idp  # noqa: E402
import loadgen  # noqa: E402  (Unit 8: classify(), and clearing the last run's shared state)
import refusals  # noqa: E402  (Unit 7: start servers, connect with a token)
from common_mcp import load_env, trace_meta  # noqa: E402
from opentelemetry import trace  # noqa: E402

TRACER = trace.get_tracer("unit11-traffic")

HOSTS = ["jump-prod-02", "web-prod-01", "cache-prod-11", "erp-prod-08"]
RESTRICTED = "db-prod-01"
SERVICES = ["payments-api", "customer-db", "dns", "ci-build"]


def host_triage(r: random.Random, host: str | None = None) -> list:
    host = host or r.choice(HOSTS)
    return [("cmdb", "get_asset", {"hostname": host}),
            ("siem", "list_incidents", {"host": host}),
            ("siem", "event_histogram", {"dataset": "auth", "start": "now-7d",
                                         "filters": {"host.name": host}}),
            ("siem", "top_values", {"dataset": "auth", "field": "source.ip", "start": "now-3d",
                                    "filters": {"host.name": host}})]


def incident(r: random.Random) -> list:
    return [("siem", "list_incidents", {"status": ["open", "in_progress"], "limit": 5}),
            ("siem", "get_incident", {"incident_id": "INC-0042"}),
            ("cmdb", "get_asset", {"hostname": "jump-prod-02"}),
            ("siem", "event_histogram", {"dataset": "syslog", "start": "now-7d",
                                         "filters": {"host.name": "jump-prod-02"}})]


def service_impact(r: random.Random) -> list:
    service = r.choice(SERVICES)
    return [("cmdb", "service_dependencies", {"service": service}),
            ("cmdb", "find_assets", {"service": service}),
            ("cmdb", "count_assets", {"environment": "prod"})]


def restricted_triage(r: random.Random) -> list:
    return host_triage(r, RESTRICTED)[:3]


# who -> (groups, playbooks they run)
PEOPLE = {
    "priya": (["soc-l1"], [host_triage, service_impact, restricted_triage]),
    "marc": (["soc-l3"], [incident, host_triage]),
    "sam": (["contractors"], [service_impact]),
}
AGENT = (["soc-l3"], [host_triage, incident, service_impact])


async def worker(who: str, groups: list[str], playbooks: list, urls: dict, until: float,
                 think: float, rng: random.Random, calls: list) -> None:
    clients = {}
    session_id = f"s-{who}-{uuid.uuid4().hex[:6]}"
    try:
        for kind in ("cmdb", "siem"):
            token = idp.issue(who, urls[kind], groups=groups, ttl=3600)
            clients[kind] = await refusals.connect(urls[kind], token).__aenter__()
        while time.monotonic() < until:
            playbook = rng.choice(playbooks)
            with TRACER.start_as_current_span(f"investigate {playbook.__name__}") as span:
                await investigation(span, playbook(rng), session_id, who, playbook.__name__,
                                    clients, think, rng, calls)
    finally:
        for c in clients.values():
            try:
                await c.__aexit__(None, None, None)
            except BaseException:  # noqa: BLE001 -- closing after the run: nothing to do
                pass


async def investigation(span, steps: list, session_id: str, who: str, name: str,
                        clients: dict, think: float, rng: random.Random, calls: list) -> None:
    # One investigation, one trace. With a tracer provider configured, the SDK puts this
    # span's trace in `_meta` itself, over anything we put there, so take the trace id from
    # it; without one, the span is a no-op and we make our own.
    ctx = span.get_span_context()
    trace_id = f"{ctx.trace_id:032x}" if ctx.is_valid else uuid.uuid4().hex
    for step, (server, tool, args) in enumerate(steps, start=1):
        t0 = time.monotonic()
        try:
            res = await clients[server].call_tool(
                tool, args, meta=trace_meta(session_id, trace_id, step))
            text = res.content[0].text if res.content else ""
            outcome = loadgen.classify(res.is_error, text)
        except Exception as e:  # noqa: BLE001 -- the transport failed: an error
            outcome, text = "error", f"{type(e).__name__}: {e}"
        calls.append({"who": who, "session_id": session_id, "trace_id": trace_id,
                      "step": step, "playbook": name, "server": server,
                      "tool": tool, "outcome": outcome,
                      "ms": round((time.monotonic() - t0) * 1000, 1),
                      "detail": text[:160] if outcome != "ok" else ""})
        if think:
            await asyncio.sleep(rng.expovariate(1 / think))


async def run(agents: int = 4, seconds: float = 15, think: float = 0.2,
              env: dict | None = None, seed: int = 11, log_dir: Path | None = None) -> dict:
    load_env()
    env = {"MCP_POOL": "1", "MCP_LIMITS": "1", "MCP_TELEMETRY": "opensearch", "MCP_AUDIT": "1",
           **(env or {})}
    loadgen.clear_cursors()
    loadgen.clear_shared_state()
    calls: list = []
    started = time.time()
    async with refusals.servers(log_dir, script=HERE / "observed_server.py", env=env) as urls:
        until = time.monotonic() + seconds
        crew = [(who, *PEOPLE[who]) for who in PEOPLE] + [
            (f"agent-{i:02d}", *AGENT) for i in range(agents)]
        await asyncio.gather(*(worker(who, groups, books, urls, until, think,
                                      random.Random(seed + i), calls)
                               for i, (who, groups, books) in enumerate(crew)))
        logs = {k: str(v) for k, v in urls["logs"].items()}
    return {"settings": {"agents": agents, "seconds": seconds, "think": think, **env},
            "started": started, "calls": calls, "logs": logs, "summary": summarise(calls)}


def summarise(calls: list) -> list[dict]:
    rows: dict = {}
    for c in calls:
        row = rows.setdefault(c["who"] if not c["who"].startswith("agent-") else "agents",
                              {"who": c["who"] if not c["who"].startswith("agent-")
                               else "agents", "calls": 0, "traces": set()})
        row["calls"] += 1
        row["traces"].add(c["trace_id"])
        row[c["outcome"]] = row.get(c["outcome"], 0) + 1
    return [{**r, "traces": len(r["traces"])} for r in rows.values()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Investigations against the observed servers.")
    parser.add_argument("--agents", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--think", type=float, default=0.2)
    parser.add_argument("--env", nargs="*", default=[], metavar="KEY=VALUE")
    parser.add_argument("--out", help="also write every call as JSONL here")
    args = parser.parse_args()
    result = asyncio.run(run(args.agents, args.seconds, args.think,
                             dict(kv.split("=", 1) for kv in args.env)))
    if args.out:
        Path(args.out).write_text("".join(json.dumps(c) + "\n" for c in result["calls"]))
    print(json.dumps(result["settings"]))
    for row in result["summary"]:
        print(json.dumps(row))
    print("one trace:", next((c["trace_id"] for c in result["calls"]
                             if c["who"] == "marc"), "-"))


if __name__ == "__main__":
    main()
