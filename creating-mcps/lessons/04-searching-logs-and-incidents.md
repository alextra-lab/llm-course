---
title: "Searching Logs and Incidents"
linkTitle: "4. Searching Logs and Incidents"
weight: 4
---

**Goal:** build the SIEM server's real tools: incidents, aggregations and log search over
OpenSearch. Three habits run through all of them:
- **every query has a bounded time window**, and the server picks only the indexes that
  window touches;
- **aggregate first, then drill down**: count *when*, then *who*, and only then read events;
- **shape what comes back**: catalogued fields only, long text cut short, a page at a time,
  the total always reported, and a cursor that belongs to the query that made it.

Then you'll investigate the planted intrusion yourself, and let `gpt-oss-120b` do it with the
same tools, and compare the two paths.

**Where this fits:** Unit 3 told a model *what* there is to query. This unit decides *how* it
queries the log store, with tools shaped around the job. It's the restrictive end of the
permission dial that Unit 5 measures against raw Query DSL. It uses Unit 3's catalog for field
names and permissions, and the Unit 2 host to run the model.

---

## Why log search needs its own tools

A thin "search" tool that passes a query through to OpenSearch works in a demo and fails in
three ways on a real cluster. [`examples/04/cost.py`](../examples/04/cost.py) shows all three
on one question, *"what happened with failed logins around INC-0042?"*, asked as the L1
analyst:

```bash
python examples/04/cost.py
```

```text
how                                 matched  returned       bytes   ~tokens  shards
raw documents, no window              5,195     5,195   2,742,879   685,720      21
raw documents, the incident's day     1,698     1,698     906,915   226,729      21
search_events, one page               1,698        20       5,361     1,340       1
event_histogram (30m buckets)         1,698        48       2,476       619       1
top_values source.ip                  1,698        10         586       146       1
```

- **No window means everything.** Without a time range the query reads all 21 daily indexes,
  and the result would take 686k tokens. That's five times `gpt-oss-120b`'s context.
- **A window isn't enough on its own.** One day of failed logins is still 227k tokens of raw
  documents. And through `logs-auth-*` it still opens all 21 shards to find the one that holds
  that day.
- **The question was about a pattern, not about documents.** A histogram answers *when* in 619
  tokens. A top-10 answers *who* in 146. Neither gets bigger when the data does.

The three Unit 4 tools in the table make those defaults. The rest of this unit builds them.

Refresh `common_mcp.py` in `work/` first (`cp examples/common_mcp.py work/`). This unit adds
`log_note`, which lets a tool add its own fields to the joinable log record. You'll also need
`work/catalog.py` from Unit 3.

## Time windows done properly

Every log tool takes `start` and `end`. That makes four decisions the server has to get right
before it sends anything to OpenSearch.

1. **What did the caller mean?** Models write times many ways: `now-24h`,
   `2026-10-09T02:00:00Z`, `2026-10-09T04:00+02:00`, `2026-10-09`, and sometimes `yesterday`.
   Accept the forms that are unambiguous. Refuse the rest with an example of what works.
2. **Which time zone?** A time with no offset is ambiguous. Read it in the caller's `tz`
   (default UTC), and echo the window back in every answer so the model can see how its words
   were read. The spike that is "4 a.m." for an analyst in Paris is 02:00 UTC in the logs.
3. **Is it allowed?** Refuse windows that are backwards, start in the future, start before
   retention, or are wider than the tool allows. The limit depends on the tool: an
   aggregation over 21 days costs about the same as one over an hour, but a raw search
   doesn't.
4. **Which indexes?** The logs are daily indexes in UTC (`logs-auth-2026.10.09`). Name the
   days the window touches instead of searching the pattern.

Create **`work/windows.py`**. Start with parsing:

```python
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RETENTION = timedelta(days=21)
_RELATIVE = re.compile(r"^now(?:\s*-\s*(\d+)\s*([mhd]))?$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


class WindowError(ValueError):
    """A window the caller must change. The message says how."""


def zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise WindowError(f"unknown time zone {tz!r}; use an IANA name such as UTC, "
                          "Europe/Paris or America/New_York.") from None


def parse_time(text: str, tz: str = "UTC", now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    value = str(text).strip()
    m = _RELATIVE.match(value.lower())
    if m:
        amount, unit = m.groups()
        return now - timedelta(**{_UNITS[unit]: int(amount)}) if amount else now
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise WindowError(f"can't read the time {value!r}. Use ISO 8601 "
                          "(2026-10-09T02:00:00Z, or with an offset like +02:00), a date "
                          "(2026-10-09), or a relative time like now-24h.") from None
    if dt.tzinfo is None:                       # no offset: read it in the caller's zone
        dt = dt.replace(tzinfo=zone(tz))
    return dt.astimezone(UTC)
```

Then the checks. Each refusal is written as an instruction, because a model reads the error
and tries again:

```python
def window(start: str, end: str = "now", *, tz: str = "UTC", max_span: timedelta,
           now: datetime | None = None, tool: str = "this tool") -> "Window":
    now = now or datetime.now(UTC)
    zone(tz)
    s, e = parse_time(start, tz, now), parse_time(end, tz, now)
    if e <= s:
        raise WindowError(f"the window ends ({e:%Y-%m-%dT%H:%M}Z) before it starts "
                          f"({s:%Y-%m-%dT%H:%M}Z); swap start and end.")
    if s > now:
        raise WindowError("the window starts in the future; logs only go up to now.")
    e = min(e, now)
    oldest = (now - RETENTION).replace(hour=0, minute=0, second=0, microsecond=0)
    if s < oldest:
        raise WindowError(f"the window starts before retention ({RETENTION.days} days, "
                          f"oldest {oldest:%Y-%m-%dT%H:%M}Z). Start at now-21d or later.")
    if e - s > max_span:
        raise WindowError(f"the window is {e - s}; {tool} allows at most {max_span}. "
                          "Narrow it, for example to the spike an event_histogram shows, "
                          "or to the incident's suggested_window.")
    return Window(s, e, tz)
```

The `Window` itself is small. It knows its span, how to show a time in the caller's zone,
the `range` clause for OpenSearch, and which daily indexes it covers:

```python
@dataclass(frozen=True)
class Window:
    start: datetime          # inclusive, UTC
    end: datetime            # exclusive, UTC
    tz: str

    def range_query(self, field: str = "@timestamp") -> dict:
        return {"range": {field: {"gte": self.start.isoformat(),
                                  "lt": self.end.isoformat()}}}


def daily_indexes(prefix: str, w: Window) -> list[str]:
    day = w.start.replace(hour=0, minute=0, second=0, microsecond=0)
    names = []
    while day < w.end:
        names.append(f"{prefix}-{day:%Y.%m.%d}")
        day += timedelta(days=1)
    return names
```

The reference, [`examples/04/windows.py`](../examples/04/windows.py), adds `describe()` (the
echo) and `iso()` (a time in the caller's zone, with its offset), and has a small CLI:

```bash
python examples/04/windows.py 2026-10-09T04:00 2026-10-09T05:00 Europe/Paris
python examples/04/windows.py now-30d
```

```text
{'start': '2026-10-09T04:00:00+02:00', 'end': '2026-10-09T05:00:00+02:00', 'span': '1h',
 'tz': 'Europe/Paris', 'start_utc': '2026-10-09T02:00:00Z', 'end_utc': '2026-10-09T03:00:00Z'}
indexes: logs-auth-2026.10.09
refused: the window starts before retention (21 days, oldest 2026-09-20T00:00Z). Start at now-21d or later.
```

*(Dates in this unit's output come from a seed with anchor 2026-10-11, so the incident is on
2026-10-09. Yours follow your own anchor; the times of day are the same.)*

**Why name the days?** Searching `logs-auth-*` with a one-day range gives the same answer as
searching `logs-auth-2026.10.09`. The difference is how much of the cluster OpenSearch
touches to get it:

| Index expression | Shards searched | Took (default data) |
|---|---|---|
| `logs-auth-*` | 21 (none skipped) | ~10 ms |
| `logs-auth-2026.10.09` | 1 | ~2 ms |

On a laptop the milliseconds hardly matter. Shards are what matter once fifty agents query
at the same time, and that's Unit 8's subject. OpenSearch can skip shards whose dates can't
match (the `can_match` pre-filter), but only above a shard-count threshold (128 by default).
Below it, it opens every one. Name the days, and pass `ignore_unavailable` so a day with no
index yet isn't an error.

## The SIEM server

[`examples/04/siem_server.py`](../examples/04/siem_server.py) has five tools, in the order an
analyst uses them:

| Tool | Answers | Window | Returns |
|---|---|---|---|
| `list_incidents` | what's active? | on `incident.created`, optional | up to 50, newest first, with `total` |
| `get_incident` | tell me about this one | none | the incident, plus a `suggested_window` |
| `event_histogram` | *when*? | required, up to 21 days | counts per bucket, the `peak`, the `total` |
| `top_values` | *who* or *what*? | required, up to 21 days | top N values with counts, `other_count`, `distinct_approx` |
| `search_events` | show me | required, **up to 24 h** | up to 50 shaped events, `total`, `next_cursor` |

Create **`work/siem_server.py`**. Copy the reference (`cp examples/04/siem_server.py work/`)
and read it alongside the sections below, which walk through the parts that matter.

**The server's instructions say how to work.** They're the first thing a host's model reads
about the server, so they carry the method as well as the inventory:

```python
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
```

**The incident hands over a window.** `get_incident` adds a `suggested_window`, from an hour
before the incident was raised to an hour after. Every later call can start there instead of
guessing:

```text
{"id": "INC-0042", "title": "Brute force followed by successful login on jump-prod-02",
 "status": "open", "severity": "high", "created": "2026-10-09T02:40:00Z",
 "host.name": "jump-prod-02", "source.ip": "203.0.113.77", "user.name": "svc_backup", ...,
 "suggested_window": {"start": "2026-10-09T01:40:00Z", "end": "2026-10-09T03:40:00Z"}}
```

`list_incidents` pages with `offset`. That's fine for a few hundred documents in one index,
sorted by a field that doesn't change. Logs are the opposite case, and they get a cursor (see
below).

### Fields come from the catalog

Every field a caller names, whether as a filter, a field to return, or a field to count, is
checked against **Unit 3's catalog for that persona** before any query is built. Three rules
come out of that:

- **Unknown or forbidden fields are refused, with the list of usable ones.** As the L1 analyst,
  `user.email` isn't in the catalog, so a filter on it is refused. That's better than letting
  field-level security return zero hits that look like an answer.
- **Masked fields can be counted but not filtered.** The L3 responder may `top_values` on
  `user.email` and gets stable hashes. A filter on it would silently match nothing, so the
  server refuses it and says what to do instead.
- **Full-text fields go through `text`.** A filter on `message` is refused with "put words in
  `text`, or filter on message.keyword".

```python
def _filter_clauses(fields: dict, filters: dict | None, dataset: str) -> list[dict]:
    clauses = []
    for name, value in (filters or {}).items():
        spec = _field(fields, name, dataset, "filter on")       # refused if not catalogued
        if spec["sensitivity"] == catalog.PII:
            raise ToolError(f"{name} is masked for you, so a filter on it would silently "
                            f"match nothing. Filter on user.name, or use top_values on "
                            f"{name} to correlate.")
        if isinstance(value, dict):                              # {"gte": 500, "lte": 599}
            clauses.append({"range": {name: value}})
        elif isinstance(value, list):                            # any of
            clauses.append({"terms": {name: value}})
        else:
            clauses.append({"term": {name: value}})
    return clauses
```

Filters are a small JSON object: `{"event.outcome": "failure"}`, a list for any-of, a range
as `{"gte": 500}`. That's all a model gets. It can't send Query DSL, scripts or wildcards,
because none of these tools accept them. Unit 5 measures what that restriction costs and what
it prevents.

### Aggregate first

`event_histogram` is a `date_histogram` over the window: fixed buckets, `min_doc_count: 0`
and `extended_bounds`, so quiet periods show as zeros and a spike has a visible shape. With
`interval: "auto"` it picks the smallest interval that gives at most 60 buckets. It refuses
more than 200, so the size of the answer stays bounded.

```text
event_histogram auth, suggested window, {"event.outcome": "failure"}  ->  1,350 bytes
{"interval": "5m", "total": 1511, "buckets": [
   {"start": "2026-10-09T01:40:00Z", "count": 1}, ... ,
   {"start": "2026-10-09T02:05:00Z", "count": 301}, {"start": "2026-10-09T02:10:00Z", "count": 300},
   ... five buckets of ~300 ..., {"start": "2026-10-09T02:30:00Z", "count": 0}, ...],
 "peak": {"start": "2026-10-09T02:05:00Z", "count": 301}}
```

`top_values` is a `terms` aggregation plus a `cardinality`. It returns the top N values,
how many events had some other value (`other_count`), and roughly how many distinct values
there were. Over the same window and filter, one address made 1,500 of the 1,511 failures.

### Then drill down: shaping and paging the events

`search_events` is the only tool that returns documents, so it carries the tightest limits:

- **24 hours at most.** A wider window is refused with a pointer to `event_histogram`.
- **Catalogued fields only, and few of them.** Each dataset has a short default set (for
  `auth`: time, host, user, source address, outcome, message). The caller can name others it
  may see. The server asks OpenSearch for exactly those (`_source.includes`), so the rest never
  leave the cluster.
- **Long text is cut** at 300 characters, with a note of how much was dropped.
- **At most 50 events a page**, and **`total` is always reported**. By default OpenSearch
  stops counting at 10,000 and reports `gte`. The server sets `track_total_hits` on the first
  page so the model knows whether it's looking at all of it. When the total is well above a
  page, it adds a `hint` to aggregate instead.

Paging is where most search tools cut corners. `from`/`size` gets slower the deeper it goes,
it's capped at 10,000, and it can skip or repeat documents while new logs arrive. The server
uses a **point in time (PIT)** with **`search_after`** instead. The PIT pins the indexes as
they were on page 1, and `search_after` resumes from the last sort key. The cursor the model
gets back is that state, sealed:

```python
def _seal(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    tag = hmac.new(_CURSOR_KEY, body.encode(), "sha256").hexdigest()[:32]
    return f"{body}.{tag}"
```

The payload holds the PIT id, the last sort values, the window as resolved on page 1, the
total, and a **digest of the query arguments**. Each of those does a job:

- **The HMAC** means a cursor the model edits, or invents, is refused. It's the same idea as
  the SDK's sealed `request_state` from Unit 2.
- **The digest** means a cursor only works with the arguments that made it. Change the
  filters and send the old cursor, and the server says "this cursor belongs to a different
  search". Without it, page 2 would silently be page 2 of a different question.
- **The resolved window** means `now-24h` doesn't move between pages. The window is fixed
  when page 1 runs.
- **The total** means later pages skip counting.

The server keeps no state between calls: the cursor carries it, as the 2026-07-28 protocol
expects. The last page deletes the PIT, and an abandoned one expires after two minutes.

```text
search_events auth, suggested window, {"source.ip": "203.0.113.77"}, limit 3
{"total": 1501, "total_exact": true, "page": 1, "returned": 3, "events": [
   {"@timestamp": "2026-10-09T02:05:00Z", "host.name": "jump-prod-02", "user.name": "support",
    "source.ip": "203.0.113.77", "event.outcome": "failure",
    "message": "Failed password for support from 203.0.113.77 port 58417 ssh2"}, ...],
 "next_cursor": "eyJxIjogImE4MTlm...", "hint": "1501 events match; this page shows 3. ..."}
```

### What the log records

Each tool calls `log_note(indexes=..., shards=..., took_ms=..., hits=..., returned=...)`.
The joinable-log middleware merges those fields into its record for that request. So the
line that already says *who asked what* (session, trace, step, tool, argument keys, outcome)
now also says *what it cost the cluster*:

```text
{"session_id": "...", "trace_id": "...", "step": 5, "server": "siem", "name": "search_events",
 "arg_keys": ["dataset", "end", "filters", "start"], "outcome": "ok", "ms": 55.5,
 "indexes": 1, "shards": 1, "took_ms": 15, "hits": 1, "returned": 1, "page": 1}
```

`log_note` is a few lines in `common_mcp.py`: a `ContextVar` holding a dict that the
middleware creates per request and reads back when the call finishes. The model never sees
these fields.

## Investigate the planted incident

Now use the tools the way the lesson argues. [`examples/04/investigate.py`](../examples/04/investigate.py)
runs the path through the Unit 2 host, step by step:

```bash
python examples/04/investigate.py
```

```text
By hand, as l1_analyst:
  1. list_incidents      923 B  4 active high-severity: INC-0042, INC-0002, INC-0008, INC-0027
  2. get_incident        608 B  Brute force followed by successful login on jump-prod-02; window 2026-10-09T01:40:00Z to 2026-10-09T03:40:00Z
  3. event_histogram   1,350 B  1511 failures; peak 301 at 2026-10-09T02:05:00Z (5m buckets)
  4. top_values          594 B  203.0.113.77 (1500), 10.30.38.123 (1), 10.30.47.46 (1)
  5. search_events       414 B  2026-10-09T02:31:12Z svc_backup on jump-prod-02
  6. search_events       648 B  2026-10-09T02:31:12Z jump-prod-02 from 203.0.113.77; 2026-10-09T02:46:15Z db-prod-01 from 10.20.167.107
  7. search_events       117 B  error: ... edr logs aren't available to your role. You can search: auth, web, syslog.
  4,654 bytes in all (~1,164 tokens)
```

Read it as a triage:

1. **The histogram finds the spike.** About 300 failed logins every five minutes from 02:05
   to 02:30 UTC, against a background of one or two.
2. **Top values finds the source.** One address, `203.0.113.77`, made 1,500 of them.
3. **A narrow search finds the break-in.** That address's only *successful* login was as
   `svc_backup` at 02:31:12, a minute after the spraying stopped.
4. **Following the account finds the next step.** Fifteen minutes later, `svc_backup` logged
   in to `db-prod-01` from `10.20.167.107`, which is `jump-prod-02` itself.
5. **The L1 analyst stops at EDR.** Run it as `--persona l3_responder` and step 7 shows what
   happened on the host: a shell, `curl` downloading a script to a hidden directory in
   `/tmp`, the script running, a connection to `198.51.100.23:443`, and an `ssh` to
   `db-prod-01`.

The whole investigation cost about 1,200 tokens of results. One raw dump of the day's
failures would have cost 227,000.

Two things in the data are worth stopping on:

- **The L1 analyst can't look up `db-prod-01` in the CMDB.** It's a `restricted` asset, and
  row-level security hides it. But the auth logs name it plainly. Each backend enforces its
  own rules, and they don't always agree on what a role may learn. Unit 7 is about making
  them agree.
- **There's a syslog line on `jump-prod-02` at 02:52 addressed to "any AI assistant".** It
  asks the reader to close INC-0042 as a false positive. It's planted, and it's exactly what
  a log search returns: attacker-controlled text inside a tool result. Unit 10 attacks the
  agent with it. For now, note that `investigate.py`'s system prompt says log contents are
  data, not instructions. That helps, and it isn't a defence.

### Let the model do it

Now give the same server to `gpt-oss-120b` and ask it to triage INC-0042, several times:

```bash
python examples/04/investigate.py --model --repeats 3
```

Each run is a fresh conversation through `unit2.run_turn`. The script records the run's
**path** (which tools, in what order), how many bytes of results it pulled in, whether it
**aggregated before its first search**, and how many calls failed. It then checks the answer
against **facts computed live from the estate as the same persona**:
- the attacker's address;
- the account used;
- the first successful login, as HH:MM UTC;
- the next host;
- for the L3 responder, also the download / C2 address.

The score is the share of those facts the answer states. As in Unit 3, read the **range**
across runs, not just the mean.

Things to look for when you compare its path with yours:

- **Did it count first?** A run that skips the histogram has to read its way to the spike:
  a search over the suggested window matches 1,501 events from the attacker alone. The
  `hint` on the first page is there to turn it around.
- **How did it handle refusals?** A 3-day `search_events` is refused with "narrow it". A good
  run narrows it. A bad one retries the same call. The errors are written as instructions
  partly so that this gets measured.
- **Did it say UTC?** The tools return UTC with a `Z` unless asked otherwise, and the system
  prompt asks for UTC. A run that reports 04:31 has converted to a local zone without saying
  so.

The lesson makes no claim about scores; run it on your endpoint and keep the numbers. Unit 6
uses the same harness to A/B tool descriptions, and Unit 12's capstone grows this triage into
the gateway's main job.

> **Security:** the tools' shape is the first control. No tool accepts Query DSL, scripts or
> wildcards, every field passes the persona's catalog, the search window is capped at 24
> hours, and pages at 50. Within that, OpenSearch still enforces the real rules: index
> permissions (L1 gets no EDR), field-level security, masking. The catalog check means the
> server refuses before the backend would answer silently. Cursors are HMAC-sealed with a
> per-process key and bound to their query, so a model can't page into a search it didn't
> make. Results contain attacker-controlled text (log lines, user agents, command lines). The
> planted syslog line shows that, and Unit 10 is about it. The server still acts as one fixed
> persona; Unit 7 replaces that with the caller.

> **Observe:** the joinable log now records each request's backend cost (`indexes`, `shards`,
> `took_ms`, `hits`, `returned`, `page`), joined to the session, trace and step that caused it.
> That answers *which conversation made the cluster work hardest, and was it the histogram or
> the paging?* `investigate.py` records each run's path, bytes, refusals and
> `aggregated_first`, across repeats. Watch `aggregated_first` and the share of
> `tool_error`s: they show whether the instructions and error messages are teaching the
> model.

> **Limits:**
> - **Context.** Every answer is bounded by design: a histogram ≤ 200 buckets
>   (~1-3 KB), a top-N ≤ 50 values (<1 KB), a page ≤ 50 events with text cut at 300
>   characters (~5 KB for 20 auth events). A PIT cursor costs about 500 characters per index
>   in the window, sent back on every page. Returning fewer `fields` is the biggest lever: the
>   same 20 events with two fields are 2.1 KB.
> - **Cluster.** Naming the days limits a search to the shards that can hold an answer.
>   Each open PIT holds resources until it's deleted or expires. The cluster allows 300
>   (`search.max_open_pit_context`), shared by every caller. That's why the last page deletes
>   it and the keep-alive is two minutes. `track_total_hits: true` costs a full count, so only
>   page 1 asks for it.
> - **What's not bounded yet.** OpenSearch's default search timeout is *none*
>   (`search.default_search_timeout: -1`). There's also no per-caller rate or concurrency
>   limit. Unit 8 adds both, with query-cost gates.

> **Porting to Elasticsearch:** the same design works. Elasticsearch's point in time is
> `POST /<index>/_pit?keep_alive=2m` (OpenSearch: `POST /<index>/_search/point_in_time`), and
> `search_after` with `_shard_doc` works the same. Field-level security and masking need a
> paid Elastic licence. Without them, the catalog check is the only thing standing between a
> model and those fields.

## Lab: investigate the planted incident

Open [`labs/04_searching_logs.py`](../labs/04_searching_logs.py) with the estate running:

```bash
cp labs/04_searching_logs.py work/
marimo edit work/04_searching_logs.py
```

It drives the SIEM server through the Unit 2 host, one call at a time, as the persona you
pick:

1. List the active incidents and open one; its suggested window fills in below.
2. Edit the window and see how it's read, which daily indexes it touches, and whether the
   aggregations and the search would accept it.
3. Draw the histogram (filters as JSON) and find the spike.
4. Count the top values of any catalogued field behind it.
5. Read the events a page at a time with **next page**. Change the filters while a cursor
   is live and see the refusal.
6. Measure what raw hits would have cost.
7. Compare your own path (every call you made, with its bytes) with the lesson's path, and,
   with a model endpoint, with the model's path over several runs.

## Challenges

1. **A `count_events` tool.** Some questions need one number, not a histogram ("how many
   5xx responses yesterday?"). Add `count_events(dataset, start, end, filters, text)` with
   the same window and field rules, returning `{"window", "total"}`. *Success:* as the L1
   analyst, it returns the same total as `event_histogram` for three different filter sets,
   in under 300 bytes each. A filter on `user.email` is refused with the same message as in
   `search_events`.
2. **Relative to the incident.** Let `start` and `end` accept `incident-1h` and
   `incident+30m` when an `incident_id` argument is given, resolved from the incident's
   `created` time. *Success:* `search_events(dataset="auth", incident_id="INC-0042",
   start="incident-10m", end="incident", filters={"event.outcome": "success",
   "source.ip": "203.0.113.77"})` returns the 02:31:12 login. An unknown incident id is
   refused with a pointer to `list_incidents`.
3. **Make the model count first.** Run `investigate.py --model --repeats 5` and note how
   often it aggregates first. Then change *only* the tool descriptions and the server's
   instructions (not the limits) to push it towards the histogram. *Success:* a before/after
   table of `aggregated_first`, score (mean and range) and result bytes over at least 5 runs
   each, and one sentence that doesn't claim a difference where the ranges overlap.

## Recap

- A thin pass-through search fails three ways on a log cluster: no window reads everything,
  raw documents flood the context (227k tokens for one day of failed logins), and the
  question usually wanted a pattern anyway.
- **Every log query has a bounded window.** Parse what models actually write; read naive
  times in the caller's `tz` and echo the result; refuse backwards, future, pre-retention
  and too-wide windows with an instruction. Aggregations may span retention; raw search, 24 h.
- **Name the daily indexes** the window touches: 1 shard instead of 21.
- **Aggregate first, then drill down.** Histogram for *when* (~600 tokens), top values for
  *who* (~150), then a narrow search for *what*. The whole INC-0042 triage cost ~1,200
  tokens of results.
- **Shape the results:** catalogued fields only, checked per persona; text cut short; pages
  of at most 50; `total` always reported; a `hint` to aggregate when a search matches far more
  than a page.
- **Page with a point in time and `search_after`,** behind a cursor that's sealed, bound to
  its query, and carries the frozen window. The server stays stateless, and the last page
  frees the PIT.
- The joinable log now carries each request's backend cost, via `log_note`.

## Next

**[Unit 5: The Permission Dial: Restrictive vs Permissive Tools](05-the-permission-dial.md)**: the tools
you just built are the restrictive end. Unit 5 puts them next to raw SQL, raw Query DSL
(OpenSearch's own MCP server) and a guarded middle, and measures success, tokens, latency and
blast radius on the same task set.
