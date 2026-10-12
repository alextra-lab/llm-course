"""
Unit 9: four OpenSearch choices that change what a log question costs, measured.

Each experiment runs the same request many times as an L3 responder (impersonated through the
gateway login, as the servers do) and reports the median and p95 latency, plus whatever else
it changes. Run it on the large seed (`python env/env.py reset --size large`) to see the
differences clearly; on the small seed they're there, but small.

    rounding    the same 7-day histogram with "now" exact to the millisecond, and rounded to
                the minute, counting OpenSearch's shard request cache hits. As admin (if this
                machine has the login) and as a persona: field- and document-level security
                turn the request cache off for the persona
    indexes     the same 1-day search against the daily indexes it needs (Unit 4) and against
                the wildcard `logs-auth-*`, which asks every shard to rule itself out
    totals      a page of 20 events with an exact total, the default (counts to 10,000), and
                no total at all
    source      a page of 100 events with every field, and with the five the tool shows

    python examples/09/os_perf.py --runs 30
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "04"), str(HERE.parent)]
import windows  # noqa: E402  (Unit 4)
from common_mcp import acting_for, load_env, os_client  # noqa: E402

IGNORE = {"ignore_unavailable": "true"}         # a day with no index yet is no error
FIELDS = ["@timestamp", "event.outcome", "user.name", "source.ip", "host.name"]


def _range(start: datetime, end: datetime) -> dict:
    return {"range": {"@timestamp": {"gte": start.isoformat(), "lt": end.isoformat()}}}


def timed(fn, runs: int) -> tuple[dict, object]:
    took, last = [], None
    for _ in range(runs):
        t = time.perf_counter()
        last = fn()
        took.append(time.perf_counter() - t)
    took.sort()
    return {"p50_ms": round(statistics.median(took) * 1000, 1),
            "p95_ms": round(took[int(0.95 * (len(took) - 1))] * 1000, 1)}, last


def cache_hits(client, index: str) -> int:
    stats = client.indices.stats(index=index, metric="request_cache")
    return stats["_all"]["total"]["request_cache"]["hit_count"]


def rounding(client, runs: int, who: str = "as l3_responder") -> list[dict]:
    rows = []
    for label, step in ((f"{who}, now exact", 0), (f"{who}, now rounded to the minute", 60)):
        def histogram(step=step):
            now = datetime.now(UTC)
            if step:
                now = datetime.fromtimestamp(now.timestamp() // step * step, UTC)
            w = windows.Window(now - timedelta(days=7), now, "UTC")
            return client.search(index=",".join(windows.daily_indexes("logs-auth", w)),
                                 body={"size": 0, "query": _range(w.start, w.end), "aggs": {
                                     "t": {"date_histogram": {"field": "@timestamp",
                                                              "fixed_interval": "1h"}}}},
                                 params=IGNORE)
        before = cache_hits(client, "logs-auth-*")
        stats, _ = timed(histogram, runs)
        rows.append({"experiment": "rounding", "variant": label, **stats,
                     "request cache hits": cache_hits(client, "logs-auth-*") - before})
    return rows


def indexes(client, runs: int) -> list[dict]:
    now = windows.current_time()
    w = windows.Window(now - timedelta(days=3), now - timedelta(days=2), "UTC")
    body = {"size": 20, "query": _range(w.start, w.end), "sort": [{"@timestamp": "desc"}],
            "_source": FIELDS}
    rows = []
    for label, index in (("the daily indexes it needs",
                          ",".join(windows.daily_indexes("logs-auth", w))),
                         ("logs-auth-* (every day)", "logs-auth-*")):
        stats, r = timed(lambda index=index: client.search(index=index, body=body, params=IGNORE),
                         runs)
        rows.append({"experiment": "indexes", "variant": label, **stats,
                     "shards": r["_shards"]["total"], "skipped": r["_shards"].get("skipped", 0)})
    return rows


def totals(client, runs: int) -> list[dict]:
    now = windows.current_time()
    w = windows.Window(now - timedelta(days=7), now, "UTC")
    index = ",".join(windows.daily_indexes("logs-auth", w))
    rows = []
    for label, track in (("exact total", True), ("default (up to 10,000)", None),
                         ("no total", False)):
        body = {"size": 20, "query": _range(w.start, w.end), "sort": [{"@timestamp": "desc"}],
                "_source": FIELDS}
        if track is not None:
            body["track_total_hits"] = track
        stats, r = timed(lambda body=body: client.search(index=index, body=body, params=IGNORE),
                         runs)
        total = r["hits"].get("total")
        rows.append({"experiment": "totals", "variant": label, **stats,
                     "total": f"{total['value']:,} ({total['relation']})" if total else "-"})
    return rows


def source(client, runs: int) -> list[dict]:
    now = windows.current_time()
    w = windows.Window(now - timedelta(days=1), now, "UTC")
    index = ",".join(windows.daily_indexes("logs-web", w))
    rows = []
    for label, src in (("every field", True), ("the five it shows", FIELDS)):
        body = {"size": 100, "query": _range(w.start, w.end), "_source": src,
                "sort": [{"@timestamp": "desc"}], "track_total_hits": False}
        stats, r = timed(lambda body=body: client.search(index=index, body=body, params=IGNORE),
                         runs)
        rows.append({"experiment": "source", "variant": label, **stats,
                     "response KB": round(len(json.dumps(r)) / 1024, 1)})
    return rows


def admin_client():
    """The estate's admin login, if this machine has it (your own estate; not a shared one)."""
    try:
        sys.path.insert(0, str(HERE.parents[1] / "env"))
        import env as estate

        client = estate.admin_os()
        client.info()
        return client
    except Exception:  # noqa: BLE001
        return None


def run(runs: int = 30) -> list[dict]:
    load_env()
    rows = []
    admin = admin_client()
    if admin is not None:        # no field- or document-level security: the cache can work
        rows += rounding(admin, runs, "as admin")
    with acting_for("l3_responder"):
        client = os_client("l3_responder")
        return (rows + rounding(client, runs) + indexes(client, runs) + totals(client, runs)
                + source(client, runs))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=30)
    args = parser.parse_args()
    for row in run(args.runs):
        print(json.dumps(row))


if __name__ == "__main__":
    main()
