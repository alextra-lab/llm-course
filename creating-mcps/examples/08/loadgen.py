"""
Unit 8: a load generator -- many agents, one envelope server, finite backends.

Starts the CMDB and SIEM envelope servers with the settings you choose, then runs N simulated
agents against them for a while. Each agent has its own identity (a token as `agent-NN`, an
L3 responder), its own pair of connections, and a loop: pick a call from a realistic mix,
make it, think, repeat. Optionally one more agent, `runaway`, runs several loops at once with
no thinking at all: the agent stuck in a retry loop that every shared server meets.

It records every call (latency, and what came back: ok, busy and why, too slow, error) and
samples the backends twice a second (the gateway's Postgres connections; OpenSearch's search
thread pool), then summarises: throughput, p50/p95 latency, refusals by kind, and the same for
the runaway agent separately, which is where fairness shows.

    python examples/08/loadgen.py --callers 20 --seconds 15
    python examples/08/loadgen.py --callers 40 --env MCP_POOL=0 MCP_LIMITS=0     # Unit 7
    python examples/08/loadgen.py --callers 10 --runaway 8 --env MCP_LIMITS=0     # unfair
    python examples/08/loadgen.py --callers 10 --runaway 8                        # fair
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "07"), str(HERE.parent)]
import issuer as idp  # noqa: E402
import refusals  # noqa: E402  (Unit 7: start servers, connect with a token)
from common_mcp import load_env, os_client, pg_connect  # noqa: E402

HOSTS = ["jump-prod-02", "db-prod-02", "web-prod-01", "cache-prod-11", "erp-prod-08"]
SERVICES = ["payments-api", "customer-db", "dns", "ci-build"]
DATASETS = ["auth", "web", "syslog"]

def _day(r: random.Random, args: dict) -> dict:
    """One of the last week's days: search_events allows 24 h at a time."""
    d = r.randint(2, 7)
    return {**args, "start": f"now-{d}d", "end": f"now-{d - 1}d"}


# (weight, server, tool, arguments): what a SOC agent's calls look like, roughly
MIX = [
    (4, "cmdb", "get_asset", lambda r: {"hostname": r.choice(HOSTS)}),
    (2, "cmdb", "count_assets", lambda r: {"environment": r.choice(["prod", "staging"])}),
    (2, "cmdb", "find_assets", lambda r: {"service": r.choice(SERVICES)}),
    (3, "siem", "event_histogram", lambda r: {"dataset": r.choice(DATASETS),
                                              "start": "now-7d"}),
    (2, "siem", "top_values", lambda r: {"dataset": "auth", "field": "source.ip",
                                         "start": "now-3d"}),
    (2, "siem", "search_events", lambda r: _day(r, {"dataset": r.choice(DATASETS),
                                                    "limit": 20})),
]

DEFAULTS = {"MCP_POOL": "1", "MCP_LIMITS": "1"}


def classify(is_error: bool, text: str) -> str:
    """What came back, as one word: ok, busy:<which limit>, gated (refused by a cost gate),
    too_slow, error."""
    if not is_error:
        return "ok"
    t = text.lower()
    if t.startswith("busy:"):
        for key, kind in (("rate limit", "rate"), ("result budget", "quota"),
                          ("calls running", "concurrency"), ("at capacity", "capacity"),
                          ("in flight for everyone", "tool_cap"),
                          ("cursors open for everyone", "cursors_total"),
                          ("result cursors open", "cursors")):
            if key in t:
                return f"busy:{kind}"
        return "busy:other"
    if "the server is busy" in t or "the log cluster" in t:
        return "busy:backend"
    if "narrow the window" in t and "the limit is" in t:
        return "gated"
    if "time budget" in t:
        return "too_slow"
    return "error"


async def agent(name: str, urls: dict, until: float, think: float, rng: random.Random,
                calls: list, loops: int = 1) -> None:
    clients = {}
    try:
        for kind in ("cmdb", "siem"):
            token = idp.issue(name, urls[kind], groups=["soc-l3"], ttl=3600)
            clients[kind] = await refusals.connect(urls[kind], token).__aenter__()

        async def loop():
            weights = [m[0] for m in MIX]
            while time.monotonic() < until:
                _w, server, tool, make = rng.choices(MIX, weights)[0]
                t0 = time.monotonic()
                try:
                    r = await clients[server].call_tool(tool, make(rng))
                    text = r.content[0].text if r.content else ""
                    outcome = classify(r.is_error, text)
                except Exception as e:  # noqa: BLE001 -- the transport failed: an error
                    outcome, text = "error", f"{type(e).__name__}: {e}"
                calls.append({"agent": name, "tool": tool, "t": t0,
                              "ms": (time.monotonic() - t0) * 1000, "outcome": outcome,
                              "detail": text[:160] if outcome != "ok" else ""})
                if think:
                    await asyncio.sleep(rng.expovariate(1 / think))

        await asyncio.gather(*(loop() for _ in range(loops)))
    finally:
        for c in clients.values():
            try:
                await c.__aexit__(None, None, None)
            except BaseException:  # noqa: BLE001 -- closing after the run: nothing to do
                pass


def sample_backends(pg, osc) -> dict:
    conns = pg.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = 'mcp_service'"
                       ).fetchone()[0]
    nodes = osc.nodes.stats(metric="thread_pool,indices", index_metric="search")["nodes"]
    search = [n["thread_pool"]["search"] for n in nodes.values()]
    return {"pg_connections": conns, "os_active": sum(s["active"] for s in search),
            "os_queue": sum(s["queue"] for s in search),
            "os_rejected": sum(s["rejected"] for s in search),
            "os_open_contexts": sum(n["indices"]["search"]["open_contexts"]
                                   for n in nodes.values())}


def clear_cursors() -> bool:
    """Close every point in time on the cluster, so one run's abandoned cursors don't count
    against the next. Needs the estate's admin login (your own estate); False without it."""
    try:
        sys.path.insert(0, str(HERE.parents[1] / "env"))
        import env as estate
        estate.admin_os().delete_all_pits()
        return True
    except Exception:  # noqa: BLE001 -- a shared server: no admin, the cursors expire anyway
        return False


async def run(callers: int = 20, seconds: float = 15, think: float = 0.3, runaway: int = 0,
              env: dict | None = None, seed: int = 7, log_dir: Path | None = None) -> dict:
    load_env()
    env = {**DEFAULTS, **(env or {})}
    calls, samples = [], []
    clear_cursors()
    async with refusals.servers(log_dir, script=HERE / "envelope_server.py", env=env) as urls:
        # Connections first, then the clock: the run measures calls, not start-up.
        until = time.monotonic() + seconds + 3
        tasks = [asyncio.create_task(agent(f"agent-{i:02d}", urls, until, think,
                                           random.Random(seed + i), calls))
                 for i in range(callers)]
        if runaway:
            tasks.append(asyncio.create_task(agent("runaway", urls, until, 0,
                                                   random.Random(seed - 1), calls, runaway)))
        with pg_connect("l3_responder") as pg:
            osc = os_client("l3_responder")
            start = sample_backends(pg, osc)
            while not all(t.done() for t in tasks):
                s = sample_backends(pg, osc)
                s["t"] = time.monotonic()
                s["os_rejected"] -= start["os_rejected"]
                samples.append(s)
                await asyncio.sleep(0.5)
        await asyncio.gather(*tasks, return_exceptions=True)
        logs = {k: str(v) for k, v in urls["logs"].items()}
    t0 = min((c["t"] for c in calls), default=0)
    for c in calls:
        c["t"] = round(c["t"] - t0, 3)
        c["ms"] = round(c["ms"], 1)
    for s in samples:
        s["t"] = round(s["t"] - t0, 2)
    return {"settings": {"callers": callers, "seconds": seconds, "think": think,
                         "runaway": runaway, **env},
            "calls": calls, "samples": samples, "logs": logs,
            "summary": summarise(calls, samples)}


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1], 1)


def summarise(calls: list, samples: list) -> list[dict]:
    rows = []
    span = (max(c["t"] for c in calls) - min(c["t"] for c in calls)) if calls else 0
    for group, mine in (("everyone", calls),
                        ("agents", [c for c in calls if c["agent"] != "runaway"]),
                        ("runaway", [c for c in calls if c["agent"] == "runaway"])):
        if not mine:
            continue
        ok = [c["ms"] for c in mine if c["outcome"] == "ok"]
        outcomes: dict[str, int] = {}
        for c in mine:
            outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1
        rows.append({"who": group, "calls": len(mine), "ok": len(ok),
                     "ok_per_s": round(len(ok) / span, 1) if span else None,
                     "p50_ms": _pct(ok, 50), "p95_ms": _pct(ok, 95),
                     "refused": {k: v for k, v in sorted(outcomes.items()) if k != "ok"}})
    if samples:
        rows[0]["max_pg_connections"] = max(s["pg_connections"] for s in samples)
        rows[0]["os_rejected"] = samples[-1]["os_rejected"]
        rows[0]["max_open_contexts"] = max(s["os_open_contexts"] for s in samples)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Many agents against the envelope server.")
    parser.add_argument("--callers", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--think", type=float, default=0.3, help="mean seconds between calls")
    parser.add_argument("--runaway", type=int, default=0,
                        help="parallel loops for one agent that never thinks (0: none)")
    parser.add_argument("--env", nargs="*", default=[], metavar="KEY=VALUE",
                        help="server settings, e.g. MCP_POOL=0 MCP_PER_CALLER=1")
    parser.add_argument("--out", help="also write every call as JSONL here")
    args = parser.parse_args()
    env = dict(kv.split("=", 1) for kv in args.env)
    result = asyncio.run(run(args.callers, args.seconds, args.think, args.runaway, env))
    if args.out:
        Path(args.out).write_text("".join(json.dumps(c) + "\n" for c in result["calls"]))
    print(json.dumps(result["settings"]))
    for row in result["summary"]:
        print(json.dumps(row))
    errors = [c["detail"] for c in result["calls"] if c["outcome"] == "error"][:3]
    for e in errors:
        print("  error:", e)


if __name__ == "__main__":
    main()
