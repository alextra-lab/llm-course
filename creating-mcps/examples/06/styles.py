"""
Unit 6: three ways to describe the same nine tools.

The tools are Unit 5's restrictive variant: Unit 3's and Unit 5's CMDB tools, and Unit 4's
SIEM tools. Their code never changes here; only what the model reads about them does.

    terse      what many real servers ship: a few words per tool, nothing per parameter
    original   the descriptions as Units 3-5 wrote them (docstrings)
    rewritten  this unit's rules applied: what it answers, when to use it (and what to use
               instead), the limits, one example; a description on every parameter; bounds
               and fixed choices in the schema, not only in prose; read-only annotations
    lean       rewritten, with each shared convention (the window format, the filter
               syntax) spelled out once, in event_histogram, and referred to elsewhere

`styled_server.py` serves any of them; `selection.py` and `recovery.py` measure them.
"""

from __future__ import annotations

STYLES = ("terse", "original", "rewritten", "lean")

READ_ONLY = {"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True,
             "open_world_hint": False}

WINDOW = ("ISO 8601 (2026-10-09T02:00:00Z; a time without an offset is read in `tz`) or "
          "relative (now-24h, now-3d)")
FILTERS = ('exact values on the dataset\'s fields, e.g. {"event.outcome": "failure", '
           '"host.name": "jump-prod-02"}; a list means any-of; {"gte": 500, "lt": 600} is a '
           "range")

DATASETS = "which logs"                     # completed per persona by styled_server
DATASET_MEANING = {"auth": "logins", "web": "HTTP requests", "syslog": "system logs",
                   "edr": "process telemetry"}

TOOLS: dict[str, dict] = {
    # --- CMDB ------------------------------------------------------------------------------
    "get_asset": {
        "server": "cmdb",
        "terse": "Get an asset.",
        "rewritten": (
            "One host's CMDB record by exact hostname: OS, environment, classification, the "
            "service it runs, its owner and the owner's team. Use it for 'who owns X', 'what "
            "runs on X', 'is X production'. For several hosts use find_assets; for a count "
            "use count_assets."),
        "params": {"hostname": {"description": "exact short hostname, e.g. jump-prod-02 "
                                               "(no domain)"}},
    },
    "find_assets": {
        "server": "cmdb",
        "terse": "Find assets.",
        "rewritten": (
            "List hosts by service and/or environment, with the total that matched. Use it "
            "to see which hosts they are; if you only need how many, count_assets is "
            "cheaper. Returns at most `limit` hosts."),
        "params": {
            "service": {"description": "service name, e.g. customer-db, payments-api"},
            "environment": {"description": "prod, staging or dev"},
            "limit": {"description": "hosts to return", "ge": 1, "le": 50},
        },
    },
    "service_dependencies": {
        "server": "cmdb",
        "terse": "Service dependencies.",
        "rewritten": (
            "Both directions for one service: depends_on (what it needs) and depended_on_by "
            "(what would break if it went down). Use it for blast-radius and 'what does X "
            "need' questions."),
        "params": {"service": {"description": "service name, e.g. customer-db"}},
    },
    "hosts_with_vulnerability": {
        "server": "cmdb",
        "terse": "Hosts with a vulnerability.",
        "rewritten": (
            "Hosts that have one CVE, by finding status (open by default) and optionally "
            "environment. Use it for 'which hosts' and 'how many hosts' have a CVE: `total` "
            "counts every matching host; `hosts` lists up to `limit` of them."),
        "params": {
            "cve_id": {"description": "CVE id, e.g. CVE-2099-1068"},
            "environment": {"description": "prod, staging or dev; omit for all"},
            "status": {"description": "open (still exposed), mitigated, or accepted (risk "
                                      "accepted, not fixed)"},
            "limit": {"description": "hosts to list (the total is always exact)",
                      "ge": 1, "le": 50},
        },
    },
    "count_assets": {
        "server": "cmdb",
        "terse": "Count assets.",
        "rewritten": (
            "How many hosts match, without listing them. Combine any of environment, tier, "
            "classification and service. Use it for every 'how many hosts' question."),
        "params": {
            "environment": {"description": "prod, staging or dev"},
            "tier": {"description": "tier of the service the host runs: 1 = business "
                                    "critical, 2 = important, 3 = least critical"},
            "classification": {"description": "data classification of the host"},
            "service": {"description": "service name, e.g. erp"},
        },
    },
    # --- SIEM ------------------------------------------------------------------------------
    "list_incidents": {
        "server": "siem",
        "terse": "List incidents.",
        "rewritten": (
            "SOC incidents, newest first, with the total that matched. Use it to find "
            "incidents by status, severity or host; then get_incident for one in full. "
            "'Active' means status open or in_progress."),
        "params": {
            "status": {"description": 'any of open, in_progress, closed, false_positive; '
                                      'e.g. ["open", "in_progress"] for active ones'},
            "severity": {"description": "low, medium or high"},
            "host": {"description": "exact hostname the incident is about"},
            "since": {"description": "created at or after: " + WINDOW},
            "until": {"description": "created before: " + WINDOW},
            "limit": {"description": "incidents per page", "ge": 1, "le": 50},
            "offset": {"description": "skip this many, to page", "ge": 0, "le": 1000},
        },
    },
    "get_incident": {
        "server": "siem",
        "terse": "Get an incident.",
        "rewritten": (
            "One incident in full: summary, rule, host, status, and a suggested_window (an "
            "hour either side of when it was raised). Use it before the log tools: pass the "
            "window to them as start/end."),
        "params": {"incident_id": {"description": "incident id, e.g. INC-0042"}},
    },
    "event_histogram": {
        "server": "siem",
        "terse": "Event histogram.",
        "rewritten": (
            "Counts of log events per time bucket, plus the total and the peak. Use it FIRST "
            "for 'when' and 'how many' questions about logs: it costs a few hundred tokens "
            "whatever the volume. Window up to 21 days. Then top_values for 'who', and "
            "search_events (24h max) to read events."),
        "params": {
            "dataset": {"description": DATASETS},
            "start": {"description": "window start: " + WINDOW},
            "end": {"description": "window end, default now"},
            "filters": {"description": FILTERS},
            "text": {"description": "words that must all appear in the message"},
            "interval": {"description": "bucket size; auto picks one",
                         "choices": ("auto", "1m", "5m", "10m", "15m", "30m", "1h", "3h",
                                     "6h", "12h", "1d")},
            "tz": {"description": "time zone for naive times and for the answer"},
        },
    },
    "top_values": {
        "server": "siem",
        "terse": "Top values.",
        "rewritten": (
            "The most common values of one field among matching log events, with counts: "
            "'which IP', 'which user', 'which host'. Same window (21 days max), filters and "
            "text as event_histogram. other_count is how many matches had another value."),
        "params": {
            "dataset": {"description": DATASETS},
            "field": {"description": "field to rank, e.g. source.ip, user.name, host.name, "
                                     "http.response.status_code"},
            "start": {"description": "window start: " + WINDOW},
            "end": {"description": "window end, default now"},
            "filters": {"description": FILTERS},
            "text": {"description": "words that must all appear in the message"},
            "size": {"description": "how many values", "ge": 1, "le": 50},
            "tz": {"description": "time zone for naive times"},
        },
    },
    "search_events": {
        "server": "siem",
        "terse": "Search events.",
        "rewritten": (
            "The matching log events themselves, a page at a time, oldest first. Window at "
            "most 24h, so find it with event_histogram first. `total` counts every match. If "
            "next_cursor is set, call again with the same arguments plus cursor."),
        "params": {
            "dataset": {"description": DATASETS},
            "start": {"description": "window start: " + WINDOW},
            "end": {"description": "window end, at most 24h after start"},
            "filters": {"description": FILTERS},
            "text": {"description": "words that must all appear in the message"},
            "fields": {"description": "fields to return; default a short set per dataset"},
            "limit": {"description": "events per page", "ge": 1, "le": 50},
            "newest_first": {"description": "true to start from the latest event"},
            "tz": {"description": "time zone for naive times and for the answer"},
            "cursor": {"description": "next_cursor from the previous page, unchanged"},
        },
    },
}


SHORT = {WINDOW: "same format as event_histogram's start",
         FILTERS: "same syntax as event_histogram's filters"}
# Lean also drops prose that only restates the schema (the enum already lists the values).
RESTATED = {("find_assets", "environment"), ("hosts_with_vulnerability", "environment"),
            ("count_assets", "environment"), ("count_assets", "classification"),
            ("list_incidents", "severity")}


def params_for(style: str, tool: str, datasets: list[str] | None = None) -> dict[str, dict]:
    """Parameter specs for the rewritten and lean styles (copies, safe to change).
    `datasets` (the persona's) narrows the dataset choices and its description."""
    params = {k: dict(v) for k, v in TOOLS[tool]["params"].items()}
    if "dataset" in params and datasets is not None:
        meaning = ", ".join(f"{d} = {DATASET_MEANING[d]}" for d in datasets)
        params["dataset"] = {"choices": tuple(datasets), "description": f"{DATASETS}: {meaning}"}
    if style == "lean":
        for name in [n for n in params if (tool, n) in RESTATED]:
            params[name].pop("description")
    if style == "lean" and tool != "event_histogram":
        for spec in params.values():
            for long, short in SHORT.items():
                if long in spec.get("description", ""):
                    spec["description"] = spec["description"].replace(long, short)
    return params


def server_table(style: str, persona: str, errors: str = "instructive") -> dict:
    """The Unit 2 host's server table: the restrictive variant's two servers in `style`."""
    import os
    import sys
    from pathlib import Path

    from mcp import StdioServerParameters

    server = Path(__file__).resolve().parent / "styled_server.py"
    return {kind: StdioServerParameters(
                command=sys.executable, args=[str(server)],
                env={**os.environ, "MCP_SERVER_KIND": kind, "TOOL_STYLE": style,
                     "ERROR_STYLE": errors, "MCP_PERSONA": persona})
            for kind in ("cmdb", "siem")}
