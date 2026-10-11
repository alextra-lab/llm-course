"""
Shared helpers for the Creating MCPs course.

Like Foundations' examples/common.py, this holds the one bit of boilerplate every unit would
otherwise repeat -- here, *connecting to the estate as someone*. Every connection names a
persona, because in this course there is no such thing as an anonymous query: who is asking
decides what comes back (Unit 7 makes that the whole lesson).

Later units add to this file (the in-memory CMDB fixture in Unit 1, `trace_meta` and
`joinable_log` in Unit 2; later the per-caller limiter, the policy client). Re-copy it into
work/ when a unit says so.
Scripts import it the same way the Foundations examples import common.py:

    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
    from common_mcp import pg_connect, os_client

Configuration comes from creating-mcps/env/.env (copy env/.env.example). Values already in
your environment win, so a shared-lab learner can override PG_HOST / OS_HOST in their shell.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

COURSE_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = COURSE_DIR / "env" / ".env"
REPO_EXAMPLES = COURSE_DIR.parent / "examples"   # Foundations' examples/ (common.py lives there)

# persona -> the env var holding its password. The same four exist in Postgres and OpenSearch.
PERSONAS = {
    "l1_analyst": "PERSONA_L1_PASSWORD",
    "l3_responder": "PERSONA_L3_PASSWORD",
    "auditor": "PERSONA_AUDITOR_PASSWORD",
    "mcp_service": "PERSONA_SERVICE_PASSWORD",
}


def load_env(path: Path = ENV_FILE) -> bool:
    """Load KEY=VALUE lines from env/.env into os.environ without overriding what's set.

    Returns False when the file doesn't exist (the caller decides whether that matters).
    Deliberately tiny: no quoting rules, no interpolation -- the .env.example has none.
    """
    if not path.exists():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
    return True


load_env()


def _password(persona: str) -> str:
    if persona not in PERSONAS:
        raise ValueError(f"unknown persona {persona!r}; expected one of {sorted(PERSONAS)}")
    value = os.environ.get(PERSONAS[persona])
    if not value:
        raise RuntimeError(f"{PERSONAS[persona]} is not set -- copy env/.env.example to env/.env")
    return value


def pg_connect(persona: str = "l1_analyst", **kwargs):
    """Open a Postgres connection to the CMDB *as* a persona.

    Row-level security, column privileges, statement_timeout and the connection limit all
    follow the persona, not the code -- the database enforces them.
    """
    import psycopg

    return psycopg.connect(
        host=os.environ.get("PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("PG_PORT", "5433")),
        dbname=os.environ.get("PG_DATABASE", "cmdb"),
        user=persona,
        password=_password(persona),
        connect_timeout=5,
        **kwargs,
    )


def os_client(persona: str = "l1_analyst", **kwargs):
    """An OpenSearch client authenticated *as* a persona (index, field and document
    permissions are enforced by OpenSearch's security plugin)."""
    import logging

    from opensearchpy import OpenSearch

    # opensearch-py logs every request (and every 403) as plain text. On an MCP server that's
    # stderr, the joinable log's channel, and errors reach the caller as exceptions anyway.
    logging.getLogger("opensearch").setLevel(logging.CRITICAL)

    verify = os.environ.get("OS_VERIFY_CERTS", "0").lower() in ("1", "true", "yes")
    options = dict(
        hosts=[{"host": os.environ.get("OS_HOST", "127.0.0.1"),
                "port": int(os.environ.get("OS_PORT", "9200"))}],
        http_auth=(persona, _password(persona)),
        use_ssl=True,
        verify_certs=verify,
        ssl_show_warn=False,
        timeout=10,
    )
    if verify and os.environ.get("OS_CA_CERTS"):
        options["ca_certs"] = os.environ["OS_CA_CERTS"]
    options.update(kwargs)
    return OpenSearch(**options)


def trace_meta(session_id: str, trace_id: str, step: int) -> dict:
    """The joinable tuple for a request's `_meta` (Unit 1): a W3C `traceparent` carrying the
    trace id, plus this course's own `session_id` and `step` keys."""
    import secrets

    return {"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01",
            "session_id": session_id, "step": step}



def joinable_log(server: str):
    """Unit 1's joinable-log middleware, for any server: one JSONL record per request on stderr,
    stamped with the caller's session_id / trace_id / step from `_meta`. Unit 2 moved it here so
    every server logs the same way: `mcp.middleware.append(joinable_log("tickets"))`."""
    import json
    import time
    import uuid

    from mcp.shared.exceptions import MCPError

    async def middleware(ctx, call_next):
        meta = ctx.meta or {}
        if not isinstance(meta, dict):
            meta = meta.model_dump(by_alias=True, exclude_none=True)
        parts = str(meta.get("traceparent", "")).split("-")
        caller = len(parts) == 4 and len(parts[1]) == 32
        params = ctx.params or {}
        record = {
            "session_id": meta.get("session_id"),
            "trace_id": parts[1] if caller else uuid.uuid4().hex,
            "step": meta.get("step"),
            "operation": "mcp.request",
            "server": server,
            "method": ctx.method,
            "name": params.get("name") or params.get("uri"),
            "arg_keys": sorted((params.get("arguments") or {}).keys()),
            "client": (meta.get("io.modelcontextprotocol/clientInfo") or {}).get("name"),
            "protocol_version": ctx.protocol_version,
            "trace_origin": "caller" if caller else "server",
        }
        t0 = time.perf_counter()
        try:
            result = await call_next(ctx)
        except MCPError as e:
            record.update(outcome="error", code=e.error.code)
            raise
        except Exception as e:
            record.update(outcome="crash", error=type(e).__name__)
            raise
        else:
            get = result.get if isinstance(result, dict) else lambda k: getattr(
                result, {"isError": "is_error", "resultType": "result_type"}[k], None)
            record["outcome"] = ("input_required" if get("resultType") == "input_required"
                                 else "tool_error" if get("isError") else "ok")
            return result
        finally:
            record["ms"] = round((time.perf_counter() - t0) * 1000, 2)
            print(json.dumps(record), file=sys.stderr, flush=True)

    return middleware

# The CMDB's columns, in the order generate.cmdb() returns them (same as env/seed/cmdb.sql).
_CMDB_COLUMNS = {
    "teams": ("id", "name", "email", "oncall_slack"),
    "people": ("id", "full_name", "email", "phone", "team_id", "title"),
    "services": ("id", "name", "tier", "owner_team_id", "description"),
    "service_dependencies": ("service_id", "depends_on_id"),
    "assets": ("id", "hostname", "ip", "os", "environment", "classification", "service_id",
               "owner_id", "location", "description", "commissioned"),
    "vulnerabilities": ("cve_id", "severity", "cvss", "summary"),
    "asset_vulnerabilities": ("asset_id", "cve_id", "detected_on", "status"),
}
FIXTURE_ANCHOR = "2026-10-10"


def cmdb_fixture() -> dict[str, list[dict]]:
    """The estate's CMDB as plain Python data: {table: [row dict, ...]}. No Docker needed.

    Units 1-2 build servers against this so the protocol is the only new thing; Unit 3 moves
    them onto Postgres. It comes from the same seeded generator as `env.py seed`, so the
    hostnames, owners and services are the ones in the database (dates use a fixed anchor).
    Note what it is NOT: it has no personas, so it applies no row or column security --
    everything is visible to everyone, which is exactly the gap Unit 7 closes.
    """
    import random

    seed_dir = COURSE_DIR / "env" / "seed"
    if str(seed_dir) not in sys.path:
        sys.path.append(str(seed_dir))
    import generate

    rows = generate.cmdb(random.Random(generate.SEED), generate.parse_anchor(FIXTURE_ANCHOR))
    out = {}
    for table, columns in _CMDB_COLUMNS.items():
        out[table] = [
            {c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in zip(columns, row)}
            for row in rows[table]
        ]
    return out


def llm():
    """Return (client, model) for the course's LLM endpoint, or None when it isn't set.

    Reuses Foundations' examples/common.py, so the same .env and TLS knobs apply.
    """
    if not (os.environ.get("OPENAI_BASE_URL") and os.environ.get("OPENAI_API_KEY")):
        return None
    if str(REPO_EXAMPLES) not in sys.path:
        sys.path.append(str(REPO_EXAMPLES))
    from common import MODEL, get_client

    return get_client(), MODEL
