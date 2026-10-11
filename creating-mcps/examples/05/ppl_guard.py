"""
Unit 5: the guarded middle of the dial for OpenSearch -- PPL, bounded by construction and
checked on its compiled plan.

PPL is OpenSearch's piped query language:

    source = logs-auth-* | where `event.outcome` = 'failure' | stats count() by `source.ip`

Models write it more reliably than Query DSL, and a person can read it in a log. The guard
works in three steps:

  1. **Check the text** for what only the text shows. The source must be one index pattern on
     the persona's allowlist (from Unit 3's catalog). Every command must be on a short
     allowlist: where, fields, stats, sort, head and a few more. No join, lookup, ml, regex
     or parse.
  2. **Bound it by construction.** The caller passes the window (start, end) as arguments,
     not inside the query. The guard rewrites the source to the daily indexes that window
     touches (as Unit 4 does) and adds the exact time filter itself. Every query also ends in
     `| head MAX_ROWS`.
  3. **Check the compiled plan.** PPL's `_explain` returns the OpenSearch request the query
     compiles to. The guard refuses a wildcard, prefix or regexp query that starts with a
     wildcard (a full scan of the terms dictionary), and a plan that would fetch more than
     MAX_ROWS documents to post-process them on the coordinator.

Why bound the window by construction instead of checking the plan for a time range? On
OpenSearch 3.9, PPL's Calcite engine compiles many ordinary filters into its own scripts:
an IP comparison, any function call, and sometimes the time range along with them. The plan
then has *no* range query to check, even though the query has a perfectly good time filter.
A rule like "no scripts, and a range on @timestamp" would refuse half of the normal queries.
Naming the indexes bounds what any script can touch, whatever the plan looks like.

    python examples/05/ppl_guard.py "source = logs-auth-* | stats count()" now-2d now-1d
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "04"), str(HERE.parent / "03"), str(HERE.parent)]
import catalog  # noqa: E402  (Unit 3)
import windows  # noqa: E402  (Unit 4)

MAX_ROWS = 200
MAX_SPAN = timedelta(days=7)
UNTIMED = {"incidents"}                     # small, not time-partitioned: no window needed
COMMANDS = {"where", "fields", "stats", "sort", "head", "dedup", "top", "rare", "rename",
            "eval", "timechart", "fillnull"}
_SOURCE = re.compile(r"^\s*(?:search\s+)?source\s*=\s*`?([\w.*\-]+)`?\s*(?=\||$)", re.I)


class PplRefused(ValueError):
    """The query may not run. The message says what to change."""


def allowed_indexes(persona: str) -> list[str]:
    return [n for n, d in catalog.DATASETS.items()
            if d["backend"] == "opensearch" and persona in d["allowed"]]


def _stages(query: str) -> list[str]:
    """Split on pipes that aren't inside quotes."""
    stages, buf, quote = [], "", None
    for ch in query:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"`":
            quote = ch
        elif ch == "|":
            stages.append(buf.strip())
            buf = ""
            continue
        buf += ch
    stages.append(buf.strip())
    return [s for s in stages if s]


def check_text(query: str, persona: str) -> tuple[str, list[str]]:
    """Step 1. Returns the source index pattern and the stages after it."""
    stages = _stages(query)
    m = _SOURCE.match(stages[0] if stages else "")
    if not m:
        raise PplRefused("start with `source = <index pattern>`, e.g. "
                         "source = logs-auth-* | where ... | stats count()")
    index = m.group(1)
    mine = allowed_indexes(persona)
    if index not in mine:
        raise PplRefused(f"{index!r} isn't an index pattern you may query. Yours: "
                         f"{', '.join(mine)}. Pass the time window as start/end, not as "
                         "daily index names.")
    for stage in stages[1:]:
        cmd = stage.split()[0].lower()
        if cmd not in COMMANDS:
            raise PplRefused(f"the `{cmd}` command isn't allowed. Allowed: "
                             f"{', '.join(sorted(COMMANDS))}.")
    return index, stages[1:]


_EXISTING: dict = {}


def _existing(client, pattern: str) -> set[str]:
    """Which daily indexes exist (cached for a minute): PPL fails on a missing one."""
    hit = _EXISTING.get(pattern)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    names = {r["index"] for r in client.cat.indices(index=pattern, format="json", h="index")}
    _EXISTING[pattern] = (time.monotonic(), names)
    return names


def bounded(client, index: str, stages: list[str], start: str | None, end: str | None) -> str:
    """Step 2. The query as it will run: daily indexes, the exact window, a row cap."""
    if index in UNTIMED:
        source = index
        head = []
    else:
        if not start:
            raise PplRefused("log queries need a window: pass start and end (ISO 8601 or "
                             f"now-24h), at most {MAX_SPAN.days} days.")
        try:
            w = windows.window(start, end or "now", max_span=MAX_SPAN, tool="ppl_query")
        except windows.WindowError as e:
            raise PplRefused(str(e)) from None
        days = [n for n in windows.daily_indexes(index.removesuffix("*").rstrip("-"), w)
                if n in _existing(client, index)]
        if not days:
            raise PplRefused("no log index covers this window, so nothing can match.")
        source = ",".join(days)
        fmt = "%Y-%m-%d %H:%M:%S"
        head = [f"where `@timestamp` >= '{w.start:{fmt}}' and `@timestamp` < '{w.end:{fmt}}'"]
    return " | ".join([f"source = {source}", *head, *stages, f"head {MAX_ROWS}"])


def _requests(explain: dict) -> list[dict]:
    """Every OpenSearch request body in an explain result's physical plan."""
    plan = (explain.get("calcite") or {}).get("physical") or json.dumps(explain)
    out, start = [], 0
    while (i := plan.find("sourceBuilder=", start)) != -1:
        body, end = json.JSONDecoder().raw_decode(plan, i + len("sourceBuilder="))
        total = re.match(r",\s*requestedTotalSize=(\d+)", plan[end:])
        body["_requested_total"] = int(total.group(1)) if total else body.get("size", 0)
        out.append(body)
        start = end
    return out


def _walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def check_plan(requests: list[dict]) -> None:
    """Step 3: what the cluster would actually run."""
    if not requests:
        raise PplRefused("this query can't be pushed down to OpenSearch as a search; "
                         "simplify it (filter with where, aggregate with stats).")
    for body in requests:
        for key, value in _walk(body):
            if key in ("wildcard", "prefix", "regexp") and isinstance(value, dict):
                for spec in value.values():
                    pattern = (spec.get("wildcard") or spec.get("value")
                               if isinstance(spec, dict) else spec)
                    if key == "regexp" or str(pattern or "").startswith(("*", "?")):
                        raise PplRefused("leading wildcards and regular expressions scan "
                                         "every term in the index. Match an exact value, or a "
                                         "prefix like 'svc_%'.")
        if body.get("size", 0) and body["_requested_total"] > MAX_ROWS:
            raise PplRefused(f"this query would fetch {body['_requested_total']} documents "
                             f"to post-process them (max {MAX_ROWS}). Aggregate with stats, "
                             "or narrow the filter.")


def run(client, query: str, persona: str, start: str | None = None,
        end: str | None = None) -> dict:
    """Check, bound and run `query` with the persona's client."""
    index, stages = check_text(query, persona)
    final = bounded(client, index, stages, start, end)
    try:
        explain = client.transport.perform_request("POST", "/_plugins/_ppl/_explain",
                                                   body={"query": final})
    except Exception as e:  # noqa: BLE001 -- PPL syntax and field errors come back here
        raise PplRefused(_reason(e)) from None
    check_plan(_requests(explain))
    try:
        r = client.transport.perform_request("POST", "/_plugins/_ppl", body={"query": final})
    except Exception as e:  # noqa: BLE001
        raise PplRefused(_reason(e)) from None
    rows = r.get("datarows", [])
    out = {"columns": [c["name"] for c in r.get("schema", [])], "rows": rows,
           "returned": len(rows), "truncated": len(rows) >= MAX_ROWS, "ran": final}
    if out["truncated"]:
        out["note"] = f"capped at {MAX_ROWS} rows; aggregate with stats instead of listing."
    return out


def _reason(e: Exception) -> str:
    """The useful sentence out of an OpenSearch PPL error."""
    info = getattr(e, "info", None)
    if isinstance(info, dict):
        err = info.get("error", {})
        reason = err.get("reason") or err.get("details") or str(err)
        hint = err.get("suggestion")
        return f"the query failed: {str(reason)[:200]}" + (f" ({hint})" if hint else "")
    return f"the query failed: {type(e).__name__}"


if __name__ == "__main__":
    from common_mcp import os_client

    query, *window = sys.argv[1:] or ["source = logs-auth-* | stats count()", "now-2d"]
    try:
        print(json.dumps(run(os_client("l1_analyst"), query, "l1_analyst", *window))[:600])
    except PplRefused as e:
        print("refused:", e)
