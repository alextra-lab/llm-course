"""
E1: the change drill. Starts the stand-in backends and the actions gateway, then walks Marc (L3)
and Priya (L1) through what happens to a real containment step, and checks each outcome against
the backends' ledger, which records every side effect that really happened:

     1  priya lists tools          the action tools aren't in an L1 analyst's list
     2  outside the window         a production host, 14:00 UTC, no emergency: refused before
                                   anyone is asked, nothing done
     3  not the proposal           a proposal id that isn't this step's: refused
     4  declined                   Marc is asked for a ticket and declines: nothing done
     5  emergency, approved        asked, approved with INC-0042; became a task; polled to done;
                                   one change record, one isolation, one comment
     6  the same call again        "already isolated": nobody asked, nothing done
     7  release, no tasks          a client that didn't declare the Tasks extension: the same
                                   call simply waits, and gets the same kind of result
     8  lost replies               every first answer is lost after the work is done: the
                                   retries replay, and there is still one isolation
     9  EDR down                   retries, then the breaker opens; the next call fails at once;
                                   once the EDR is back and the cooldown passes, it works
    and the record                 every EDR action cites the approval ticket and a change
                                   record, and every change record has its outcome

    python examples/E1/drill.py             # exits 1 if a check fails
    python examples/E1/drill.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "07"), str(EXAMPLES)]
import issuer as idp  # noqa: E402
import refusals  # noqa: E402
from tasks_ext import TasksClient  # noqa: E402

INCIDENT, HOST = "INC-0042", "jump-prod-02"
PROPOSAL = "PRP-21d20d3e"                 # Unit 12's propose_containment, isolate jump-prod-02
SETTINGS = {"MCP_CLOCK": "2026-10-12T14:00:00Z",      # outside the 22:00-05:00 window
            "MCP_EDR_SECONDS": "2", "MCP_TASK_AFTER": "0.5", "MCP_BREAKER_COOLDOWN": "3"}


async def _ready(url: str, proc: subprocess.Popen, log: Path) -> None:
    deadline = time.time() + 30
    async with httpx.AsyncClient() as http:
        while True:
            with contextlib.suppress(httpx.TransportError):
                if (await http.get(url)).status_code < 500:
                    return
            if time.time() > deadline or proc.poll() is not None:
                raise RuntimeError(f"{log.name} didn't start:\n" + log.read_text()[-1500:])
            await asyncio.sleep(0.2)


@contextlib.asynccontextmanager
async def serving(env: dict | None = None, log_dir: Path | None = None, gateway: bool = True):
    """The backends and the actions gateway (or only the backends). Yields {"url",
    "backends", "log"}."""
    idp.keys()
    log_dir = Path(log_dir or tempfile.mkdtemp(prefix="e1-"))
    log_dir.mkdir(parents=True, exist_ok=True)
    bport, gport = refusals._free_port(), refusals._free_port()
    backends = f"http://127.0.0.1:{bport}"
    env = {**os.environ, **SETTINGS, "MCP_BACKENDS": backends, **(env or {})}
    procs = []
    try:
        for script, port, name in (("backends.py", bport, "backends"),
                                   ("actions_gateway.py", gport, "gateway"))[:1 + gateway]:
            log = log_dir / f"{name}.log"
            procs.append(subprocess.Popen([sys.executable, str(HERE / script), "--port",
                                           str(port)], stdout=subprocess.DEVNULL,
                                          stderr=log.open("w"), env=env))
            await _ready(f"http://127.0.0.1:{port}/" + ("healthz" if name == "backends"
                                                        else "mcp"), procs[-1], log)
        yield {"url": f"http://127.0.0.1:{gport}/mcp", "backends": backends,
               "log": log_dir / "gateway.log"}
    finally:
        for p in procs:
            p.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=5)


def connect(url: str, who: str, tasks: bool = True, answer: str = "accept",
            ticket: str = INCIDENT, seen: list | None = None):
    """A client as `who`; `seen` collects what happened on the way: asks and task polls."""
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client
    from mcp.types import ElicitResult

    seen = seen if seen is not None else []

    async def on_elicit(_ctx, params):              # the person answers, not the model
        seen.append({"asked": params.message})
        if answer == "accept":
            return ElicitResult(action="accept", content={"ticket": ticket})
        return ElicitResult(action="decline")

    headers = {"Authorization": f"Bearer {idp.issue(who, url)}"}
    extensions = [TasksClient(on_task=lambda t: seen.append(
        {"task": t.taskId[:8], "status": t.status}))] if tasks else []
    return Client(streamable_http_client(url, http_client=create_mcp_http_client(headers)),
                  elicitation_callback=on_elicit, extensions=extensions)


def _says(r) -> str:
    if r.structured_content:
        return json.dumps(r.structured_content)
    return "\n".join(c.text for c in r.content if getattr(c, "text", None))


async def call(url: str, who: str, tool: str, args: dict, **how) -> dict:
    """One call; what came back, what the person was asked, the task polls, and the time."""
    seen: list = []
    t0 = time.monotonic()
    async with connect(url, who, seen=seen, **how) as client:
        r = await client.call_tool(tool, args)
    return {"who": who, "tool": tool, "args": args, "error": r.is_error,
            "result": r.structured_content or {}, "says": _says(r),
            "asked": sum("asked" in s for s in seen),
            "task": [s["status"] for s in seen if "task" in s],
            "ms": round((time.monotonic() - t0) * 1000)}


async def drill(url: str, backends: str) -> dict:
    """The ten steps. Returns each step's call and the ledger after it."""
    isolate = {"incident_id": INCIDENT, "host": HOST, "proposal_id": PROPOSAL}
    release = {"incident_id": INCIDENT, "host": HOST}
    steps: list[dict] = []
    async with httpx.AsyncClient(base_url=backends) as http:
        await http.post("/_reset")

        async def step(name: str, coro, faults: dict | None = None) -> dict:
            await http.post("/_faults", json=faults or {})
            out = {"step": len(steps) + 1, "name": name, **(await coro)}
            await http.post("/_faults", json={})
            out["ledger"] = (await http.get("/_ledger")).json()
            steps.append(out)
            return out

        async def listed(who):
            async with connect(url, who) as client:
                tools = [t.name for t in (await client.list_tools()).tools]
            return {"who": who, "tools": tools}

        await step("priya lists tools", listed("priya"))
        await step("outside the window", call(url, "marc", "isolate_host", isolate))
        await step("not the proposal", call(url, "marc", "isolate_host",
                                            {**isolate, "proposal_id": "PRP-00000000"}))
        await step("declined", call(url, "marc", "isolate_host", {**isolate, "emergency": True},
                                    answer="decline"))
        await step("emergency, approved", call(url, "marc", "isolate_host",
                                               {**isolate, "emergency": True}))
        await step("the same call again", call(url, "marc", "isolate_host",
                                               {**isolate, "emergency": True}))
        await step("release, no tasks", call(url, "marc", "release_host", release, tasks=False))
        await step("lost replies", call(url, "marc", "isolate_host",
                                        {**isolate, "emergency": True}),
                   faults={"lose_first_reply": True})
        await http.post("/_faults", json={"down": True})
        down = [await call(url, "marc", "release_host", release) for _ in range(2)]
        await http.post("/_faults", json={})
        before = (await http.get("/_ledger")).json()
        await asyncio.sleep(float(SETTINGS["MCP_BREAKER_COOLDOWN"]))
        recovered = await call(url, "marc", "release_host", release)
        steps.append({"step": len(steps) + 1, "name": "EDR down", "down": down,
                      "before": before, "recovered": recovered,
                      "ledger": (await http.get("/_ledger")).json()})
        tickets = {t["ticket"]: (await http.get(f"/tickets/{t['ticket']}")).json()
                   for t in steps[-1]["ledger"] if t["effect"] == "open_ticket"}
    return {"steps": steps, "tickets": tickets}


def _effects(ledger: list, effect: str) -> list:
    return [e for e in ledger if e["effect"] == effect]


def check(run: dict) -> list[tuple[str, bool, str]]:
    s = {x["name"]: x for x in run["steps"]}
    out = []

    def row(name, ok, detail):
        out.append((name, bool(ok), detail))

    priya = s["priya lists tools"]["tools"]
    row("L1 doesn't see the action tools", not {"isolate_host", "release_host"} & set(priya),
        f"{len(priya)} tools, none of them actions")
    w = s["outside the window"]
    row("outside the window: refused, nobody asked, nothing done",
        w["error"] and w["asked"] == 0 and not w["ledger"] and "change window" in w["says"],
        w["says"][:110])
    p = s["not the proposal"]
    row("an unproposed step is refused", p["error"] and not p["ledger"] and "Propose" in p["says"],
        p["says"][:90])
    d = s["declined"]
    row("declined: asked once, nothing done", d["error"] and d["asked"] == 1 and not d["ledger"],
        d["says"][:90])
    e = s["emergency, approved"]
    row("approved: asked once, became a task, polled to completed",
        not e["error"] and e["asked"] == 1 and e["task"][:1] == ["working"]
        and e["task"][-1] == "completed", f"task: {' > '.join(_squash(e['task']))}")
    row("approved: one change, one isolation, one comment",
        [x["effect"] for x in e["ledger"]] == ["open_ticket", "isolate", "comment"],
        f"{e['result'].get('change_id')}, {e['result'].get('action_id')}, "
        f"{e['result'].get('took_s')} s")
    a = s["the same call again"]
    row("the same call again: nobody asked, nothing done",
        not a["error"] and a["asked"] == 0 and a["ledger"] == e["ledger"],
        a["result"].get("status", a["says"][:80]))
    r = s["release, no tasks"]
    row("no Tasks extension: the same call waits and completes",
        not r["error"] and not r["task"] and r["result"].get("status") == "released"
        and r["ms"] >= 1500, f"{r['ms']} ms, no task")
    lost = s["lost replies"]
    new = lost["ledger"][len(r["ledger"]):]
    tries = lost["result"].get("attempts", {})
    replays = sum(v.get("replays", 0) for v in tries.values())
    row("lost replies: retried, replayed, done once",
        not lost["error"] and [x["effect"] for x in new] == ["open_ticket", "isolate", "comment"]
        and replays >= 3, f"{replays} replays; {len(_effects(new, 'isolate'))} new isolation")
    dn = s["EDR down"]
    first, second = dn["down"]
    row("EDR down: retried, then failed saying nothing was done",
        first["error"] and "Nothing was" in first["says"]
        and len(_effects(dn["before"], "release")) == 1,
        f"{first['ms']} ms: {first['says'][:80]}")
    row("breaker open: the next call fails at once", second["error"] and second["ms"] < 1000
        and "circuit breaker" in second["says"], f"{second['ms']} ms")
    rec = dn["recovered"]
    row("after the cooldown: works again", not rec["error"]
        and rec["result"].get("status") == "released", rec["result"].get("status", rec["says"]))
    ledger = dn["ledger"]
    acts = [x for x in ledger if x["effect"] in ("isolate", "release")]
    changes = [t for t in run["tickets"].values()]
    row("every action cites the approval; every change has its outcome",
        acts and all(x["ticket"] == INCIDENT for x in acts)
        and len(changes) == len(acts) and all(len(t["comments"]) == 1 for t in changes),
        f"{len(acts)} actions, {len(changes)} change records: "
        + ", ".join(t["ticket_id"] for t in changes))
    return out


def _squash(statuses: list[str]) -> list[str]:
    return [x for i, x in enumerate(statuses) if i == 0 or statuses[i - 1] != x]


async def run_all(env: dict | None = None) -> dict:
    async with serving(env) as srv:
        return await drill(srv["url"], srv["backends"])


def main() -> None:
    parser = argparse.ArgumentParser(description="E1: the change drill.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    from common_mcp import load_env

    load_env()
    run = asyncio.run(run_all())
    rows = check(run)
    if args.json:
        print(json.dumps({"run": run, "checks": rows}, indent=1, default=str))
    else:
        for x in run["steps"]:
            print(f"{x['step']:2} {x['name']}")
        for name, ok, detail in rows:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    sys.exit(0 if all(ok for _, ok, _ in rows) else 1)


if __name__ == "__main__":
    main()
