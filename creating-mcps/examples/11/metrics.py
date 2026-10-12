"""
Unit 11: per-caller metrics, straight from the gateway's own telemetry, read as the auditor.

Every number here is an OpenSearch aggregation over mcp-telemetry-*: no metrics pipeline, no
second copy. The records were written for joining, and the same fields make good dimensions:
tool, caller, persona, verdict, rule, outcome, busy, server, pid.

    by_tool()       calls, refusals, error rate, p50 / p95 / p99 latency per tool (a refusal
                    isn't an error: the server did its job by saying no)
    by_caller()     calls per caller, and how many were denied, refused as busy, failed
    refusals()      why calls didn't run: the policy rule, or the limit that refused them
    over_time()     calls and errors per bucket, for a chart
    methods()       tools/call against everything else (discovery and listing aren't free)
    slo()           one service-level objective and how much of its error budget is spent

    python examples/11/metrics.py                 # the last hour, every table
    python examples/11/metrics.py --since now-15m --slo-ms 500
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import load_env, os_client  # noqa: E402

INDEX = "mcp-telemetry-*"
CALLS = {"term": {"method": "tools/call"}}
# Calls the policy refused, or a limit turned away: the caller's problem, not the server's.
REFUSED = [{"term": {"verdict": "deny"}}, {"exists": {"field": "busy"}},
           {"exists": {"field": "gate"}}]
FAILED = {"terms": {"outcome": ["error", "crash"]}}


def _search(body: dict, since: str) -> dict:
    query = body.pop("query", {"match_all": {}})
    body = {"size": 0, "query": {"bool": {"filter": [
        {"range": {"@timestamp": {"gte": since}}}, query]}}, **body}
    return os_client("auditor").search(index=INDEX, body=body, ignore_unavailable=True,
                                       allow_no_indices=True)


def _aggs(r: dict) -> dict:
    return r.get("aggregations") or {}


def _pct(agg: dict, p: str) -> float | None:
    v = (agg.get("values") or {}).get(p)
    return round(v, 1) if v is not None else None


def by_tool(since: str = "now-1h") -> list[dict]:
    r = _search({"query": CALLS, "aggs": {"tool": {"terms": {"field": "name", "size": 50}, "aggs": {
        "ms": {"percentiles": {"field": "ms", "percents": [50, 95, 99]}},
        "refused": {"filter": {"bool": {"should": REFUSED, "minimum_should_match": 1}}},
        "tool_errors": {"filter": {"bool": {"filter": [{"term": {"outcome": "tool_error"}}],
                                            "must_not": REFUSED}}},
        "failed": {"filter": FAILED}}}}}, since)
    rows = []
    for b in _aggs(r).get("tool", {}).get("buckets", []):
        n = b["doc_count"]
        rows.append({"tool": b["key"], "calls": n, "refused": b["refused"]["doc_count"],
                     "tool_errors": b["tool_errors"]["doc_count"],
                     "failed": b["failed"]["doc_count"],
                     "error_rate": round((b["tool_errors"]["doc_count"]
                                          + b["failed"]["doc_count"]) / n, 3),
                     "p50_ms": _pct(b["ms"], "50.0"), "p95_ms": _pct(b["ms"], "95.0"),
                     "p99_ms": _pct(b["ms"], "99.0")})
    return rows


def by_caller(since: str = "now-1h") -> list[dict]:
    r = _search({"query": CALLS, "aggs": {"caller": {
        "terms": {"field": "caller", "size": 100}, "aggs": {
            "persona": {"terms": {"field": "persona", "size": 1}},
            "denied": {"filter": {"term": {"verdict": "deny"}}},
            "busy": {"filter": {"exists": {"field": "busy"}}},
            "failed": {"filter": FAILED},
            "tokens": {"sum": {"field": "result_tokens"}},
            "ms": {"percentiles": {"field": "ms", "percents": [95]}}}}}}, since)
    rows = []
    for b in _aggs(r).get("caller", {}).get("buckets", []):
        persona = b["persona"]["buckets"]
        rows.append({"caller": b["key"], "persona": persona[0]["key"] if persona else None,
                     "calls": b["doc_count"], "denied": b["denied"]["doc_count"],
                     "busy": b["busy"]["doc_count"], "failed": b["failed"]["doc_count"],
                     "result_tokens": int(b["tokens"]["value"] or 0),
                     "p95_ms": _pct(b["ms"], "95.0")})
    return rows


def refusals(since: str = "now-1h") -> list[dict]:
    r = _search({"query": {"bool": {"filter": [CALLS], "should": REFUSED,
                                    "minimum_should_match": 1}},
                 "aggs": {"rule": {"filter": {"term": {"verdict": "deny"}}, "aggs": {
                              "by": {"terms": {"field": "rule", "size": 20}}}},
                          "busy": {"terms": {"field": "busy", "size": 20}},
                          "gate": {"terms": {"field": "gate", "size": 20}}}}, since)
    a = _aggs(r)
    rows = [{"why": f"policy: {b['key']}", "calls": b["doc_count"]}
            for b in a.get("rule", {}).get("by", {}).get("buckets", [])]
    rows += [{"why": f"busy: {b['key']}", "calls": b["doc_count"]}
             for b in a.get("busy", {}).get("buckets", [])]
    rows += [{"why": f"gate: {b['key']}", "calls": b["doc_count"]}
             for b in a.get("gate", {}).get("buckets", [])]
    return sorted(rows, key=lambda x: -x["calls"])


def over_time(since: str = "now-1h", interval: str = "10s") -> list[dict]:
    r = _search({"query": CALLS, "aggs": {"t": {
        "date_histogram": {"field": "@timestamp", "fixed_interval": interval,
                           "min_doc_count": 0},
        "aggs": {"bad": {"filter": {"bool": {"should": [
            FAILED, {"term": {"outcome": "tool_error"}}], "minimum_should_match": 1,
            "must_not": REFUSED}}},
            "refused": {"filter": {"bool": {"should": REFUSED, "minimum_should_match": 1}}},
            "ms": {"percentiles": {"field": "ms", "percents": [95]}}}}}}, since)
    return [{"t": b["key_as_string"], "calls": b["doc_count"], "errors": b["bad"]["doc_count"],
             "refused": b["refused"]["doc_count"], "p95_ms": _pct(b["ms"], "95.0")}
            for b in _aggs(r).get("t", {}).get("buckets", [])]


def methods(since: str = "now-1h") -> list[dict]:
    r = _search({"aggs": {"m": {"terms": {"field": "method", "size": 20}}}}, since)
    return [{"method": b["key"], "requests": b["doc_count"]}
            for b in _aggs(r).get("m", {}).get("buckets", [])]


def slo(since: str = "now-1h", target: float = 0.99, slo_ms: float = 1000) -> dict:
    """`target` of the calls the policy allowed answer without failing, within `slo_ms`."

    Refusals (denied, busy) are left out: the server did its job by saying no. A tool_error
    counts as good too: the tool answered, with a reason the model can act on. What's bad is a
    crash, a protocol error, or an answer slower than the objective."""
    allowed = {"bool": {"filter": [CALLS], "must_not": REFUSED}}
    r = _search({"query": allowed, "aggs": {
        "failed": {"filter": FAILED},
        "slow": {"filter": {"bool": {"must_not": [FAILED],
                                     "filter": [{"range": {"ms": {"gt": slo_ms}}}]}}}}}, since)
    total = r["hits"]["total"]["value"]
    a = _aggs(r)
    bad = a.get("failed", {}).get("doc_count", 0) + a.get("slow", {}).get("doc_count", 0)
    budget = (1 - target) * total
    return {"objective": f"{target:.1%} of allowed calls succeed within {slo_ms:g} ms",
            "allowed_calls": total, "bad": bad,
            "failed": a.get("failed", {}).get("doc_count", 0),
            "slow": a.get("slow", {}).get("doc_count", 0),
            "achieved": round(1 - bad / total, 4) if total else None,
            "budget_calls": round(budget, 1),
            "budget_spent": round(bad / budget, 2) if budget else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-caller metrics from the telemetry.")
    parser.add_argument("--since", default="now-1h")
    parser.add_argument("--slo-ms", type=float, default=1000)
    parser.add_argument("--target", type=float, default=0.99)
    args = parser.parse_args()
    load_env()
    for name, rows in (("methods", methods(args.since)), ("by tool", by_tool(args.since)),
                       ("by caller", by_caller(args.since)),
                       ("refusals", refusals(args.since))):
        print(f"-- {name}")
        for row in rows:
            print(json.dumps(row))
    print("-- slo")
    print(json.dumps(slo(args.since, args.target, args.slo_ms)))


if __name__ == "__main__":
    main()
