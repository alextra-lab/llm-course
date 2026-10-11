"""
Unit 5: the dial, measured -- the same tasks, through each variant, repeated.

Ten tasks a SOC analyst asks. Eight are "on the menu": a task-shaped tool answers each one
directly. Two are "off the menu": no task tool fits, so a restrictive server can only answer
them by fetching far too much, and a hybrid has to ask the person to open the hatch. Gold
answers are computed live, as the same persona.

For every variant the report gives, over --repeats runs:

    success     mean, and the min-max across repeats (one run is an anecdote)
    tokens      prompt tokens the model consumed per task (from the API's usage)
    calls       tool calls per task, and how many came back as errors
    approvals   how often the person was asked to approve something (hybrid only)
    seconds     wall-clock per task

The hybrid's approver says yes to everything, as blast.py's does: the grid measures what
the hatch costs and buys, not how good the person is at saying no.

Before any model runs, it also prints what each variant's tools/list costs: every task
pays for it on every model call.

    python examples/05/grid.py                       # 3 repeats, every variant available
    python examples/05/grid.py --variants restrictive hybrid --repeats 5
    python examples/05/grid.py --tools-only          # just the tools/list cost (no model)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import statistics
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import variants  # noqa: E402
from common_mcp import llm, load_env, os_client, pg_connect  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


unit2 = _load("unit2_host", HERE.parent / "02" / "host.py")
grade = _load("unit3_eval", HERE.parent / "03" / "eval_discovery.py").grade

SYSTEM = (
    "You are a SOC analyst's assistant. Answer questions about the company's IT estate (the "
    "CMDB) and its logs (the SIEM) using the tools. Never guess a number or a name: if the "
    "tools can't give you the answer, say so. All times are UTC. End your reply with exactly "
    "one line `ANSWER: <value>`; for a list, comma-separated. If the data isn't available to "
    "you, write `ANSWER: UNAVAILABLE`."
)


# --- the tasks, with gold answers computed live ---------------------------------------------
def _one(persona, sql, *args):
    with pg_connect(persona) as conn:
        return conn.execute(sql, args).fetchone()[0]


def _set(persona, sql, *args):
    with pg_connect(persona) as conn:
        return {r[0] for r in conn.execute(sql, args).fetchall()}


def incident_day(os_) -> date:
    inc = os_.search(index="incidents", body={"query": {"term": {"incident.id": "INC-0042"}}})
    return date.fromisoformat(inc["hits"]["hits"][0]["_source"]["incident"]["created"][:10])


def tasks(persona: str) -> list[dict]:
    """Each: {id, menu: on|off, ask, gold}. gold is an int, a str, or a set of str."""
    os_ = os_client(persona)
    day = incident_day(os_)
    window = {"range": {"@timestamp": {"gte": f"{day}T00:00:00Z",
                                       "lt": f"{day + timedelta(days=1)}T00:00:00Z"}}}

    def auth(*filters, **kw):
        return os_.search(index="logs-auth-*", body={
            "query": {"bool": {"filter": [window, *filters]}}, **kw})

    top_ip = auth({"term": {"event.outcome": "failure"}},
                  {"term": {"host.name": "jump-prod-02"}}, size=0,
                  aggs={"ip": {"terms": {"field": "source.ip", "size": 1}}})
    backup = auth({"term": {"user.name": "svc_backup"}}, {"term": {"event.outcome": "success"}},
                  size=10, sort=[{"@timestamp": "asc"}], _source=["@timestamp", "host.name"])
    logins = [h["_source"] for h in backup["hits"]["hits"]]
    first_host = next(lg["host"]["name"] for lg in logins)
    next_host = next(lg["host"]["name"] for lg in logins if lg["host"]["name"] != first_host)
    web_5xx = os_.count(index="logs-web-*", body={"query": {"bool": {"filter": [
        window, {"range": {"http.response.status_code": {"gte": 500, "lt": 600}}}]}}})
    cve = _one(persona, "SELECT f.cve_id FROM cmdb.asset_vulnerabilities f "
               "JOIN cmdb.assets a ON a.id = f.asset_id WHERE f.status = 'open' "
               "AND a.environment = 'prod' GROUP BY f.cve_id ORDER BY count(*) DESC, f.cve_id "
               "LIMIT 1")
    return [
        {"id": "owner-of-host", "menu": "on",
         "ask": "Who is the owner of the host jump-prod-02? Give their name.",
         "gold": _one(persona, "SELECT p.full_name FROM cmdb.assets a JOIN cmdb.people p "
                      "ON p.id = a.owner_id WHERE a.hostname = 'jump-prod-02'")},
        {"id": "depends-on-customer-db", "menu": "on",
         "ask": "Which services depend on customer-db?",
         "gold": _set(persona, "SELECT s.name FROM cmdb.service_dependencies d "
                      "JOIN cmdb.services s ON s.id = d.service_id "
                      "JOIN cmdb.services t ON t.id = d.depends_on_id "
                      "WHERE t.name = 'customer-db'")},
        {"id": "cve-open-prod", "menu": "on",
         "ask": f"How many production hosts still have {cve} open?",
         "gold": _one(persona, "SELECT count(DISTINCT a.id) FROM cmdb.asset_vulnerabilities f "
                      "JOIN cmdb.assets a ON a.id = f.asset_id WHERE f.cve_id = %s "
                      "AND f.status = 'open' AND a.environment = 'prod'", cve)},
        {"id": "tier1-prod-count", "menu": "on",
         "ask": "How many production hosts run tier-1 (business-critical) services?",
         "gold": _one(persona, "SELECT count(*) FROM cmdb.assets a JOIN cmdb.services s "
                      "ON s.id = a.service_id WHERE a.environment = 'prod' AND s.tier = 1")},
        {"id": "top-failing-ip", "menu": "on",
         "ask": f"Which source IP had the most failed logins against jump-prod-02 on {day}?",
         "gold": top_ip["aggregations"]["ip"]["buckets"][0]["key"]},
        {"id": "backup-first-login", "menu": "on",
         "ask": f"At what time (HH:MM, UTC) did svc_backup first log in successfully on {day}?",
         "gold": logins[0]["@timestamp"][11:16]},
        {"id": "web-5xx-day", "menu": "on",
         "ask": f"How many web requests got a server error (5xx) response on {day}?",
         "gold": web_5xx["count"]},
        {"id": "backup-next-host", "menu": "on",
         "ask": f"On {day}, svc_backup logged in to {first_host}. Which host did it log in to "
                "next?",
         "gold": next_host},
        {"id": "team-most-prod-hosts", "menu": "off",
         "ask": "Which team owns the most production hosts?",
         "gold": _one(persona, "SELECT t.name FROM cmdb.assets a JOIN cmdb.services s "
                      "ON s.id = a.service_id JOIN cmdb.teams t ON t.id = s.owner_team_id "
                      "WHERE a.environment = 'prod' GROUP BY t.name "
                      "ORDER BY count(*) DESC, t.name LIMIT 1")},
        {"id": "os-critical-prod", "menu": "off",
         "ask": "Among production hosts with an open critical vulnerability, which operating "
                "system is the most common?",
         "gold": _one(persona, "SELECT a.os FROM cmdb.assets a WHERE a.environment = 'prod' "
                      "AND EXISTS (SELECT 1 FROM cmdb.asset_vulnerabilities f "
                      "JOIN cmdb.vulnerabilities v ON v.cve_id = f.cve_id WHERE "
                      "f.asset_id = a.id AND f.status = 'open' AND v.severity = 'critical') "
                      "GROUP BY a.os ORDER BY count(*) DESC, a.os LIMIT 1")},
    ]


# --- the tools/list cost, no model needed -----------------------------------------------------
async def tool_cost(variant: str, persona: str) -> dict:
    async with unit2.Host(variants.servers(variant, persona), _approve,
                          env={"MCP_PERSONA": persona}) as host:
        text = json.dumps(host.openai_tools())
    return {"variant": variant, "tools": len(host.tools), "bytes": len(text.encode()),
            "approx_tokens": len(text.encode()) // 4}


async def _approve(_server, _params):
    return unit2.ElicitResult(action="accept", content={})


# --- one conversation -------------------------------------------------------------------------
async def ask(host, client, model, task, max_steps=12) -> dict:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task["ask"]}]
    tools = host.openai_tools()
    trace_id, t0 = uuid.uuid4().hex, time.time()
    usage = {"prompt": 0, "completion": 0}
    calls = errors = approvals = 0
    answer = "(stopped: reached max_steps)"
    for _ in range(max_steps):
        response = await asyncio.to_thread(client.chat.completions.create, model=model,
                                           messages=messages, tools=tools, tool_choice="auto")
        if response.usage:
            usage["prompt"] += response.usage.prompt_tokens
            usage["completion"] += response.usage.completion_tokens
        msg = response.choices[0].message
        if not msg.tool_calls:
            answer = msg.content or ""
            break
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            calls += 1
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                text = "error: the arguments were not valid JSON"
            else:
                text, ev = await host.call(tc.function.name, args, "grid", trace_id, calls)
                approvals += ev.asked_user
            errors += text.startswith("error")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
    ok, got = grade(answer, task["gold"])
    return {"task": task["id"], "menu": task["menu"], "correct": ok, "answer": got,
            "tool_calls": calls, "errors": errors, "approvals": approvals,
            "prompt_tokens": usage["prompt"], "completion_tokens": usage["completion"],
            "seconds": round(time.time() - t0, 1)}


async def run(persona: str, which: list[str], repeats: int, on_result=None) -> list[dict]:
    client, model = llm()
    ts = tasks(persona)
    results = []
    for variant in which:
        async with unit2.Host(variants.servers(variant, persona), _approve,
                              env={"MCP_PERSONA": persona}) as host:
            for repeat in range(1, repeats + 1):
                for t in ts:
                    r = {"variant": variant, "repeat": repeat,
                         **await ask(host, client, model, t)}
                    results.append(r)
                    if on_result:
                        on_result(r)
    return results


def summarise(results: list[dict]) -> list[dict]:
    """Per variant (and per menu): success per repeat -> mean and spread; mean cost per task."""
    out = []
    for variant in dict.fromkeys(r["variant"] for r in results):
        for menu in ("all", "on", "off"):
            mine = [r for r in results if r["variant"] == variant
                    and menu in ("all", r["menu"])]
            per_repeat = [statistics.mean(r["correct"] for r in mine if r["repeat"] == k)
                          for k in sorted({r["repeat"] for r in mine})]
            mean = lambda key: statistics.mean(r[key] for r in mine)  # noqa: E731
            out.append({
                "variant": variant, "tasks": menu, "repeats": len(per_repeat),
                "success": round(statistics.mean(per_repeat), 3),
                "min": round(min(per_repeat), 3), "max": round(max(per_repeat), 3),
                "prompt_tokens": round(mean("prompt_tokens")),
                "tool_calls": round(mean("tool_calls"), 1),
                "errors": round(mean("errors"), 1),
                "approvals": round(mean("approvals"), 1),
                "seconds": round(mean("seconds"), 1),
            })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="The same tasks through every variant.")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--variants", nargs="+", choices=variants.NAMES)
    parser.add_argument("--tools-only", action="store_true",
                        help="only print what each variant's tools/list costs")
    parser.add_argument("--out", help="also write every result as JSONL here")
    args = parser.parse_args()
    load_env()
    which = args.variants or variants.available()

    print("what each variant's tool list costs, on every model call:")
    for v in which:
        c = asyncio.run(tool_cost(v, args.persona))
        print(f"  {v:<12} {c['tools']:>2} tools  {c['bytes']:>6,} bytes  "
              f"~{c['approx_tokens']:,} tokens")
    if args.tools_only:
        return
    if llm() is None:
        print("\nOPENAI_BASE_URL / OPENAI_API_KEY not set -- the grid needs the model. "
              "blast.py and --tools-only work without it.")
        return
    print()
    for t in tasks(args.persona):
        print(f"  gold {t['id']:<22} ({t['menu']:>3}) {t['gold']}")

    def show(r):
        print(f"{r['variant']:<12} #{r['repeat']} {r['task']:<22} "
              f"{'ok ' if r['correct'] else 'BAD'} got={r['answer'][:30]!r:<32} "
              f"calls={r['tool_calls']:>2} tokens={r['prompt_tokens']:>6} "
              f"approvals={r['approvals']}")
    results = asyncio.run(run(args.persona, which, args.repeats, show))
    if args.out:
        Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in results))
    print()
    for s in summarise(results):
        print(f"{s['variant']:<12} {s['tasks']:<4} success {s['success']:.0%} (range "
              f"{s['min']:.0%}-{s['max']:.0%} over {s['repeats']} runs)  "
              f"~{s['prompt_tokens']:,} tokens/task  {s['tool_calls']} calls  "
              f"{s['errors']} errors  {s['approvals']} approvals  {s['seconds']} s")


if __name__ == "__main__":
    main()
