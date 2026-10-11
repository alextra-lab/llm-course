"""
Unit 8: price a query before it runs -- cost gates for Postgres and OpenSearch.

A timeout stops a query that's already too expensive, after it has used the backend for the
whole budget. A gate refuses it first, from an estimate that costs almost nothing:

    Postgres    EXPLAIN (no ANALYZE: the query isn't run) gives the planner's estimated total
                cost and rows. Refuse above a ceiling. Useful wherever the SQL varies: Unit
                5's guarded tier, or the hatch.
    OpenSearch  the daily indexes a window touches are known before the search (Unit 4), and
                so are their document counts (index stats). Refuse a search whose indexes
                hold more documents than a ceiling: that's an upper bound on what it scans.

Estimates are estimates: the planner's cost unit is arbitrary (calibrate it on your own
queries, as the lab does), and a document count ignores filters. A gate is for the
order-of-magnitude mistakes; the timeout still catches the rest.

    python examples/08/cost_gates.py           # price a few queries, as an L3 responder
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "04"), str(HERE.parent / "05"), str(HERE.parent)]
import windows  # noqa: E402  (Unit 4)
from common_mcp import load_env, os_client, persona, pg_connect  # noqa: E402

PG_MAX_COST = 50_000          # planner cost units: calibrate on your own queries
PG_MAX_ROWS = 10_000          # estimated rows: a cheap plan can still return a flood
OS_MAX_DOCS = 5_000_000       # documents in the indexes a search would touch
PREFIX = {"auth": "logs-auth", "web": "logs-web", "syslog": "logs-syslog", "edr": "edr-events"}


class TooExpensive(Exception):
    pass


def pg_cost(conn, sql: str, params=None) -> dict:
    """The planner's estimate for `sql`, without running it."""
    plan = conn.execute(f"EXPLAIN (FORMAT JSON) {sql}", params).fetchone()[0][0]["Plan"]
    return {"cost": plan["Total Cost"], "rows": plan["Plan Rows"], "node": plan["Node Type"]}


def pg_gate(conn, sql: str, params=None, max_cost: float = PG_MAX_COST,
            max_rows: int = PG_MAX_ROWS) -> dict:
    est = pg_cost(conn, sql, params)
    if est["cost"] > max_cost or est["rows"] > max_rows:
        raise TooExpensive(
            f"this query is estimated at {est['cost']:,.0f} cost units and ~{est['rows']:,} "
            f"rows (the limits are {max_cost:,} and {max_rows:,} rows). Add a filter, join "
            "on keys, or aggregate instead of listing.")
    return est


_DOC_COUNTS: dict[str, tuple[float, dict[str, int]]] = {}


def os_scan(client, dataset: str, start: str, end: str = "now", tz: str = "UTC") -> dict:
    """The indexes a log query's window touches, and how many documents they hold."""
    w = windows.window(start, end, tz=tz, max_span=windows.RETENTION)
    names = windows.daily_indexes(PREFIX[dataset], w)
    pattern, now = PREFIX[dataset] + "-*", time.monotonic()
    if pattern not in _DOC_COUNTS or now - _DOC_COUNTS[pattern][0] > 60:   # cached a minute
        rows = client.cat.indices(index=pattern, format="json", h="index,docs.count")
        _DOC_COUNTS[pattern] = (now, {r["index"]: int(r["docs.count"] or 0) for r in rows})
    counts = _DOC_COUNTS[pattern][1]
    return {"indexes": len(names), "docs": sum(counts.get(n, 0) for n in names),
            "span": w.describe()}


def os_gate(client, dataset: str, start: str, end: str = "now", tz: str = "UTC",
            max_docs: int = OS_MAX_DOCS) -> dict:
    est = os_scan(client, dataset, start, end, tz)
    if est["docs"] > max_docs:
        raise TooExpensive(
            f"this window covers {est['indexes']} daily indexes holding {est['docs']:,} "
            f"{dataset} events (the limit is {max_docs:,}). Narrow the window.")
    return est


EXAMPLES = [
    ("one host by name", "SELECT hostname, os FROM cmdb.assets WHERE hostname = %s",
     ("jump-prod-02",)),
    ("hosts per service", "SELECT s.name, count(*) FROM cmdb.assets a JOIN cmdb.services s "
     "ON s.id = a.service_id GROUP BY s.name", None),
    ("every asset with every CVE (no join key)",
     "SELECT a.hostname, v.cve_id FROM cmdb.assets a, cmdb.vulnerabilities v", None),
    ("assets x assets x people (a cross join)",
     "SELECT count(*) FROM cmdb.assets a, cmdb.assets b, cmdb.people p", None),
]


def main() -> None:
    load_env()
    with pg_connect("l3_responder") as conn:
        for label, sql, params in EXAMPLES:
            est = pg_cost(conn, sql, params)
            verdict = "refused" if est["cost"] > PG_MAX_COST or est["rows"] > PG_MAX_ROWS else (
                "ok")
            print(f"  {verdict:<8} cost {est['cost']:>14,.0f}  rows {est['rows']:>12,}  {label}")
    client = os_client(persona())
    for dataset, start in (("auth", "now-1h"), ("auth", "now-7d"), ("syslog", "now-21d")):
        est = os_scan(client, dataset, start)
        print(f"  {dataset:<7} {start:<8} {est['indexes']:>3} indexes  {est['docs']:>9,} docs")


if __name__ == "__main__":
    main()
