"""
Unit 4: the SIEM server -- incidents, aggregations and bounded log search over OpenSearch.

Five tools, in the order an analyst uses them:

    list_incidents, get_incident    what's open, and the window to start looking in
    event_histogram                 how many events, when: the shape of the activity
    top_values                      which hosts, users, addresses: who's behind a spike
    search_events                   the events themselves, a page at a time

Three rules run through all of them:

  1. **Every log query has a bounded window** (windows.py). Aggregations may cover all of
     retention; a raw search may cover 24 hours. Only the daily indexes the window touches
     are searched.
  2. **Aggregate first, then drill down.** A histogram or a top-10 costs a few hundred
     tokens whatever the volume; raw events cost tokens per event. search_events says so
     when a search matches far more than a page.
  3. **Results are shaped.** Only catalogued fields the caller may see, long text cut short,
     a page at most, the total always reported, and a cursor bound to the query that made it.

Fields come from Unit 3's catalog: a filter or field the persona's catalog doesn't offer is
refused with the list of ones it does. The server acts as one persona (`MCP_PERSONA`, default
l1_analyst), as in Unit 3; Unit 7 makes it the real caller.

    MCP_PERSONA=l3_responder python examples/04/investigate.py     # drives this server
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "03"), str(EXAMPLES)]
import catalog  # noqa: E402  (Unit 3)
import windows  # noqa: E402
from common_mcp import joinable_log, log_note, os_client, persona  # noqa: E402

# Who each call acts for: MCP_PERSONA here; the authenticated caller in Unit 7.

Dataset = Literal["auth", "web", "syslog", "edr"]
PREFIX = {"auth": "logs-auth", "web": "logs-web", "syslog": "logs-syslog", "edr": "edr-events"}

MAX_SEARCH_SPAN = timedelta(hours=24)       # raw events: one day at most
MAX_AGG_SPAN = windows.RETENTION            # counts: all of retention
MAX_PAGE = 50                               # events or incidents per call
MAX_TEXT = 300                              # characters of any one text value
MAX_BUCKETS = 200
AUTO_BUCKETS = 60                           # what interval="auto" aims under
INTERVALS = ["1m", "5m", "10m", "15m", "30m", "1h", "3h", "6h", "12h", "1d"]
PIT_KEEP_ALIVE = "2m"                       # how long a cursor stays usable between pages
SUMMARISE_ABOVE = 500                       # suggest aggregating when a search matches more

DEFAULT_FIELDS = {
    "auth": ["@timestamp", "host.name", "user.name", "source.ip", "event.outcome", "message"],
    "web": ["@timestamp", "host.name", "http.request.method", "url.path",
            "http.response.status_code", "source.ip"],
    "syslog": ["@timestamp", "host.name", "process.name", "log.level", "message"],
    "edr": ["@timestamp", "host.name", "user.name", "event.action", "process.name",
            "process.command_line", "file.path", "destination.ip", "rule.name"],
}

_CURSOR_KEY = secrets.token_bytes(32)       # per process: a restart invalidates cursors

def mine() -> list[str]:
    """The log datasets the current caller may search."""
    return [d for d in PREFIX if catalog.describe(PREFIX[d] + "-*", persona())]


MINE = mine()                               # for the instructions: the starting persona's

mcp = MCPServer(
    "siem",
    version="0.1.0",
    instructions=(
        "SOC incidents and logs (OpenSearch). Times are UTC unless you pass tz. Start from an "
        "incident's suggested_window, or a window you choose. Count before you read: "
        "event_histogram for when, top_values for who, then search_events for the events. "
        f"Log datasets you may search: {', '.join(MINE) or 'none'}."
    ),
    cache_hints={"tools/list": CacheHint(ttl_ms=300_000, scope="private")},
)
mcp.middleware.append(joinable_log("siem"))


# =========================================================================================
# Fields, filters and shaping: everything the caller names must be in its catalog
# =========================================================================================

def _catalog(dataset: str) -> dict:
    if dataset not in PREFIX:
        raise ToolError(f"unknown dataset {dataset!r}; use one of {sorted(PREFIX)}.")
    described = catalog.describe(PREFIX[dataset] + "-*", persona())
    if described is None:
        raise ToolError(f"{dataset} logs aren't available to your role. You can search: "
                        f"{', '.join(mine()) or 'none'}.")
    return described["fields"]


def _field(fields: dict, name: str, dataset: str, purpose: str) -> dict:
    base = name.removesuffix(".keyword")
    spec = fields.get(base)
    if spec is None or (name != base and spec["type"] != "text"):
        usable = sorted(f for f, s in fields.items()
                        if purpose == "return" or s["use"] in ("filter", "join", "id"))
        raise ToolError(f"can't {purpose} {name!r}: it isn't a {dataset} field you can use. "
                        f"Fields you can: {', '.join(usable)}.")
    return spec


def _filter_clauses(fields: dict, filters: dict | None, dataset: str) -> list[dict]:
    clauses = []
    for name, value in (filters or {}).items():
        spec = _field(fields, name, dataset, "filter on")
        if spec["sensitivity"] == catalog.PII:
            raise ToolError(f"{name} is masked for you, so a filter on it would silently match "
                            "nothing. Filter on user.name, or use top_values on "
                            f"{name} to correlate.")
        if spec["type"] == "text" and not name.endswith(".keyword"):
            raise ToolError(f"{name} is full text: put words in `text` to search the "
                            f"message, or filter on {name}.keyword for an exact value.")
        if isinstance(value, dict):
            bounds = {k: v for k, v in value.items() if k in ("gt", "gte", "lt", "lte")}
            if not bounds or len(bounds) != len(value):
                raise ToolError(f"a range on {name} takes gt, gte, lt and/or lte, "
                                'e.g. {"gte": 500, "lte": 599}.')
            clauses.append({"range": {name: bounds}})
        elif isinstance(value, list):
            if not value or len(value) > 50:
                raise ToolError(f"a list filter on {name} takes 1 to 50 values.")
            clauses.append({"terms": {name: value}})
        else:
            clauses.append({"term": {name: value}})
    return clauses


def _query(w: windows.Window, fields: dict, dataset: str, filters, text) -> dict:
    must = [w.range_query(), *_filter_clauses(fields, filters, dataset)]
    if text:
        must.append({"match": {"message": {"query": str(text)[:200], "operator": "and"}}})
    return {"bool": {"filter": must}}


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + f"...[+{len(value) - MAX_TEXT} chars]"
    return value


def _pluck(source: dict, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(source, dict) or part not in source:
            return None
        source = source[part]
    return source


def _shape(hit: dict, wanted: list[str], w: windows.Window) -> dict:
    out = {}
    for name in wanted:
        value = _pluck(hit["_source"], name)
        if value is None:
            continue
        if name == "@timestamp":
            value = w.iso(windows.parse_time(value))
        out[name] = _short(value)
    return out


def _search(index: str, body: dict, **params) -> dict:
    """Run a search, noting what it cost the backend in the joinable log."""
    try:
        r = os_client(persona()).search(index=index, body=body, ignore_unavailable=True,
                                      allow_no_indices=True, **params)
    except Exception as e:  # noqa: BLE001 -- the backend refused or failed: say what to do
        raise ToolError(f"the search failed ({type(e).__name__}). Check the filter values "
                        "and types, or narrow the window.") from None
    log_note(indexes=index.count(",") + 1 if index else 0, shards=r["_shards"]["total"],
             took_ms=r["took"])
    return r


def _window(start: str, end: str, tz: str, max_span: timedelta, tool: str) -> windows.Window:
    try:
        return windows.window(start, end, tz=tz, max_span=max_span, tool=tool)
    except windows.WindowError as e:
        raise ToolError(str(e)) from None


def _indexes(dataset: str, w: windows.Window) -> str:
    return ",".join(windows.daily_indexes(PREFIX[dataset], w))


# =========================================================================================
# Incidents
# =========================================================================================

def _incident(source: dict, tz: str) -> dict:
    inc = source["incident"]
    out = {k: inc.get(k) for k in ("id", "title", "status", "severity", "assignee", "rule",
                                   "alert_count")}
    out["created"] = windows.iso(windows.parse_time(inc["created"]), tz)
    for name in ("host.name", "source.ip", "user.name"):
        if (value := _pluck(source, name)) is not None:
            out[name] = value
    return out


@mcp.tool()
def list_incidents(status: list[Literal["open", "in_progress", "closed", "false_positive"]]
                   | None = None,
                   severity: Literal["low", "medium", "high"] | None = None,
                   host: str | None = None, since: str = "now-21d", until: str = "now",
                   limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """SOC incidents, newest first. Active ones: status ["open", "in_progress"].
    `since`/`until` bound when they were created (ISO 8601 or now-7d). `total` is how many
    match; page with `offset`."""
    if not 1 <= limit <= MAX_PAGE or not 0 <= offset <= 1000:
        raise ToolError(f"limit is 1 to {MAX_PAGE}, offset 0 to 1000.")
    try:
        w = windows.window(since, until, max_span=timedelta(days=366), retention=None)
    except windows.WindowError as e:
        raise ToolError(str(e)) from None
    must = [w.range_query("incident.created")]
    if status:
        must.append({"terms": {"incident.status": status}})
    if severity:
        must.append({"term": {"incident.severity": severity}})
    if host:
        must.append({"term": {"host.name": host}})
    r = _search("incidents", {"query": {"bool": {"filter": must}}, "size": limit,
                              "from": offset, "track_total_hits": True,
                              "sort": [{"incident.created": "desc"}]})
    rows = [_incident(h["_source"], "UTC") for h in r["hits"]["hits"]]
    for row in rows:                                   # the list stays short; details on demand
        row.pop("rule", None)
        row.pop("alert_count", None)
    log_note(hits=r["hits"]["total"]["value"], returned=len(rows))
    return {"total": r["hits"]["total"]["value"], "offset": offset, "returned": len(rows),
            "incidents": rows}


@mcp.tool()
def get_incident(incident_id: str) -> dict[str, Any]:
    """One incident in full, with a suggested_window to start the investigation in (from an
    hour before it was raised to an hour after)."""
    r = _search("incidents", {"query": {"term": {"incident.id": incident_id.strip().upper()}},
                              "size": 1})
    if not r["hits"]["hits"]:
        raise ToolError(f"no incident {incident_id!r}. Ids look like INC-0042; "
                        "list_incidents shows them.")
    source = r["hits"]["hits"][0]["_source"]
    out = _incident(source, "UTC")
    out["updated"] = source["incident"].get("updated")
    out["summary"] = _short(source["incident"].get("summary"))
    out["tags"] = source.get("tags", [])
    created = datetime.fromisoformat(source["incident"]["created"].replace("Z", "+00:00"))
    out["suggested_window"] = {"start": windows.iso(created - timedelta(hours=1)),
                               "end": windows.iso(created + timedelta(hours=1))}
    return out


# =========================================================================================
# Aggregations: cheap whatever the volume
# =========================================================================================

def _interval_minutes(interval: str) -> int:
    return int(interval[:-1]) * {"m": 1, "h": 60, "d": 1440}[interval[-1]]


@mcp.tool()
def event_histogram(dataset: Dataset, start: str, end: str = "now",
                    filters: dict[str, Any] | None = None, text: str | None = None,
                    interval: str = "auto", tz: str = "UTC") -> dict[str, Any]:
    """How many events per time bucket: use it first, to find when something happened.
    Window: start/end as ISO 8601 (2026-10-09T02:00:00Z) or relative (now-24h); up to 21 days.
    filters: exact values on catalogued fields, e.g. {"event.outcome": "failure"}, a list for
    any-of, or a range {"gte": 500}. text: words to find in the message. interval: auto, or
    one of 1m 5m 10m 15m 30m 1h 3h 6h 12h 1d."""
    fields = _catalog(dataset)
    w = _window(start, end, tz, MAX_AGG_SPAN, "event_histogram")
    span = int(w.span.total_seconds() // 60)
    if interval == "auto":
        interval = next((i for i in INTERVALS if span / _interval_minutes(i) <= AUTO_BUCKETS),
                        INTERVALS[-1])
    elif interval not in INTERVALS:
        raise ToolError(f"interval must be auto or one of {' '.join(INTERVALS)}.")
    if span / _interval_minutes(interval) > MAX_BUCKETS:
        raise ToolError(f"{interval} buckets over {windows.span_text(w.span)} would be "
                        f"{span // _interval_minutes(interval)} buckets (max {MAX_BUCKETS}). "
                        "Use a wider interval, or interval=auto.")
    r = _search(_indexes(dataset, w), {
        "size": 0, "track_total_hits": True,
        "query": _query(w, fields, dataset, filters, text),
        "aggs": {"t": {"date_histogram": {
            "field": "@timestamp", "fixed_interval": interval, "time_zone": tz,
            "min_doc_count": 0,
            "extended_bounds": {"min": int(w.start.timestamp() * 1000),
                                "max": int(w.end.timestamp() * 1000) - 1}}}}})
    found = r.get("aggregations", {}).get("t", {}).get("buckets", [])   # none: no index yet
    buckets = [{"start": w.iso(datetime.fromtimestamp(b["key"] / 1000, UTC)),
                "count": b["doc_count"]} for b in found]
    total = r["hits"]["total"]["value"]
    log_note(hits=total, buckets=len(buckets))
    out = {"dataset": dataset, "window": w.describe(), "interval": interval, "total": total,
           "buckets": buckets}
    if total:
        out["peak"] = max(buckets, key=lambda b: b["count"])
    return out


@mcp.tool()
def top_values(dataset: Dataset, field: str, start: str, end: str = "now",
               filters: dict[str, Any] | None = None, text: str | None = None,
               size: int = 10, tz: str = "UTC") -> dict[str, Any]:
    """The most common values of one field, with counts: use it to find who or what is behind
    a spike (source.ip, user.name, host.name, ...). Same window, filters and text as
    event_histogram. `other_count` is how many matching events had some other value."""
    fields = _catalog(dataset)
    spec = _field(fields, field, dataset, "count values of")
    if spec["use"] not in ("filter", "join", "id") and not field.endswith(".keyword"):
        raise ToolError(f"{field} is {spec['use']} text; count {field}.keyword, or pick a "
                        "keyword field.")
    if not 1 <= size <= 50:
        raise ToolError("size is 1 to 50.")
    w = _window(start, end, tz, MAX_AGG_SPAN, "top_values")
    r = _search(_indexes(dataset, w), {
        "size": 0, "track_total_hits": True,
        "query": _query(w, fields, dataset, filters, text),
        "aggs": {"top": {"terms": {"field": field, "size": size}},
                 "distinct": {"cardinality": {"field": field}}}})
    agg = r.get("aggregations") or {"top": {"buckets": [], "sum_other_doc_count": 0},
                                    "distinct": {"value": 0}}       # none: no index yet
    values = [{"value": b["key"], "count": b["doc_count"]} for b in agg["top"]["buckets"]]
    total = r["hits"]["total"]["value"]
    log_note(hits=total, returned=len(values))
    return {"dataset": dataset, "field": field, "window": w.describe(), "total": total,
            "distinct_approx": agg["distinct"]["value"], "values": values,
            "other_count": agg["top"]["sum_other_doc_count"]}


# =========================================================================================
# Search: the events themselves, a page at a time
# =========================================================================================

def _digest(args: dict) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _seal(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    tag = hmac.new(_CURSOR_KEY, body.encode(), "sha256").hexdigest()[:32]
    return f"{body}.{tag}"


def _open(cursor: str) -> dict:
    body, _, tag = cursor.partition(".")
    good = hmac.new(_CURSOR_KEY, body.encode(), "sha256").hexdigest()[:32]
    if not hmac.compare_digest(tag, good):
        raise ToolError("that cursor isn't one this server issued (or the server restarted). "
                        "Repeat the search without a cursor.")
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


@mcp.tool()
def search_events(dataset: Dataset, start: str, end: str = "now",
                  filters: dict[str, Any] | None = None, text: str | None = None,
                  fields: list[str] | None = None, limit: int = 20,
                  newest_first: bool = False, tz: str = "UTC",
                  cursor: str | None = None) -> dict[str, Any]:
    """The matching events themselves, oldest first, a page at a time. The window can be at
    most 24h: find it with event_histogram first. Same filters and text as event_histogram.
    fields: which fields to return (default: a short set per dataset). `total` is every match;
    if next_cursor is set, call again with the SAME arguments plus cursor for the next page."""
    args = {"dataset": dataset, "start": start, "end": end, "filters": filters, "text": text,
            "fields": fields, "newest_first": newest_first, "tz": tz}
    catalog_fields = _catalog(dataset)
    wanted = fields or DEFAULT_FIELDS[dataset]
    for name in wanted:
        _field(catalog_fields, name, dataset, "return")
    if not 1 <= limit <= MAX_PAGE:
        raise ToolError(f"limit is 1 to {MAX_PAGE}; for more, follow next_cursor.")
    client = os_client(persona())

    if cursor:
        state = _open(cursor)
        if state["q"] != _digest(args):
            raise ToolError("this cursor belongs to a different search. Send the same "
                            "dataset, window, filters, text, fields and order as the call "
                            "that returned it, or drop the cursor to start a new search.")
        w = windows.Window(datetime.fromisoformat(state["s"]),
                           datetime.fromisoformat(state["e"]), tz)   # frozen at page 1
        total, exact, page, seen = state["t"], state["x"], state["p"] + 1, state["n"]
        pit, after = state["pit"], state["after"]
    else:
        w = _window(start, end, tz, MAX_SEARCH_SPAN, "search_events")
        page, seen, after = 1, 0, None
        # A point in time pins the indexes as they are now, so later pages neither skip nor
        # repeat events while new logs arrive. It holds resources on the cluster until it
        # expires or the last page deletes it.
        try:
            pit = client.create_pit(index=_indexes(dataset, w), keep_alive=PIT_KEEP_ALIVE,
                                    params={"ignore_unavailable": "true",
                                            "allow_partial_pit_creation": "true"})["pit_id"]
        except Exception as e:  # noqa: BLE001 -- also raised when no index in the window exists
            if _search(_indexes(dataset, w), {"size": 0})["_shards"]["total"]:
                raise ToolError(f"the search failed ({type(e).__name__}); try again.") from None
            pit = None
        if not pit:
            log_note(indexes=0, shards=0, hits=0, returned=0, page=1)
            return {"dataset": dataset, "window": w.describe(), "total": 0,
                    "total_exact": True, "page": 1, "returned": 0, "events": [],
                    "note": "no log index covers this window, so nothing matches."}
    order = "desc" if newest_first else "asc"
    body = {"size": limit, "query": _query(w, catalog_fields, dataset, filters, text),
            "_source": {"includes": wanted},
            "pit": {"id": pit, "keep_alive": PIT_KEEP_ALIVE},
            "sort": [{"@timestamp": order}, {"_shard_doc": order}],
            "track_total_hits": page == 1}
    if after:
        body["search_after"] = after
    try:
        r = client.search(body=body)
    except Exception as e:  # noqa: BLE001
        if not cursor:                                   # don't leave page 1's PIT open
            client.delete_pit(body={"pit_id": [pit]}, ignore=[404])
        if cursor and ("pit" in str(e).lower() or "search context" in str(e).lower()):
            raise ToolError(f"the cursor expired (cursors last {PIT_KEEP_ALIVE} between "
                            "pages). Repeat the search without a cursor.") from None
        raise ToolError(f"the search failed ({type(e).__name__}). Check the filter values "
                        "and types, or narrow the window.") from None
    hits = r["hits"]["hits"]
    if page == 1:
        total = r["hits"]["total"]["value"]
        exact = r["hits"]["total"]["relation"] == "eq"
    seen += len(hits)
    log_note(indexes=len(windows.daily_indexes(PREFIX[dataset], w)),
             shards=r["_shards"]["total"], took_ms=r["took"], hits=total, returned=len(hits),
             page=page)

    out = {"dataset": dataset, "window": w.describe(), "total": total, "total_exact": exact,
           "page": page, "returned": len(hits), "events": [_shape(h, wanted, w) for h in hits]}
    if hits and seen < total:
        out["next_cursor"] = _seal({"q": _digest(args), "s": w.start.isoformat(),
                                    "e": w.end.isoformat(), "t": total, "x": exact, "p": page,
                                    "n": seen, "pit": r.get("pit_id", pit),
                                    "after": hits[-1]["sort"]})
    else:
        client.delete_pit(body={"pit_id": [pit]})          # last page: free the backend
    if page == 1 and total > SUMMARISE_ABOVE:
        out["hint"] = (f"{total} events match; this page shows {len(hits)}. To see the whole "
                       "picture, use event_histogram (when) or top_values (who) with the same "
                       "filters, then narrow the search.")
    return out


if __name__ == "__main__":
    mcp.run()
