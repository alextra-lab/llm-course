"""
Unit 3: the curated catalog -- what an agent is told about the CMDB and the SIEM.

The raw schemas say what *exists*: column names and types, a mapping with 296 fields per
index. This file says what the data *means*, which of it is worth asking about, how to ask, and
who may see it. A profiler can draft it (examples/03/profile.py); a person who knows the data
finishes it.

Every dataset says:
  - what one row / document is, and how to join or filter it;
  - for OpenSearch: the index pattern, the time field and the retention;
  - per field: its type, how to use it (filter, search, time, metric, join, id), a description,
    example values, and a sensitivity tag;
  - who may see it (`allowed`), mirroring what Postgres and OpenSearch enforce.

The catalog is NOT the security boundary -- the backends are (row-level security, column
grants, field-level security). The catalog's job is to agree with them, so the model is never
told about data it would be refused, and is told plainly when something is withheld.
`check()` proves that agreement against the live estate; run it in CI.

    python examples/03/catalog.py                     # the catalog as each persona sees it
    python examples/03/catalog.py --check             # agree with the live estate? (exit 1 if not)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/

# Sensitivity tags. `secret` fields are never catalogued for anyone: not even their names
# reach a model. `pii` fields are catalogued only for the roles the backend lets read them.
PUBLIC, INTERNAL, PII, SECRET = "public", "internal", "pii", "secret"

ANALYSTS = ("l1_analyst", "l3_responder", "auditor")
RESPONDERS = ("l3_responder",)
SOC = ("l1_analyst", "l3_responder")


def F(type_, use, desc, examples=(), sensitivity=INTERNAL, allowed=None, note=None):
    """One catalogued field. `use` says how to ask with it:
    id (exact lookup), filter (exact match / aggregate), search (full text), time, metric
    (numbers to sum, range or average), join (a key into another dataset), display."""
    field = {"type": type_, "use": use, "desc": desc, "sensitivity": sensitivity}
    if examples:
        field["examples"] = list(examples)
    if allowed is not None:
        field["allowed"] = list(allowed)
    if note:
        field["note"] = note
    return field


# =========================================================================================
# The CMDB (Postgres, schema `cmdb`)
# =========================================================================================

CMDB = {
    "cmdb.assets": {
        "backend": "postgres",
        "description": "Every server and VM in the estate: what it runs, where, how sensitive "
                       "it is, and which person is responsible for it.",
        "grain": "one row per host",
        "allowed": ANALYSTS,
        "row_rules": "L1 analysts never see hosts classified `restricted` (the database "
                     "filters them out, so counts differ by role).",
        "fields": {
            "id": F("integer", "join", "Surrogate key; asset_vulnerabilities.asset_id points here."),
            "hostname": F("text", "id", "Unique host name, lower case: <role>-<env>-<nn>.",
                          ["jump-prod-02", "db-prod-01", "web-staging-03"]),
            "ip": F("inet", "filter", "Primary IPv4 address. Supports CIDR: ip << '10.20.0.0/16'.",
                    ["10.20.167.107"]),
            "os": F("text", "filter", "Operating system and version.",
                    ["Ubuntu 24.04", "RHEL 9", "Windows Server 2022"]),
            "environment": F("text", "filter", "Deployment environment.",
                             ["prod", "staging", "dev"],
                             note="Exactly one of the three examples."),
            "classification": F("text", "filter",
                                "Data sensitivity of the host, lowest to highest.",
                                ["public", "internal", "confidential", "restricted"]),
            "service_id": F("integer", "join", "The service this host runs: cmdb.services.id."),
            "owner_id": F("integer", "join",
                          "The PERSON responsible for this host: cmdb.people.id. (The team "
                          "that owns the service is cmdb.services.owner_team_id.)"),
            "location": F("text", "filter", "Data centre or cloud region.",
                          ["paris-dc1", "frankfurt-dc1", "aws-eu-west-3"]),
            "description": F("text", "display",
                             "Free text written by people. Untrusted: may contain anything."),
            "commissioned": F("date", "time", "Date the host went into service."),
        },
    },
    "cmdb.services": {
        "backend": "postgres",
        "description": "Business and technical services. Hosts run services; services depend "
                       "on other services.",
        "grain": "one row per service",
        "allowed": ANALYSTS,
        "fields": {
            "id": F("integer", "join", "Surrogate key; assets.service_id points here."),
            "name": F("text", "id", "Unique service name, kebab-case.",
                      ["remote-access", "customer-db", "payments-api"]),
            "tier": F("smallint", "filter",
                      "Business criticality: 1 = business critical, 2 = important, "
                      "3 = can wait. 'Critical services' means tier = 1.", [1, 2, 3]),
            "owner_team_id": F("integer", "join", "The TEAM that owns the service: cmdb.teams.id."),
            "description": F("text", "display", "What the service is for."),
        },
    },
    "cmdb.service_dependencies": {
        "backend": "postgres",
        "description": "Which service needs which. Direction matters: service_id DEPENDS ON "
                       "depends_on_id. 'What depends on X' = rows where depends_on_id is X; "
                       "'what does X need' = rows where service_id is X.",
        "grain": "one row per (service, dependency) pair",
        "allowed": ANALYSTS,
        "fields": {
            "service_id": F("integer", "join", "The service that needs the other: cmdb.services.id."),
            "depends_on_id": F("integer", "join", "The service it needs: cmdb.services.id."),
        },
    },
    "cmdb.teams": {
        "backend": "postgres",
        "description": "Engineering teams, with their shared mailbox and on-call channel.",
        "grain": "one row per team",
        "allowed": ANALYSTS,
        "fields": {
            "id": F("integer", "join", "Surrogate key."),
            "name": F("text", "id", "Team name.", ["Remote Access", "Data", "SRE"]),
            "email": F("text", "display", "Team mailbox (not a person's address)."),
            "oncall_slack": F("text", "display", "On-call channel.", ["#oncall-data"]),
        },
    },
    "cmdb.people": {
        "backend": "postgres",
        "description": "People who own hosts, and the team each belongs to.",
        "grain": "one row per person",
        "allowed": ANALYSTS,
        "fields": {
            "id": F("integer", "join", "Surrogate key; assets.owner_id points here."),
            "full_name": F("text", "display", "First and last name.", ["Camille Michel"],
                           sensitivity=PII),
            "team_id": F("integer", "join", "The person's team: cmdb.teams.id."),
            "title": F("text", "filter", "Job title.", ["Engineer", "Team Lead"]),
            "email": F("text", "display", "Personal work e-mail.", sensitivity=PII,
                       allowed=("l3_responder", "auditor")),
            "phone": F("text", "display", "Personal phone number.", sensitivity=PII,
                       allowed=("l3_responder", "auditor")),
        },
    },
    "cmdb.vulnerabilities": {
        "backend": "postgres",
        "description": "Known vulnerabilities (fictional CVE-2099-* ids) and their severity.",
        "grain": "one row per CVE",
        "allowed": ANALYSTS,
        "fields": {
            "cve_id": F("text", "id", "CVE id.", ["CVE-2099-1001"]),
            "severity": F("text", "filter", "Vendor severity.",
                          ["low", "medium", "high", "critical"]),
            "cvss": F("numeric", "metric", "CVSS base score, 0.0 to 10.0.", [9.8]),
            "summary": F("text", "display", "One-line description."),
        },
    },
    "cmdb.asset_vulnerabilities": {
        "backend": "postgres",
        "description": "Which hosts are affected by which CVE, and the finding's state. Join "
                       "asset_id to cmdb.assets.id and cve_id to cmdb.vulnerabilities.",
        "grain": "one row per (host, CVE) finding",
        "allowed": ANALYSTS,
        "row_rules": "Follows cmdb.assets: L1 analysts don't see findings on restricted hosts.",
        "fields": {
            "asset_id": F("integer", "join", "The affected host: cmdb.assets.id."),
            "cve_id": F("text", "join", "The CVE: cmdb.vulnerabilities.cve_id."),
            "detected_on": F("date", "time", "Date the scanner first reported it."),
            "status": F("text", "filter",
                        "open = still exploitable; mitigated = fixed or compensated; "
                        "accepted = risk signed off, not fixed.",
                        ["open", "mitigated", "accepted"]),
        },
    },
}

# =========================================================================================
# The SIEM (OpenSearch). Log indexes are daily: <prefix>-YYYY.MM.DD in UTC, 21 days kept.
# =========================================================================================

LOG_RETENTION = "21 days, one index per UTC day (e.g. logs-auth-2026.10.08); query the pattern"

LOG_BASE = {
    "@timestamp": F("date", "time", "When the event happened, UTC. Every search should "
                    "carry a range on it."),
    "message": F("text", "search", "The raw log line. Full text: use `match`; exact "
                 "string: message.keyword."),
    "event.dataset": F("keyword", "filter", "Which log source.",
                       ["auth", "web", "syslog", "edr"]),
    "event.action": F("keyword", "filter", "What happened."),
    "event.category": F("keyword", "filter", "Broad event class."),
    "host.name": F("keyword", "join", "The host the event is about: cmdb.assets.hostname.",
                   ["jump-prod-02"]),
    "host.ip": F("ip", "filter", "The host's address. Supports CIDR in `term`."),
    "host.os.name": F("keyword", "filter", "The host's OS, as in the CMDB."),
    "labels.environment": F("keyword", "filter", "prod, staging or dev, as in the CMDB."),
}


def _log(**extra):
    return {**LOG_BASE, **extra}


SIEM = {
    "logs-auth-*": {
        "backend": "opensearch",
        "description": "SSH logins on Linux hosts, successful and failed.",
        "grain": "one document per login attempt",
        "time_field": "@timestamp",
        "retention": LOG_RETENTION,
        "allowed": SOC,
        "fields": _log(**{
            "event.action": F("keyword", "filter", "Always ssh_login here.", ["ssh_login"]),
            "event.outcome": F("keyword", "filter", "Whether the login worked.",
                               ["success", "failure"]),
            "event.reason": F("keyword", "filter", "Why a login failed (failures only).",
                              ["invalid_password"]),
            "user.name": F("keyword", "filter", "The account that tried to log in.",
                           ["svc_backup", "alice.m", "root"]),
            "source.ip": F("ip", "filter", "Where the attempt came from. CIDR works in `term`.",
                           ["203.0.113.77", "10.30.4.17"]),
            "source.geo.country_iso_code": F("keyword", "filter",
                                             "Source country; ZZ = unknown / external.",
                                             ["FR", "DE", "ZZ"]),
        }),
    },
    "logs-web-*": {
        "backend": "opensearch",
        "description": "HTTP access logs from the web, order, payment and search front ends.",
        "grain": "one document per HTTP request",
        "time_field": "@timestamp",
        "retention": LOG_RETENTION,
        "allowed": SOC,
        "fields": _log(**{
            "event.action": F("keyword", "filter", "Always http_request here."),
            "event.outcome": F("keyword", "filter", "success if status < 400, else failure.",
                               ["success", "failure"]),
            "event.duration": F("long", "metric", "Request time in NANOSECONDS "
                                "(divide by 1e6 for ms)."),
            "http.request.method": F("keyword", "filter", "HTTP method.", ["GET", "POST"]),
            "http.response.status_code": F("short", "filter",
                                           "HTTP status. Server errors: range 500-599.",
                                           [200, 404, 500, 503]),
            "http.response.bytes": F("long", "metric", "Response size in bytes."),
            "url.path": F("keyword", "filter", "Request path, ids included.",
                          ["/login", "/api/v1/payments"]),
            "user_agent.original": F("text", "search", "Client user agent. Exact: "
                                     "user_agent.original.keyword."),
            "source.ip": F("ip", "filter", "Client address."),
            "source.geo.country_iso_code": F("keyword", "filter", "Client country."),
            "user.name": F("keyword", "filter", "Signed-in user, when there is one "
                           "(about 1 request in 5)."),
            "user.email": F("keyword", "filter", "Signed-in user's e-mail.", sensitivity=PII,
                            allowed=RESPONDERS,
                            note="Masked for you: you see a hash, the same for the same "
                                 "address. A terms aggregation on it works (to correlate); "
                                 "term and exists filters on it silently match nothing."),
        }),
    },
    "logs-syslog-*": {
        "backend": "opensearch",
        "description": "System logs from Linux hosts (sshd, cron, kernel, nginx, ...).",
        "grain": "one document per log line",
        "time_field": "@timestamp",
        "retention": LOG_RETENTION,
        "allowed": SOC,
        "fields": _log(**{
            "log.level": F("keyword", "filter", "Severity.", ["info", "warning", "error"]),
            "process.name": F("keyword", "filter", "The program that logged it.",
                              ["sshd", "cron", "kernel", "nginx"]),
        }),
    },
    "edr-events-*": {
        "backend": "opensearch",
        "description": "Endpoint detection telemetry: processes started, files written, "
                       "network connections, and detection rules that fired.",
        "grain": "one document per endpoint event",
        "time_field": "@timestamp",
        "retention": LOG_RETENTION,
        "allowed": RESPONDERS,
        "fields": _log(**{
            "event.action": F("keyword", "filter", "What the endpoint saw.",
                              ["process_start", "file_create", "network_connect"]),
            "event.outcome": F("keyword", "filter", "Always success here."),
            "event.severity": F("short", "filter", "1 = routine, 3 = suspicious.", [1, 3]),
            "user.name": F("keyword", "filter", "The account the process ran as."),
            "process.name": F("keyword", "filter", "Executable name.", ["bash", "curl", "sh"]),
            "process.pid": F("long", "display", "Process id."),
            "process.command_line": F("text", "search", "Full command line. Exact: "
                                      "process.command_line.keyword."),
            "process.parent.name": F("keyword", "filter", "Parent executable."),
            "file.path": F("keyword", "filter", "File written (file_create only)."),
            "file.hash.sha256": F("keyword", "filter", "SHA-256 of that file."),
            "destination.ip": F("ip", "filter", "Remote address (network_connect only)."),
            "destination.port": F("integer", "filter", "Remote port."),
            "rule.name": F("keyword", "filter", "Detection rule that fired, if any."),
        }),
    },
    "incidents": {
        "backend": "opensearch",
        "description": "SOC incidents: one per triaged detection, with status and owner.",
        "grain": "one document per incident",
        "time_field": "incident.created",
        "retention": "all incidents, one index",
        "allowed": SOC,
        "fields": {
            "incident.id": F("keyword", "id", "Incident id.", ["INC-0042"]),
            "incident.title": F("text", "search", "Title. Exact: incident.title.keyword."),
            "incident.status": F("keyword", "filter",
                                 "open and in_progress are active; closed and "
                                 "false_positive are done.",
                                 ["open", "in_progress", "closed", "false_positive"]),
            "incident.severity": F("keyword", "filter", "Triage severity.",
                                   ["low", "medium", "high"]),
            "incident.created": F("date", "time", "When it was opened, UTC."),
            "incident.updated": F("date", "time", "Last change, UTC."),
            "incident.assignee": F("keyword", "filter", "Analyst on it, or `unassigned`."),
            "incident.rule": F("keyword", "filter", "Detection rule that raised it."),
            "incident.summary": F("text", "search", "Analyst-facing summary."),
            "incident.alert_count": F("integer", "metric", "Alerts grouped into it."),
            "host.name": F("keyword", "join", "Affected host: cmdb.assets.hostname."),
            "source.ip": F("ip", "filter", "Attacker or source address, when known."),
            "user.name": F("keyword", "filter", "Account involved, when known."),
            "tags": F("keyword", "filter", "Free tags, e.g. the environment.", ["prod"]),
        },
    },
}

# Fields that exist in the backend but are never catalogued for anyone (sensitivity: secret).
WITHHELD = {"logs-web-*": {"labels.api_token": SECRET}}

DATASETS = {**CMDB, **SIEM}
VERSION = hashlib.sha256(json.dumps([DATASETS, WITHHELD], sort_keys=True).encode()).hexdigest()[:12]


# =========================================================================================
# The catalog as one persona sees it
# =========================================================================================

def _visible_fields(dataset: dict, persona: str) -> tuple[dict, int]:
    fields, withheld = {}, 0
    for name, field in dataset["fields"].items():
        if persona in field.get("allowed", dataset["allowed"]):
            fields[name] = {k: v for k, v in field.items() if k != "allowed"}
        else:
            withheld += 1
    return fields, withheld


def datasets_for(persona: str) -> list[str]:
    return [name for name, d in DATASETS.items() if persona in d["allowed"]]


def summary(persona: str) -> list[dict]:
    """The short list: what each dataset is. Cheap enough to load up front."""
    return [{"name": name, "backend": DATASETS[name]["backend"],
             "description": DATASETS[name]["description"]}
            for name in datasets_for(persona)]


def describe(name: str, persona: str) -> dict | None:
    """One dataset in full, as `persona` may use it. None if they may not see it at all."""
    d = DATASETS.get(name)
    if d is None or persona not in d["allowed"]:
        return None
    fields, withheld = _visible_fields(d, persona)
    out = {"name": name, "backend": d["backend"], "description": d["description"],
           "grain": d["grain"]}
    for key in ("time_field", "retention", "row_rules"):
        if key in d:
            out[key] = d[key]
    out["fields"] = fields
    withheld += len(WITHHELD.get(name, {}))
    if withheld:
        out["withheld"] = (f"{withheld} field(s) exist but are not available to your role; "
                           "don't try to query them.")
    return out


def instructions_line(persona: str) -> str:
    """A one-paragraph summary for a server's `instructions`."""
    names = ", ".join(datasets_for(persona)) or "nothing"
    return (f"Catalog {VERSION} of the CMDB (Postgres) and the SIEM (OpenSearch). You may "
            f"use: {names}. Call describe_dataset before querying a dataset you haven't "
            "described yet in this conversation.")


# =========================================================================================
# Does the catalog agree with the live estate?
# =========================================================================================

def _flatten(props: dict, prefix: str = "") -> set[str]:
    out = set()
    for key, spec in props.items():
        if "properties" in spec:
            out |= _flatten(spec["properties"], f"{prefix}{key}.")
        else:
            out.add(prefix + key)
    return out


def check(persona: str) -> list[str]:
    """Problems where the catalog promises `persona` something the backend refuses, or a
    `secret` field is visible to them. An empty list means the catalog and the backend agree."""
    from common_mcp import os_client, pg_connect

    problems = []
    with pg_connect(persona) as conn:
        cols = conn.execute(
            "SELECT table_schema || '.' || table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'cmdb'").fetchall()
    live_pg: dict[str, set] = {}
    for table, column in cols:
        live_pg.setdefault(table, set()).add(column)

    client = os_client(persona)
    for name in datasets_for(persona):
        mine = describe(name, persona)["fields"]
        if DATASETS[name]["backend"] == "postgres":
            missing = set(mine) - live_pg.get(name, set())
            what = f"columns {sorted(missing)}" if live_pg.get(name) else "the table"
        else:
            try:
                mapping = client.indices.get_mapping(index=name)
            except Exception as e:  # noqa: BLE001 -- a 403 is exactly what we're looking for
                problems.append(f"{persona}: catalog offers {name}, OpenSearch refuses: "
                                f"{type(e).__name__}")
                continue
            live = _flatten(next(iter(mapping.values()))["mappings"]["properties"])
            missing = {f for f in mine if f not in live}
            what = f"fields {sorted(missing)}"
            for secret in WITHHELD.get(name, {}):
                if secret in live:
                    problems.append(f"{persona}: secret field {name}:{secret} is visible in "
                                    "the backend (field-level security is missing)")
        if missing:
            problems.append(f"{persona}: catalog offers {name} {what}, which the backend "
                            "doesn't have or won't show this persona")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="The Unit 3 curated catalog.")
    parser.add_argument("--check", action="store_true",
                        help="verify the catalog against the live estate, for every persona")
    args = parser.parse_args()
    if args.check:
        from common_mcp import load_env

        load_env()
        problems = [p for persona in ANALYSTS for p in check(persona)]
        for p in problems:
            print("FAIL", p)
        print(f"catalog {VERSION}: {'agrees with' if not problems else 'DISAGREES with'} "
              f"the estate for {', '.join(ANALYSTS)}")
        return 1 if problems else 0
    print(f"catalog {VERSION}")
    for persona in ANALYSTS:
        full = [describe(n, persona) for n in datasets_for(persona)]
        print(f"\n{persona}: {len(full)} datasets, "
              f"{sum(len(d['fields']) for d in full)} fields, "
              f"summary {len(json.dumps(summary(persona)))} bytes, "
              f"everything {len(json.dumps(full))} bytes")
        for d in full:
            note = "  (" + d["withheld"].split(" exist")[0] + " withheld)" if "withheld" in d else ""
            print(f"  {d['name']:<28} {len(d['fields']):>3} fields{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
