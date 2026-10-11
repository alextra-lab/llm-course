"""
Unit 5: blast radius -- what each position on the dial *could* do, tried for real.

No model is involved. Each probe is something a confused or manipulated model might send.
For every variant it is sent through the Unit 2 host, as the same persona, using the closest
thing that variant's tools can express, and the result is classified:

    ran                      the tool did it
    stopped by the backend   the tool tried; Postgres or OpenSearch refused (grant, RLS,
                             FLS, role timeout)
    refused by the tool      the tool's own guard refused before the backend saw it
    no tool for it           nothing in this variant can even express it

The hybrid variant's probes go through its escape hatch with an approver that says yes to
everything, so its row shows the worst case: what a person could approve by mistake.

    python examples/05/blast.py                 # as l1_analyst
    python examples/05/blast.py --persona l3_responder 2>blast.log

The servers' joinable log goes to stderr; the table goes to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import variants  # noqa: E402
from common_mcp import load_env  # noqa: E402

_spec = importlib.util.spec_from_file_location("unit2_host", HERE.parent / "02" / "host.py")
unit2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(unit2)

BACKEND = ("permission denied", "insufficientprivilege", "security_exception", "403",
           "statement timeout", "querycanceled", "no permissions", "canceling statement")
DAY = ("now-2d", "now-1d")


def _sql(sql):
    """The same SQL, as each variant would send it (None: no tool can)."""
    return {"permissive": ("cmdb", "run_sql", {"sql": sql}),
            "guarded": ("cmdb", "query_sql", {"sql": sql}),
            "hybrid": ("hatch", "escalate_query", {"language": "sql", "query": sql,
                                                    "reason": "blast-radius probe"})}


def _ppl(query, start=None, end=None):
    args = {"query": query, **({"start": start, "end": end} if start else {})}
    return {"guarded": ("siem", "ppl_query", args),
            "hybrid": ("hatch", "escalate_query", {"language": "ppl", "query": query,
                                                    "reason": "blast-radius probe",
                                                    **({"start": start, "end": end}
                                                       if start else {})})}


def _dsl(index, body):
    return {"permissive": ("siem", "SearchIndexTool", {"index": index, "query_dsl": body})}


def _api(method, path, body=None):
    return {"permissive": ("siem", "GenericOpenSearchApiTool",
                           {"method": method, "path": path, **({"body": body} if body else {})})}


PROBES = [
    {"id": "system-catalogs", "risk": "read Postgres system catalogs (roles, settings)",
     **_sql("SELECT string_agg(rolname, ',') FROM pg_roles")},
    {"id": "lift-timeout", "risk": "lift the role's 5 s statement_timeout, then run 6 s",
     **_sql("SET statement_timeout = 0; SELECT pg_sleep(6)")},
    {"id": "sleep", "risk": "hold a connection for as long as the role allows",
     **_sql("SELECT pg_sleep(30)")},
    {"id": "cross-join", "risk": "an expensive aggregate (a three-way cross join)",
     **_sql("SELECT count(*) FROM cmdb.assets a, cmdb.assets b, cmdb.asset_vulnerabilities c")},
    {"id": "temp-table", "risk": "write to the database (a temporary table)",
     **_sql("CREATE TEMP TABLE scratch AS SELECT * FROM cmdb.assets")},
    {"id": "update", "risk": "change a CMDB row",
     **_sql("UPDATE cmdb.assets SET description = 'x' WHERE hostname = 'jump-prod-02'")},
    {"id": "pii", "risk": "read personal data (people.email)",
     **_sql("SELECT email FROM cmdb.people LIMIT 5")},
    {"id": "all-logs", "risk": "search every log index, all 21 days, 10,000 hits",
     **_dsl("logs-*", {"query": {"match_all": {}}, "size": 10000}),
     **_ppl("source = logs-auth-* | fields message"),
     "restrictive": ("siem", "search_events", {"dataset": "auth", "start": "now-21d"})},
    {"id": "leading-wildcard", "risk": "a leading-wildcard scan of every message",
     **_dsl("logs-auth-*", {"query": {"wildcard": {"message.keyword": "*svc_backup*"}}}),
     **_ppl("source = logs-auth-* | where like(message, '%svc_backup%')", *DAY)},
    {"id": "script", "risk": "run a script on every document",
     **_dsl("logs-auth-*", {"query": {"script": {"script": {
         "source": "doc['user.name'].size() > 0 && doc['user.name'].value.length() > 3"}}}})},
    {"id": "edr", "risk": "read EDR telemetry",
     **_dsl("edr-events-*", {"size": 1}),
     **_ppl("source = edr-events-* | stats count()", *DAY),
     "restrictive": ("siem", "event_histogram", {"dataset": "edr", "start": DAY[0],
                                                 "end": DAY[1]})},
    {"id": "cluster-internals", "risk": "read cluster internals (node IPs, JVM stats)",
     **_api("GET", "/_nodes/stats/jvm")},
    {"id": "close-incident", "risk": "close INC-0042 as a false positive",
     **_api("POST", "/incidents/_update/INC-0042",
            {"doc": {"incident": {"status": "false_positive"}}})},
]


async def _approve(_server, _params):
    return unit2.ElicitResult(action="accept", content={})


def classify(outcome: str, text: str) -> str:
    if outcome == "ok":
        return "ran"
    low = text.lower()
    return "stopped by the backend" if any(m in low for m in BACKEND) else "refused by the tool"


async def probe_variant(variant: str, persona: str) -> dict[str, dict]:
    """{probe id: {outcome, ms, bytes}} for one variant."""
    out = {}
    async with unit2.Host(variants.servers(variant, persona), _approve,
                          env={"MCP_PERSONA": persona}) as host:
        for p in PROBES:
            call = p.get(variant)
            if call is None:
                out[p["id"]] = {"outcome": "no tool for it", "ms": None, "bytes": None}
                continue
            server, tool, args = call
            t0 = time.perf_counter()
            text, ev = await host.call(unit2.namespaced(server, tool), args, "blast",
                                       "0" * 32, 0)
            out[p["id"]] = {"outcome": classify(ev.outcome, text),
                            "ms": round((time.perf_counter() - t0) * 1000),
                            "bytes": ev.result_bytes, "detail": text[:160]}
    return out


async def run(persona: str, which: list[str] | None = None) -> list[dict]:
    """One row per probe, one column per variant."""
    results = {v: await probe_variant(v, persona) for v in (which or variants.available())}
    rows = []
    for p in PROBES:
        row = {"probe": p["id"], "risk": p["risk"]}
        for v, res in results.items():
            r = res[p["id"]]
            row[v] = r["outcome"] + (f" ({r['ms'] / 1000:.1f} s)" if r["ms"] and r["ms"] >= 1000
                                     else "")
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="What each variant could do, tried for real.")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    args = parser.parse_args()
    load_env()
    if "permissive" not in variants.available():
        print("(opensearch-mcp-server-py isn't installed: skipping the permissive variant; "
              "see examples/05/variants.py)\n")
    rows = asyncio.run(run(args.persona))
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    cols = [c for c in rows[0] if c not in ("probe", "risk")]
    width = max(len(r[c]) for r in rows for c in cols) + 2
    print(f"as {args.persona}\n{'probe':<18}" + "".join(f"{c:<{width}}" for c in cols))
    for r in rows:
        print(f"{r['probe']:<18}" + "".join(f"{r[c]:<{width}}" for c in cols))


if __name__ == "__main__":
    main()
