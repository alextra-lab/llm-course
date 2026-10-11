"""
Unit 6: what an error message is worth -- tasks that invite a mistake, answered end to end.

Each task is phrased the way people ask, which is not the way the tools want it: a hostname
with its domain, a service by its description, a CVE id with spaces, an incident by its
number, a window too wide for a search. The first call usually fails. What happens next
depends on what the refusal said.

Each task runs as a full conversation (Unit 5's loop) and is scored on its `ANSWER:` line
against a gold answer computed live, under conditions written style:errors:

    original:instructive   the tools as shipped
    original:generic       the same tools, every refusal replaced by "invalid request"
    lean:instructive       this unit's descriptions (fewer mistakes to recover from?)

    python examples/06/recovery.py --repeats 3
    python examples/06/recovery.py --conditions original:generic lean:generic
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "05"), str(HERE.parent)]
import styles  # noqa: E402
from common_mcp import llm, load_env, os_client, pg_connect  # noqa: E402
from grid import _approve, ask, incident_day, unit2  # noqa: E402  (Unit 5)

CONDITIONS = ("original:instructive", "original:generic", "lean:instructive")


def _one(persona, sql, *args):
    with pg_connect(persona) as conn:
        return conn.execute(sql, args).fetchone()[0]


def tasks(persona: str) -> list[dict]:
    """Each: {id, ask, gold, menu}. menu is unused here (grid.ask expects it)."""
    os_ = os_client(persona)
    day = incident_day(os_)
    inc = os_.search(index="incidents", body={"query": {"term": {"incident.id": "INC-0042"}}})
    three_days = os_.count(index="logs-auth-*", body={"query": {"bool": {"filter": [
        {"term": {"event.outcome": "failure"}}, {"term": {"host.name": "jump-prod-02"}},
        {"range": {"@timestamp": {"gte": f"{day - timedelta(days=2)}T00:00:00Z",
                                  "lt": f"{day + timedelta(days=1)}T00:00:00Z"}}}]}}})
    top = os_.search(index="logs-auth-*", body={"size": 0, "query": {"bool": {"filter": [
        {"term": {"event.outcome": "failure"}}, {"term": {"host.name": "jump-prod-02"}},
        {"range": {"@timestamp": {"gte": f"{day}T00:00:00Z",
                                  "lt": f"{day + timedelta(days=1)}T00:00:00Z"}}}]}},
        "aggs": {"ip": {"terms": {"field": "source.ip", "size": 1}}}})
    return [
        {"id": "fqdn-owner", "ask": "Who owns jump-prod-02.corp.example? Give their name.",
         "gold": _one(persona, "SELECT p.full_name FROM cmdb.assets a JOIN cmdb.people p "
                      "ON p.id = a.owner_id WHERE a.hostname = 'jump-prod-02'")},
        {"id": "service-by-description",
         "ask": "How many hosts run the customer database service?",
         "gold": _one(persona, "SELECT count(*) FROM cmdb.assets a JOIN cmdb.services s "
                      "ON s.id = a.service_id WHERE s.name = 'customer-db'")},
        {"id": "cve-with-spaces",
         "ask": "How many production hosts still have CVE 2099 1068 open?",
         "gold": _one(persona, "SELECT count(DISTINCT a.id) FROM cmdb.asset_vulnerabilities f "
                      "JOIN cmdb.assets a ON a.id = f.asset_id WHERE f.cve_id = "
                      "'CVE-2099-1068' AND f.status = 'open' AND a.environment = 'prod'")},
        {"id": "incident-by-number", "ask": "Which host is incident 42 about?",
         "gold": inc["hits"]["hits"][0]["_source"]["host"]["name"]},
        {"id": "field-by-description",
         "ask": f"Which source address had the most failed logins against jump-prod-02 on "
                f"{day}?",
         "gold": top["aggregations"]["ip"]["buckets"][0]["key"]},
        {"id": "window-too-wide",
         "ask": f"Count the failed logins against jump-prod-02 over the three days from "
                f"{day - timedelta(days=2)} to {day} inclusive (UTC).",
         "gold": three_days["count"]},
    ]


async def run(persona: str, conditions: list[str], repeats: int, on_result=None) -> list[dict]:
    client, model = llm()
    ts = [{**t, "menu": "-"} for t in tasks(persona)]
    results = []
    for condition in conditions:
        style, errors = condition.split(":")
        async with unit2.Host(styles.server_table(style, persona, errors), _approve) as host:
            for repeat in range(1, repeats + 1):
                for t in ts:
                    r = {"condition": condition, "repeat": repeat,
                         **await ask(host, client, model, t)}
                    results.append(r)
                    if on_result:
                        on_result(r)
    return results


def summarise(results: list[dict]) -> list[dict]:
    out = []
    for condition in dict.fromkeys(r["condition"] for r in results):
        mine = [r for r in results if r["condition"] == condition]
        per = [statistics.mean(r["correct"] for r in mine if r["repeat"] == k)
               for k in sorted({r["repeat"] for r in mine})]
        out.append({"condition": condition, "repeats": len(per),
                    "success": round(statistics.mean(per), 3),
                    "min": round(min(per), 3), "max": round(max(per), 3),
                    "tool_calls": round(statistics.mean(r["tool_calls"] for r in mine), 1),
                    "errors": round(statistics.mean(r["errors"] for r in mine), 1),
                    "prompt_tokens": round(statistics.mean(r["prompt_tokens"] for r in mine))})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Recovery from mistakes, per error style.")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    parser.add_argument("--out", help="also write every result as JSONL here")
    args = parser.parse_args()
    load_env()
    for c in args.conditions:
        style, _, errors = c.partition(":")
        if style not in styles.STYLES or errors not in ("instructive", "generic"):
            parser.error(f"condition {c!r}: use <style>:<instructive|generic>")
    for t in tasks(args.persona):
        print(f"  gold {t['id']:<24} {t['gold']}")
    if llm() is None:
        print("\nOPENAI_BASE_URL / OPENAI_API_KEY not set -- the eval needs the model. "
              "errors.py grades the messages themselves without it.")
        return

    def show(r):
        print(f"{r['condition']:<22} #{r['repeat']} {r['task']:<24} "
              f"{'ok ' if r['correct'] else 'BAD'} got={r['answer'][:28]!r:<30} "
              f"calls={r['tool_calls']:>2} errors={r['errors']}")
    results = asyncio.run(run(args.persona, args.conditions, args.repeats, show))
    if args.out:
        Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in results))
    print()
    for s in summarise(results):
        print(f"{s['condition']:<22} success {s['success']:.0%} (range {s['min']:.0%}-"
              f"{s['max']:.0%} over {s['repeats']} runs)  {s['tool_calls']} calls  "
              f"{s['errors']} errors  ~{s['prompt_tokens']:,} tokens/task")


if __name__ == "__main__":
    main()
