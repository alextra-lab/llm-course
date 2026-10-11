"""
Unit 4: what each way of looking at the incident day costs.

Asks one question -- "what happened with failed logins around INC-0042?" -- five ways, as one
persona, and measures what each answer would put into a model's context and how much of the
cluster it touched:

  - a naive search tool that returns raw documents (OpenSearch's size cap is 10,000), with
    and without a time window;
  - the Unit 4 server's search_events (one shaped page), event_histogram and top_values.

The naive rows call OpenSearch directly, as the persona, the way a thin "search" tool would.
The Unit 4 rows go through the Unit 2 host, so the bytes are exactly what a model would get.
Tokens are estimated at 4 bytes per token.

    python examples/04/cost.py
    python examples/04/cost.py --persona l3_responder
"""

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import windows  # noqa: E402
from common_mcp import load_env, os_client  # noqa: E402

_spec = importlib.util.spec_from_file_location("unit2_host", HERE.parent / "02" / "host.py")
unit2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(unit2)

SERVERS = {"siem": HERE / "siem_server.py"}
FAILED = {"term": {"event.outcome": "failure"}}


def tokens(n_bytes: int) -> int:
    return round(n_bytes / 4)


async def _declined(_server, _params):
    return unit2.ElicitResult(action="decline")


def _row(how, text, *, matched, returned, shards):
    n = len(text.encode())
    return {"how": how, "matched": matched, "returned": returned, "bytes": n,
            "tokens": tokens(n), "shards": shards}


def naive(persona: str, w: windows.Window | None) -> dict:
    """A thin search tool: the pattern, raw documents, as many as OpenSearch will give."""
    query = {"bool": {"filter": [FAILED] + ([w.range_query()] if w else [])}}
    r = os_client(persona).search(index="logs-auth-*", size=10_000,
                                  body={"query": query, "track_total_hits": True})
    total = r["hits"]["total"]["value"]
    return _row("raw documents, " + ("the incident's day" if w else "no window"),
                json.dumps([h["_source"] for h in r["hits"]["hits"]]), matched=total,
                returned=len(r["hits"]["hits"]), shards=r["_shards"]["total"])


async def measure(persona: str) -> list[dict]:
    rows = []
    async with unit2.Host(SERVERS, _declined, env={"MCP_PERSONA": persona}) as host:
        async def call(tool, args):
            text, _ = await host.call(f"siem__{tool}", args, "cost", "0" * 32, 0)
            if text.startswith("error:"):
                raise RuntimeError(text)
            return text, json.loads(text)

        _, incident = await call("get_incident", {"incident_id": "INC-0042"})
        created = windows.parse_time(incident["created"])
        day_start = created.replace(hour=0, minute=0, second=0)
        day = windows.Window(day_start, day_start + timedelta(days=1), "UTC")
        win = {"start": windows.iso(day.start), "end": windows.iso(day.end)}
        failed = {"filters": {"event.outcome": "failure"}}
        named = len(windows.daily_indexes("logs-auth", day))     # one shard per daily index

        rows.append(naive(persona, None))
        rows.append(naive(persona, day))
        text, r = await call("search_events", {"dataset": "auth", **win, **failed})
        rows.append(_row("search_events, one page", text, matched=r["total"],
                         returned=r["returned"], shards=named))
        text, r = await call("event_histogram", {"dataset": "auth", **win, **failed})
        rows.append(_row(f"event_histogram ({r['interval']} buckets)", text,
                         matched=r["total"], returned=len(r["buckets"]), shards=named))
        text, r = await call("top_values", {"dataset": "auth", "field": "source.ip", **win,
                                            **failed})
        rows.append(_row("top_values source.ip", text, matched=r["total"],
                         returned=len(r["values"]), shards=named))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="The cost of raw hits vs aggregations.")
    parser.add_argument("--persona", default="l1_analyst")
    args = parser.parse_args()
    load_env()
    rows = asyncio.run(measure(args.persona))
    print(f"{'how':<34} {'matched':>8} {'returned':>9} {'bytes':>11} {'~tokens':>9} "
          f"{'shards':>7}")
    for r in rows:
        print(f"{r['how']:<34} {r['matched']:>8,} {r['returned']:>9,} {r['bytes']:>11,} "
              f"{r['tokens']:>9,} {r['shards']:>7}")


if __name__ == "__main__":
    main()
