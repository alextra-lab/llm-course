"""
Unit 3: does a catalog make the model ask better questions? Measured, over repeated runs.

Nine questions about the estate, each answered by gpt-oss-120b in a fresh conversation, under
three conditions:

  none     -- only a `query` tool. The model can still poke at information_schema itself.
  raw      -- `query` plus the raw-discovery server (list/describe tables and indexes).
  curated  -- `query` plus the catalog server (list/describe datasets).

`query` belongs to this harness, not to any server you'd ship: it runs read-only SQL or an
OpenSearch search AS the persona, because what we're measuring is whether the model knows what
to ask. (Whether you'd ever give a model raw SQL is Unit 5's question.) Gold answers are
computed live, as the same persona, so row-level security applies to both sides.

Every condition is run `--repeats` times. Models are not deterministic; one run is an anecdote.
The report gives the mean accuracy AND the spread (min-max) across repeats, plus the real
prompt tokens the model consumed (from the API's `usage`).

    python examples/03/eval_discovery.py                 # 3 repeats, l1_analyst
    python examples/03/eval_discovery.py --repeats 5 --conditions raw curated
"""

import argparse
import asyncio
import importlib.util
import json
import re
import statistics
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent))  # creating-mcps/examples/
from common_mcp import llm, load_env, os_client, pg_connect  # noqa: E402

_spec = importlib.util.spec_from_file_location("unit2_host", HERE.parent / "02" / "host.py")
unit2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(unit2)

CONDITIONS = {
    "none": {},
    "raw": {"raw": HERE / "raw_server.py"},
    "curated": {"catalog": HERE / "catalog_server.py"},
}
SYSTEM = (
    "You answer questions about a company's IT estate. The CMDB is a Postgres database; the "
    "SIEM is an OpenSearch cluster. Use the tools to find out what data exists, then run "
    "queries with the `query` tool: {\"sql\": \"...\"} for Postgres (read-only), or "
    "{\"index\": \"...\", \"body\": {...}} for an OpenSearch search. Never guess a number. "
    "End your reply with exactly one line `ANSWER: <value>`; for a list, comma-separated. If "
    "the data you'd need is not available to you, write `ANSWER: UNAVAILABLE`."
)
QUERY_TOOL = {"type": "function", "function": {
    "name": "query",
    "description": "Run one read-only query. Postgres: {\"sql\": \"SELECT ...\"}. "
                   "OpenSearch: {\"index\": \"logs-auth-*\", \"body\": {\"query\": ..., "
                   "\"aggs\": ...}}. Returns at most 50 rows or the search response.",
    "parameters": {"type": "object", "properties": {
        "sql": {"type": "string"},
        "index": {"type": "string"},
        "body": {"type": "object"}}}}}
MAX_QUERY_BYTES = 6_000


# --- the harness's own query tool -------------------------------------------------------------
def run_query(persona: str, args: dict) -> str:
    try:
        if args.get("sql"):
            with pg_connect(persona) as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                cur = conn.execute(args["sql"])
                cols = [c.name for c in cur.description or []]
                rows = [list(map(_plain, r)) for r in cur.fetchmany(50)]
            text = json.dumps({"columns": cols, "rows": rows})
        elif args.get("index"):
            body = dict(args.get("body") or {})
            body.setdefault("size", 10)
            body["size"] = min(int(body["size"]), 50)
            text = json.dumps(os_client(persona).search(index=args["index"], body=body))
        else:
            return "error: pass either `sql`, or `index` and `body`"
    except Exception as e:  # noqa: BLE001 -- every failure goes back to the model as text
        return f"error: {type(e).__name__}: {str(e)[:300]}"
    if len(text) > MAX_QUERY_BYTES:
        text = text[:MAX_QUERY_BYTES] + "... [truncated: aggregate or narrow the query]"
    return text


def _plain(v):
    return v if isinstance(v, (int, float, str, bool, type(None))) else str(v)


# --- the questions, with gold answers computed live -------------------------------------------
def _sql_one(persona, sql, *args):
    with pg_connect(persona) as conn:
        return conn.execute(sql, args).fetchone()[0]


def _sql_set(persona, sql, *args):
    with pg_connect(persona) as conn:
        return {r[0] for r in conn.execute(sql, args).fetchall()}


def questions(persona: str) -> list[dict]:
    """Each: {id, ask, gold} where gold is an int, a str, or a set of str."""
    os_ = os_client(persona)
    inc = os_.search(index="incidents", body={"query": {"term": {"incident.id": "INC-0042"}}})
    day = date.fromisoformat(inc["hits"]["hits"][0]["_source"]["incident"]["created"][:10])
    window = {"gte": f"{day}T00:00:00Z", "lt": f"{day + timedelta(days=1)}T00:00:00Z"}
    failed_on_day = {"bool": {"filter": [{"term": {"event.outcome": "failure"}},
                                         {"range": {"@timestamp": window}}]}}
    top_ip = os_.search(index="logs-auth-*", body={
        "size": 0, "query": {"bool": {"filter": failed_on_day["bool"]["filter"]
                                      + [{"term": {"host.name": "jump-prod-02"}}]}},
        "aggs": {"ip": {"terms": {"field": "source.ip", "size": 1}}}})
    return [
        {"id": "prod-tier1-hosts",
         "ask": "How many production hosts run business-critical services?",
         "gold": _sql_one(persona, "SELECT count(*) FROM cmdb.assets a JOIN cmdb.services s "
                          "ON s.id = a.service_id WHERE a.environment = 'prod' AND s.tier = 1")},
        {"id": "depends-on-customer-db",
         "ask": "Which services depend on customer-db?",
         "gold": _sql_set(persona, "SELECT s.name FROM cmdb.service_dependencies d "
                          "JOIN cmdb.services s ON s.id = d.service_id "
                          "JOIN cmdb.services t ON t.id = d.depends_on_id "
                          "WHERE t.name = 'customer-db'")},
        {"id": "open-critical-prod",
         "ask": "How many open findings of critical vulnerabilities are there on production "
                "hosts?",
         "gold": _sql_one(persona, "SELECT count(*) FROM cmdb.asset_vulnerabilities f "
                          "JOIN cmdb.vulnerabilities v ON v.cve_id = f.cve_id "
                          "JOIN cmdb.assets a ON a.id = f.asset_id WHERE f.status = 'open' "
                          "AND v.severity = 'critical' AND a.environment = 'prod'")},
        {"id": "team-of-jump-service",
         "ask": "Which team owns the service that runs on jump-prod-02?",
         "gold": _sql_one(persona, "SELECT t.name FROM cmdb.assets a JOIN cmdb.services s "
                          "ON s.id = a.service_id JOIN cmdb.teams t ON t.id = s.owner_team_id "
                          "WHERE a.hostname = 'jump-prod-02'")},
        {"id": "owner-email",
         "ask": "What is the e-mail address of the person responsible for jump-prod-02?",
         "gold": "UNAVAILABLE" if persona == "l1_analyst" else _sql_one(
             persona, "SELECT p.email FROM cmdb.assets a JOIN cmdb.people p "
             "ON p.id = a.owner_id WHERE a.hostname = 'jump-prod-02'")},
        {"id": "failed-logins-day",
         "ask": f"How many failed SSH logins were recorded on {day} (UTC)?",
         "gold": os_.count(index="logs-auth-*", body={"query": failed_on_day})["count"]},
        {"id": "top-failing-ip",
         "ask": f"Which source IP address had the most failed SSH logins against jump-prod-02 "
                f"on {day}?",
         "gold": top_ip["aggregations"]["ip"]["buckets"][0]["key"]},
        {"id": "web-5xx",
         "ask": "How many HTTP requests in the web logs got a server error (5xx) response?",
         "gold": os_.count(index="logs-web-*", body={"query": {"range": {
             "http.response.status_code": {"gte": 500, "lt": 600}}}})["count"]},
        {"id": "active-incidents",
         "ask": "How many incidents are still active?",
         "gold": os_.count(index="incidents", body={"query": {"terms": {
             "incident.status": ["open", "in_progress"]}}})["count"]},
    ]


def grade(answer_text: str, gold) -> tuple[bool, str]:
    """Compare the model's `ANSWER:` line with the gold answer."""
    found = re.findall(r"ANSWER:\s*(.+)", answer_text or "")
    if not found:
        return False, ""
    got = found[-1].strip().strip("`*. ").strip()
    norm = lambda s: re.sub(r"\s+", " ", str(s).strip().strip("'\"").lower())  # noqa: E731
    if isinstance(gold, set):
        ok = {norm(x) for x in got.split(",") if x.strip()} == {norm(x) for x in gold}
    elif isinstance(gold, int):
        digits = re.sub(r"[,\s]", "", got)
        ok = digits.isdigit() and int(digits) == gold
    else:
        ok = norm(got) == norm(gold)
    return ok, got


# --- one conversation ---------------------------------------------------------------------------
async def ask(host, client, model, persona, question, max_steps=12) -> dict:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": question["ask"]}]
    tools = host.openai_tools() + [QUERY_TOOL]
    trace_id, steps, usage, t0 = uuid.uuid4().hex, 0, {"prompt": 0, "completion": 0}, time.time()
    counts = {"discovery": 0, "query": 0, "query_errors": 0}
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
            steps += 1
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                text = "error: the arguments were not valid JSON"
            else:
                if tc.function.name == "query":
                    counts["query"] += 1
                    text = await asyncio.to_thread(run_query, persona, args)
                    counts["query_errors"] += text.startswith("error:")
                else:
                    counts["discovery"] += 1
                    text, _ = await host.call(tc.function.name, args, "eval", trace_id, steps)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
    ok, got = grade(answer, question["gold"])
    return {"question": question["id"], "correct": ok, "answer": got,
            "gold": sorted(question["gold"]) if isinstance(question["gold"], set)
            else question["gold"],
            "tool_calls": steps, **counts, "prompt_tokens": usage["prompt"],
            "completion_tokens": usage["completion"], "seconds": round(time.time() - t0, 1)}


async def _no_forms(_server, _params):
    return unit2.ElicitResult(action="decline")


async def run(persona: str, conditions: list[str], repeats: int, on_result=None) -> list[dict]:
    client, model = llm()
    qs = questions(persona)
    results = []
    for condition in conditions:
        async with unit2.Host(CONDITIONS[condition], _no_forms,
                              env={"MCP_PERSONA": persona}) as host:
            for repeat in range(1, repeats + 1):
                for q in qs:
                    r = {"condition": condition, "repeat": repeat,
                         **await ask(host, client, model, persona, q)}
                    results.append(r)
                    if on_result:
                        on_result(r)
    return results


def summarise(results: list[dict]) -> list[dict]:
    """Per condition: accuracy per repeat -> mean and spread; mean cost per question."""
    out = []
    for condition in dict.fromkeys(r["condition"] for r in results):
        mine = [r for r in results if r["condition"] == condition]
        per_repeat = [statistics.mean(r["correct"] for r in mine if r["repeat"] == k)
                      for k in sorted({r["repeat"] for r in mine})]
        out.append({
            "condition": condition, "repeats": len(per_repeat),
            "accuracy": round(statistics.mean(per_repeat), 3),
            "min": round(min(per_repeat), 3), "max": round(max(per_repeat), 3),
            "prompt_tokens": round(statistics.mean(r["prompt_tokens"] for r in mine)),
            "tool_calls": round(statistics.mean(r["tool_calls"] for r in mine), 1),
            "query_errors": round(statistics.mean(r["query_errors"] for r in mine), 1),
            "seconds": round(statistics.mean(r["seconds"] for r in mine), 1),
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Query accuracy with no / raw / curated catalog.")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--conditions", nargs="+", default=list(CONDITIONS),
                        choices=list(CONDITIONS))
    parser.add_argument("--out", help="also write every result as JSONL here")
    args = parser.parse_args()
    load_env()
    if llm() is None:
        print("OPENAI_BASE_URL / OPENAI_API_KEY not set -- the eval needs the model. "
              "measure.py and catalog.py --check work without it.")
        return
    for q in questions(args.persona):
        print(f"  gold {q['id']:<24} {q['gold']}")

    def show(r):
        print(f"{r['condition']:<8} #{r['repeat']} {r['question']:<24} "
              f"{'ok ' if r['correct'] else 'BAD'} got={r['answer'][:40]!r:<42} "
              f"calls={r['tool_calls']:>2} tokens={r['prompt_tokens']:>6}")
    results = asyncio.run(run(args.persona, args.conditions, args.repeats, show))
    if args.out:
        Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in results))
    print()
    for s in summarise(results):
        print(f"{s['condition']:<8} accuracy {s['accuracy']:.0%} (range {s['min']:.0%}-"
              f"{s['max']:.0%} over {s['repeats']} runs)  ~{s['prompt_tokens']:,} prompt "
              f"tokens/question  {s['tool_calls']} calls  {s['query_errors']} query errors")


if __name__ == "__main__":
    main()
