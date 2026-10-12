"""
Unit 12: the production-readiness review. Every unit left a check behind; this runs them against
the SOC gateway and prints one scorecard, the document you'd bring to the review.

    area        what is checked                                              from
    catalog     the catalog promises nothing the backends refuse              Unit 3
    policy      the rules decide the table of cases as written                 Unit 7
    contract    what each group's tools/list says, against reviewed snapshots  Unit 11
    dial        every tool is read-only; propose_containment executes nothing  Units 5, 12
                and only L3 sees it
    identity    no token, a token for another server and a caller with no      Unit 7
                grant are all refused at the door
    envelope    the replicas' pools fit the connection budget; a caller who    Units 8, 9
                calls too fast is told to wait
    triage      the planted incident, end to end: the facts, the confirmations Units 4, 12
    audit       every step of the triage is in the audit log under its trace   Unit 11
    latency     the gateway's own time per step (from telemetry), against a    Unit 11
                budget
    eval        the tool-selection gate (needs the model; a warning without)  Units 6, 11
    owasp       the OWASP MCP Top 10 cases: blocked, got through, or todo      Unit 10

Each line is pass, warn (works, with a caveat to read), fail, or todo (not written yet: the
Unit 10 negative cases are placeholders, and the scorecard says so rather than hiding them).

    python examples/12/readiness.py                      # the scorecard
    python examples/12/readiness.py --markdown review.md # and the review document
    UPDATE_CONTRACTS=1 python examples/12/readiness.py   # accept the SOC listing as reviewed

Exit status: 1 if any line fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "11" / "tests"), str(EXAMPLES / "11"),
                str(EXAMPLES / "10"), str(EXAMPLES / "09"), str(EXAMPLES / "08"),
                str(EXAMPLES / "07"), str(EXAMPLES)]
import issuer as idp  # noqa: E402
import refusals  # noqa: E402  (Unit 7: connect with a token)
import soc_gateway  # noqa: E402
import triage  # noqa: E402
from common_mcp import llm, load_env, os_client  # noqa: E402

CONTRACTS = HERE / "contracts"
GROUPS = ("soc-l1", "soc-l3", "contractors")
STEP_BUDGET_MS = 500         # the gateway's own time for one step, at the 95th percentile
TRIAGE_BUDGET_MS = 3000      # ... and for the whole triage, all steps together


def line(area: str, status: str, detail: str) -> dict:
    return {"area": area, "status": status, "detail": detail}


def _run(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          timeout=timeout, cwd=EXAMPLES.parent)


# ---- checks that need no server --------------------------------------------------------

def catalog() -> dict:
    r = _run(str(EXAMPLES / "03" / "catalog.py"), "--check")
    last = (r.stdout.strip().splitlines() or ["no output"])[-1]
    return line("catalog", "pass" if r.returncode == 0 else "fail", last)


def policy() -> dict:
    r = _run(str(EXAMPLES / "07" / "policy.py"), "--test")
    rows = [x for x in r.stdout.splitlines() if x.startswith("  ok ") or x.startswith("  BAD")]
    bad = [x.strip() for x in rows if x.startswith("  BAD")]
    if r.returncode or bad or not rows:
        return line("policy", "fail", "; ".join(bad) or r.stderr.strip()[-200:] or "no cases")
    return line("policy", "pass", f"{len(rows)} cases decided as written")


def _gateway():
    name = "unit7_gateway"
    if name not in sys.modules:
        soc_gateway._load(name, EXAMPLES / "07" / "gateway_server.py")
    gateway = sys.modules[name]
    soc_gateway.install(gateway)
    return gateway


async def listings() -> dict:
    """What each group's tools/list says on the SOC gateway, built in memory."""
    import test_contracts

    gateway = _gateway()
    return {g: await test_contracts.listing(gateway, "soc", g) for g in GROUPS}


def contract(now: dict) -> dict:
    """Unit 11's contract test, for the gateway that will ship."""
    import test_contracts

    changed, missing = [], []
    for group, listing in now.items():
        snapshot = CONTRACTS / f"soc-{group}.json"
        if os.environ.get("UPDATE_CONTRACTS"):
            CONTRACTS.mkdir(exist_ok=True)
            snapshot.write_text(json.dumps(listing, indent=2, sort_keys=True) + "\n")
        if not snapshot.exists():
            missing.append(snapshot.name)
            continue
        changed += [f"{group}: {c}" for c in test_contracts.differences(
            json.loads(snapshot.read_text()), listing)]
    if missing:
        return line("contract", "fail", "no snapshot yet (UPDATE_CONTRACTS=1): "
                    + ", ".join(missing))
    if changed:
        return line("contract", "fail", "; ".join(changed[:6]))
    tools = {g: len(v["tools"]) for g, v in now.items()}
    return line("contract", "pass", "matches the reviewed snapshots: "
                + ", ".join(f"{g} {n} tools" for g, n in tools.items()))


def dial(now: dict, proposals: list[dict]) -> dict:
    """Where the gateway sits on the autonomy dial: it reads, and it proposes."""
    problems = []
    for group, listing in now.items():
        for t in listing["tools"]:
            hints = t.get("annotations") or {}
            if not hints.get("readOnlyHint") or hints.get("destructiveHint"):
                problems.append(f"{t['name']} isn't marked read-only")
    names = {g: {t["name"] for t in v["tools"]} for g, v in now.items()}
    if "propose_containment" in names["soc-l1"]:
        problems.append("L1 sees propose_containment")
    if names["contractors"]:
        problems.append(f"contractors see {len(names['contractors'])} tools")
    if not proposals or any(p["status"] != "proposed: not executed" for p in proposals):
        problems.append("a proposal says it executed something")
    if problems:
        return line("dial", "fail", "; ".join(sorted(set(problems))))
    return line("dial", "pass", f"{len(names['soc-l3'])} tools, all read-only; "
                f"{len(proposals)} proposals, none executed; L1 and contractors can't propose")


def budget(workers: int = 2) -> dict:
    import scaled_server

    try:
        scaled_server.check_budget(workers)
    except SystemExit as e:
        return line("envelope", "fail", str(e))
    share = scaled_server.pool_share(int(os.environ.get("MCP_PG_BUDGET", 20)), workers)
    return line("envelope", "pass", f"{workers} replicas x {share} connections fit the "
                "gateway login's limit")


def eval_gate() -> dict:
    if llm() is None:
        r = _run(str(EXAMPLES / "11" / "eval_gate.py"))
        status = "warn" if r.returncode == 0 else "fail"
        return line("eval", status, "golden set consistent; tool selection NOT measured "
                    "(no model: set OPENAI_BASE_URL and OPENAI_API_KEY)"
                    if status == "warn" else r.stdout.strip()[-300:])
    r = _run(str(EXAMPLES / "11" / "eval_gate.py"), "--repeats", "3", timeout=1800)
    last = (r.stdout.strip().splitlines() or ["no output"])[-1]
    return line("eval", "pass" if r.returncode == 0 else "fail", last)


async def owasp() -> dict:
    import cases

    rows = await cases.run(set())
    todo = [r["owasp"] for r in rows if r["result"] == "todo"]
    through = [r["owasp"] for r in rows if r["result"] == "got through"]
    blocked = [r["owasp"] for r in rows if r["result"] == "blocked"]
    if through:
        return line("owasp", "fail", "got through: " + ", ".join(through))
    if todo:
        return line("owasp", "todo", f"{len(blocked)} of {len(rows)} cases written and "
                    f"blocked; still placeholders: {', '.join(todo)}")
    return line("owasp", "pass", f"all {len(rows)} cases blocked")


# ---- checks against the running gateway ------------------------------------------------

async def identity(url: str) -> dict:
    """The door: three callers who must not get in, and one who gets in to nothing."""
    import httpx

    problems, ok = [], []
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2026-07-28", "capabilities": {},
        "clientInfo": {"name": "readiness", "version": "1"}}}
    headers = {"Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient() as http:
        for who, token in (("no token", None),
                           ("a token for another server",
                            idp.issue("marc", "urn:creating-mcps:cmdb")),
                           ("a forged token", refusals._forged("marc", url))):
            h = {**headers, **({"Authorization": f"Bearer {token}"} if token else {})}
            r = await http.post(url, json=init, headers=h)
            (ok if r.status_code == 401 else problems).append(f"{who}: {r.status_code}")
    async with refusals.connect(url, idp.issue("sam", url)) as client:
        tools = (await client.list_tools()).tools
        r = await client.call_tool("get_asset", {"hostname": "jump-prod-02"})
        text = r.content[0].text if r.content else ""
        if tools or not r.is_error:
            problems.append(f"sam (no grant): {len(tools)} tools, get_asset "
                            f"{'refused' if r.is_error else 'answered'}")
        else:
            ok.append(f"sam: no tools, get_asset refused ({text[:40]})")
    async with refusals.connect(url, idp.issue("priya", url)) as client:
        r = await client.call_tool("propose_containment", {
            "incident_id": "INC-0042", "action": "isolate_host", "target": "jump-prod-02",
            "reason": "test", "evidence": ["test"]})
        (ok if r.is_error else problems).append(
            f"priya proposing: {'refused' if r.is_error else 'ACCEPTED'}")
    return line("identity", "fail" if problems else "pass", "; ".join(problems or ok))


async def rate_limit(url: str) -> dict:
    """One caller, a burst well past the limit: some calls must be told to wait."""
    async with refusals.connect(url, idp.issue("priya", url)) as client:
        results = await asyncio.gather(*(client.call_tool("count_assets", {})
                                         for _ in range(25)))
    busy = [r for r in results if r.is_error and r.content
            and r.content[0].text.startswith("busy:")]
    if not busy:
        return line("envelope", "fail", "25 calls at once from one caller, none told to wait: "
                    "are the limits on (MCP_LIMITS=1)?")
    return line("envelope", "pass", f"25 calls at once from one caller: {len(busy)} told to "
                f"wait ({busy[0].content[0].text[:70]}...)")


def latency(trace_id: str, wait: float = 15) -> dict:
    """The gateway's own time per step, from its telemetry: what the server spent, not what
    the client waited (a retry after a busy refusal is the caller's time, not the server's)."""
    client = os_client("auditor")
    body = {"size": 100, "query": {"bool": {"filter": [
        {"term": {"trace_id": trace_id}}, {"term": {"method": "tools/call"}}]}},
        "_source": ["step", "name", "ms", "outcome"]}
    deadline = time.time() + wait
    while True:
        hits = client.search(index="mcp-telemetry-*", body=body, ignore_unavailable=True,
                             allow_no_indices=True)["hits"]["hits"]
        if hits or time.time() > deadline:
            break
        time.sleep(1)
    if not hits:
        return line("latency", "fail", f"no telemetry for trace {trace_id} (MCP_TELEMETRY?)")
    ms = sorted(h["_source"]["ms"] for h in hits if h["_source"].get("ms") is not None)
    p95 = ms[min(len(ms) - 1, int(round(0.95 * (len(ms) - 1))))]
    total = sum(ms)
    slowest = max(hits, key=lambda h: h["_source"].get("ms") or 0)["_source"]
    detail = (f"p95 {p95:.0f} ms per call (budget {STEP_BUDGET_MS}), median "
              f"{statistics.median(ms):.0f} ms, {total:.0f} ms for the whole triage (budget "
              f"{TRIAGE_BUDGET_MS}); slowest step {slowest['step']} {slowest['name']} "
              f"{slowest['ms']:.0f} ms")
    status = "pass" if p95 <= STEP_BUDGET_MS and total <= TRIAGE_BUDGET_MS else "warn"
    return line("latency", status, detail)


async def against_the_gateway(env: dict | None = None) -> tuple[list[dict], dict]:
    import loadgen

    loadgen.clear_shared_state()
    out = []
    async with triage.serving(env) as srv:
        run = await triage.scripted(srv["url"])
        out.append(await identity(srv["url"]))
        out.append(await rate_limit(srv["url"]))
    checks = triage.check(run)
    audit = [c for c in checks if c[0] == "every step is in the audit log"]
    rest = [c for c in checks if c[0] != "every step is in the audit log"]
    bad = [f"{what}: {detail}" for what, ok, detail in rest if not ok]
    out.append(line("triage", "fail" if bad else "pass", "; ".join(bad) or (
        f"INC-0042 in {len(run['steps'])} steps: {run['found']['attacker address']} as "
        f"{run['found']['account']} at {run['found']['first login (UTC)']} UTC"
        + (f", then {run['found']['next host']}" if run["found"].get("next host") else "")
        + f"; {len(run['asked'])} ticket confirmations")))
    for what, ok, detail in audit:
        out.append(line("audit", "pass" if ok else "fail", detail))
    out.append(latency(run["trace_id"]))
    return out, run


# ---- the review ---------------------------------------------------------------------------

ORDER = ("catalog", "policy", "contract", "dial", "identity", "envelope", "triage", "audit",
         "latency", "eval", "owasp")
MARK = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "todo": "TODO"}


async def review(env: dict | None = None) -> dict:
    load_env()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    lines = [catalog(), policy()]
    now = await listings()
    lines.append(contract(now))
    live, run = await against_the_gateway(env)
    lines += [dial(now, run["proposals"]), budget(), *live, eval_gate(), await owasp()]
    lines.sort(key=lambda x: ORDER.index(x["area"]))
    counts = {s: sum(x["status"] == s for x in lines) for s in MARK}
    return {"lines": lines, "counts": counts, "run": run,
            # A todo is evidence nobody has collected yet: the review can't sign it off.
            "verdict": "not ready: something fails" if counts["fail"] else
                       "not signed off: the todo lines are unproven" if counts["todo"] else
                       "ready, once the warnings are read and accepted" if counts["warn"]
                       else "ready"}


def markdown(result: dict) -> str:
    run = result["run"]
    out = ["# Production-readiness review: the SOC gateway", "",
           f"**Verdict: {result['verdict']}** "
           f"({', '.join(f'{n} {s}' for s, n in result['counts'].items() if n)})", "",
           "| Area | Result | Detail |", "|---|---|---|"]
    out += [f"| {x['area']} | {MARK[x['status']]} | {x['detail'].replace('|', '/')} |"
            for x in result["lines"]]
    out += ["", "## The triage it was reviewed on", "",
            "| Step | Tool | Asked | Finding |", "|---|---|---|---|"]
    out += [f"| {s['step']} | {s['tool']} | {'yes' if s['asked'] else ''} | "
            f"{str(s['found']).replace('|', '/')} |" for s in run["steps"]]
    out += ["", "## Proposals, for a person to approve", ""]
    for p in run["proposals"]:
        out += [f"- **{p['proposal_id']}**: {p['action']} {p['target']} ({p['status']}). "
                f"Approver: {p['approver']}. Requires: {'; '.join(p['requires'])}. "
                f"Rollback: {p['rollback']}."]
    out += ["", f"Trace `{run['trace_id']}`: `audit.trace()` and the telemetry index join on it."]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="The SOC gateway's readiness review.")
    parser.add_argument("--markdown", help="also write the review as Markdown here")
    args = parser.parse_args()
    result = asyncio.run(review())
    for x in result["lines"]:
        print(f"  [{MARK[x['status']]}] {x['area']:<9} {x['detail']}")
    print(f"verdict: {result['verdict']}")
    if args.markdown:
        Path(args.markdown).write_text(markdown(result))
        print("wrote", args.markdown)
    sys.exit(1 if result["counts"]["fail"] else 0)


if __name__ == "__main__":
    main()
