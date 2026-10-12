"""
Unit 12: "an alert fired on a host", end to end, through the SOC gateway.

`scripted` walks the measured path as Marc, an L3 responder, over HTTP, with the whole stack
on (identity, policy, envelope, telemetry, audit), one trace for the investigation:

    1  list_incidents       what's active
    2  get_incident         the host, the address, the account, a window
    3  event_histogram      when: failures per bucket in the window (count before you read)
    4  top_values           who: the addresses behind the failures
    5  search_events        what: successful logins from that address, on the narrow window
    6  search_events        where next: the account's other logins (a person lookup: the
                            policy asks Marc for a ticket, and he gives the incident's)
    7  get_asset            whose host, what it runs
    8  service_dependencies what else breaks if it's isolated
    9  search_events        what the attacker did: EDR on the host, as the account (a ticket)
    10 propose_containment  isolate the host
    11 propose_containment  disable the account

It checks the findings against facts computed from the estate (Unit 4's `facts`), and that
every step is in the audit log under the investigation's trace.

`by_model` gives the same gateway (over stdio, as Marc) to the model with the triage_alert
prompt, and records the path it took, whether it counted before it read, whether it proposed
containment, and how many facts its answer got right. Without a model it says so.

    python examples/12/triage.py                       # the scripted path
    python examples/12/triage.py --model --repeats 3   # and the model's
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "11"), str(EXAMPLES / "07"), str(EXAMPLES / "04"),
                str(EXAMPLES)]
import issuer as idp  # noqa: E402
import refusals  # noqa: E402  (Unit 7: connect with a token)
from common_mcp import llm, load_env, trace_meta  # noqa: E402

INCIDENT = "INC-0042"
AGGREGATES = ("event_histogram", "top_values")
DEFAULTS = {"MCP_POOL": "1", "MCP_LIMITS": "1", "MCP_TELEMETRY": "opensearch", "MCP_AUDIT": "1"}


def _unit4():
    if "unit4_investigate" not in sys.modules:
        spec = importlib.util.spec_from_file_location("unit4_investigate",
                                                      EXAMPLES / "04" / "investigate.py")
        module = sys.modules["unit4_investigate"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return sys.modules["unit4_investigate"]


@contextlib.asynccontextmanager
async def serving(env: dict | None = None, log_dir: Path | None = None):
    """The SOC gateway over HTTP, with the whole stack on. Yields {"url", "log"}."""
    idp.keys()
    log_dir = Path(log_dir or tempfile.mkdtemp(prefix="unit12-"))
    log_dir.mkdir(parents=True, exist_ok=True)
    port = refusals._free_port()
    log = log_dir / "soc.jsonl"
    proc = subprocess.Popen([sys.executable, str(HERE / "soc_gateway.py"), "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=log.open("w"),
                            env={**os.environ, **DEFAULTS, **(env or {})})
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        deadline = time.time() + 30
        async with httpx.AsyncClient() as http:
            while True:
                try:
                    r = await http.get(f"http://127.0.0.1:{port}/readyz")
                    if r.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.time() > deadline or proc.poll() is not None:
                    raise RuntimeError(f"the SOC gateway didn't start; see {log}:\n"
                                       + log.read_text()[-1500:])
                await asyncio.sleep(0.2)
        yield {"url": url, "log": log}
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)


async def scripted(url: str, who: str = "marc", ticket: str = INCIDENT) -> dict:
    """The measured path. Returns the steps, the proposals and the trace to join them by."""
    from mcp.types import ElicitResult

    asked: list[str] = []

    async def on_elicit(_ctx, params):              # the person, not the model, answers
        asked.append(params.message)
        return ElicitResult(action="accept", content={"ticket": ticket})

    session_id, trace_id = f"s-{who}-{uuid.uuid4().hex[:6]}", uuid.uuid4().hex
    steps: list[dict] = []

    async with refusals.connect(url, idp.issue(who, url), on_elicit) as client:
        async def step(tool, args, finding):
            n = len(steps) + 1
            before = len(asked)
            t0 = time.monotonic()
            for _ in range(3):
                r = await client.call_tool(tool, args, meta=trace_meta(session_id, trace_id, n))
                text = "\n".join(c.text for c in r.content if getattr(c, "text", None))
                wait = re.search(r"^busy: .*Retry the same call after ([\d.]+) s", text)
                if not (r.is_error and wait):
                    break
                await asyncio.sleep(float(wait.group(1)))   # what Unit 8 told callers to do
            data = r.structured_content or {}
            steps.append({"step": n, "tool": tool, "args": json.dumps(args),
                          "ms": round((time.monotonic() - t0) * 1000, 1),
                          "bytes": len(text.encode()),
                          "asked": len(asked) > before,
                          "found": text[:200] if r.is_error else finding(data)})
            if r.is_error:
                raise RuntimeError(f"step {n} ({tool}) failed: {text[:300]}")
            return data

        await step("list_incidents", {"status": ["open", "in_progress"], "severity": "high"},
                   lambda r: f"{r['total']} active high: "
                             + ", ".join(i["id"] for i in r["incidents"]))
        inc = await step("get_incident", {"incident_id": INCIDENT},
                         lambda r: f"{r['title']} ({r['source.ip']}, {r['user.name']})")
        win = inc["suggested_window"]
        failed = {"event.outcome": "failure"}
        await step("event_histogram", {"dataset": "auth", **win, "filters": failed},
                   lambda r: f"{r['total']} failures, peak {r['peak']['count']} at "
                             f"{r['peak']['start']}")
        top = await step("top_values", {"dataset": "auth", "field": "source.ip", **win,
                                        "filters": failed},
                         lambda r: ", ".join(f"{v['value']} ({v['count']})"
                                             for v in r["values"][:3]))
        attacker = top["values"][0]["value"]
        got_in = await step("search_events", {"dataset": "auth", **win, "filters": {
                                "source.ip": attacker, "event.outcome": "success"}},
                            lambda r: "; ".join(f"{e['@timestamp']} {e['user.name']} on "
                                                f"{e['host.name']}" for e in r["events"]))
        first = got_in["events"][0]
        account, host = first["user.name"], first["host.name"]
        moves = await step("search_events", {"dataset": "auth", **win, "filters": {
                               "user.name": account, "event.outcome": "success"}},
                           lambda r: "; ".join(f"{e['@timestamp'][11:16]} {e['host.name']}"
                                               for e in r["events"]))
        elsewhere = sorted({e["host.name"] for e in moves["events"]} - {host})
        asset = await step("get_asset", {"hostname": host},
                           lambda r: f"{r['service']}, {r['environment']}, "
                                     f"{r['classification']}, owned by {r['owner_team']}")
        await step("service_dependencies", {"service": asset["service"]},
                   lambda r: "depended on by: " + (", ".join(r["depended_on_by"]) or "nothing"))
        edr = await step("search_events", {"dataset": "edr", "start": first["@timestamp"],
                                           "end": win["end"], "filters": {
                                               "host.name": host, "user.name": account}},
                         lambda r: f"{r['total']} EDR events as {account}")
        evidence = [f"auth: {account} logged in on {host} from {attacker} at "
                    f"{first['@timestamp']} after the failures in {win['start']}..{win['end']}",
                    f"edr: {edr['total']} events on {host} as {account} after that login"]
        if elsewhere:
            evidence.append(f"auth: {account} then logged in on {', '.join(elsewhere)}")
        proposals = []
        for action, target, reason in (
                ("isolate_host", host, "an attacker has an interactive session on it"),
                ("disable_account", account, "the attacker is moving with its credential")):
            proposals.append(await step(
                "propose_containment", {"incident_id": INCIDENT, "action": action,
                                        "target": target, "reason": reason,
                                        "evidence": evidence},
                lambda r: f"{r['proposal_id']}: {r['action']} {r['target']} "
                          f"({r['status']}), approver {r['approver']}"))
    return {"who": who, "session_id": session_id, "trace_id": trace_id, "steps": steps,
            "proposals": proposals, "asked": asked,
            "found": {"attacker address": attacker, "account": account,
                      "first login (UTC)": first["@timestamp"][11:16],
                      **({"next host": elsewhere[0]} if elsewhere else {})}}


def check(run: dict) -> list[tuple[str, bool, str]]:
    """The scripted run against the estate's own facts, and against the audit log."""
    import audit

    gold = _unit4().facts("l3_responder")
    out = [(f"found the {k}", run["found"].get(k) == v, f"{run['found'].get(k)} vs {v}")
           for k, v in gold.items() if k in run["found"]]
    rows = audit.trace(run["trace_id"])
    # One step can be several rows: a step that asked for a ticket is two rounds of the same
    # call (input_required, then the answer), and a call turned away as busy and retried is two.
    extra = [f"{r['step']}: {r['verdict']}/{r['outcome']}" for r in rows
             if r.get("outcome") == "input_required" or r.get("verdict") == "busy"]
    out.append(("every step is in the audit log",
                sorted({r["step"] for r in rows}) == [s["step"] for s in run["steps"]]
                and all(r["outcome"] == "ok" for r in rows
                        if r.get("outcome") != "input_required" and r.get("verdict") != "busy"),
                f"{len(rows)} rows for {len(run['steps'])} steps"
                + (f"; the extra rounds: {', '.join(extra)}" if extra else "")))
    # Which steps asked, not how many times: a lookup retried after a busy refusal asks again.
    asked = [s["step"] for s in run["steps"] if s["asked"]]
    lookups = [s["step"] for s in run["steps"]
               if s["tool"] in ("search_events", *AGGREGATES) and "user.name" in s["args"]]
    out.append(("person lookups asked for a ticket, nothing else did",
                asked == lookups and len(lookups) == 2,
                f"steps {asked} asked ({len(run['asked'])} confirmations)"))
    out.append(("counted before reading", run["steps"][2]["tool"] in AGGREGATES
                and run["steps"][4]["tool"] == "search_events", "histogram, top values, search"))
    out.append(("proposed, executed nothing", all(p["status"].startswith("proposed")
                                                  for p in run["proposals"]),
                ", ".join(p["proposal_id"] for p in run["proposals"])))
    return out


# =========================================================================================
# The model's path
# =========================================================================================

async def by_model(repeats: int = 3, who: str = "marc", on_run=None) -> list[dict]:
    from mcp import StdioServerParameters

    unit4 = _unit4()
    unit2 = unit4.unit2
    client, model = llm()
    gold = unit4.facts("l3_responder")
    audience = "urn:creating-mcps:soc"
    server = StdioServerParameters(
        command=sys.executable, args=[str(HERE / "soc_gateway.py"), "--stdio"],
        env={"MCP_ACCESS_TOKEN": idp.issue(who, audience)})
    from mcp.types import ElicitResult

    async def person(_server, _params):            # Marc confirms with the incident's ticket
        return ElicitResult(action="accept", content={"ticket": INCIDENT})

    from soc_gateway import triage_alert

    runs = []
    async with unit2.Host({"soc": server}, person) as host:
        for repeat in range(1, repeats + 1):
            events = []
            messages = [{"role": "system", "content": unit4.SYSTEM},
                        {"role": "user", "content": triage_alert(INCIDENT)}]
            t0 = time.time()
            answer = await unit2.run_turn(host, client, model, messages, uuid.uuid4().hex,
                                          max_steps=20, on_event=events.append)
            path = [e.tool for e in events]
            first_search = next((i for i, t in enumerate(path) if t == "search_events"),
                                len(path))
            graded = unit4.grade(answer, gold)
            run = {"repeat": repeat, "facts": f"{sum(graded.values())}/{len(graded)}",
                   "missed": [k for k, ok in graded.items() if not ok],
                   "proposed": "propose_containment" in path,
                   "counted_first": any(t in AGGREGATES for t in path[:first_search]),
                   "tool_calls": len(path), "errors": sum(e.outcome != "ok" for e in events),
                   "result_bytes": sum(e.result_bytes for e in events),
                   "seconds": round(time.time() - t0, 1), "path": " > ".join(path),
                   "answer": answer}
            runs.append(run)
            if on_run:
                on_run(run)
    return runs


async def run_all(env: dict | None = None) -> dict:
    load_env()
    async with serving(env) as srv:
        run = await scripted(srv["url"])
    return {**run, "checks": check(run)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Alert triage through the SOC gateway.")
    parser.add_argument("--model", action="store_true", help="also run the model's triage")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    out = asyncio.run(run_all())
    for s in out["steps"]:
        print(f"{s['step']:>2} {s['tool']:<20} {s['ms']:>7} ms {s['bytes']:>6} B"
              f"{'  (asked)' if s['asked'] else ''}  {s['found']}")
    failed = 0
    for what, ok, detail in out["checks"]:
        print(f"  [{'PASS' if ok else 'FAIL'}] {what}: {detail}")
        failed += not ok
    print("trace:", out["trace_id"])
    if args.model:
        if llm() is None:
            print("model: skipped (set OPENAI_BASE_URL and OPENAI_API_KEY)")
        else:
            for r in asyncio.run(by_model(args.repeats)):
                print(json.dumps({k: v for k, v in r.items() if k != "answer"}))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
