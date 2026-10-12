"""
Shared helpers for the Creating MCPs course.

Like Foundations' examples/common.py, this holds the one bit of boilerplate every unit would
otherwise repeat -- here, *connecting to the estate as someone*. Every connection names a
persona, because in this course there is no such thing as an anonymous query: who is asking
decides what comes back (Unit 7 makes that the whole lesson).

Later units add to this file (the in-memory CMDB fixture in Unit 1, `trace_meta` and
`joinable_log` in Unit 2, `log_note` in Unit 4, `acting_for` / `persona` and the gateway
login in Unit 7, `use_pools` and `backend_refusal` in Unit 8). Re-copy it into work/ when a
unit says so.
Scripts import it the same way the Foundations examples import common.py:

    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
    from common_mcp import pg_connect, os_client

Configuration comes from creating-mcps/env/.env (copy env/.env.example). Values already in
your environment win, so a shared-lab learner can override PG_HOST / OS_HOST in their shell.
"""

from __future__ import annotations

import atexit
import contextlib
import contextvars
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


# Unit 7: the persona the current request acts for, set per request from the caller's verified
# token. Unset (Units 0-6), a server acts for the one persona it was started as.
_ACTING_FOR: contextvars.ContextVar[str | None] = contextvars.ContextVar("acting_for",
                                                                        default=None)
GATEWAY = "mcp_service"   # the gateway's own login: holds nothing, acts as callers


@contextlib.contextmanager
def acting_for(persona: str):
    """Within this block, `persona()` is `persona`, and `pg_connect` / `os_client` log in as the
    gateway and act as it (Unit 7). The gateway never needs the caller's password."""
    token = _ACTING_FOR.set(persona)
    try:
        yield
    finally:
        _ACTING_FOR.reset(token)


def persona() -> str:
    """Who this request acts for: the authenticated caller's persona (Unit 7), else the one
    the server was started as (MCP_PERSONA, default l1_analyst)."""
    return _ACTING_FOR.get() or os.environ.get("MCP_PERSONA", "l1_analyst")


def _gateway_for(persona: str) -> bool:
    """True when this connection must go through the gateway login (Unit 7)."""
    acting = _ACTING_FOR.get()
    if acting is not None and persona != acting:
        raise PermissionError(f"this request acts for {acting!r}, not {persona!r}")
    return acting is not None


def _pg_params(login: str, **kwargs) -> dict:
    return dict(host=os.environ.get("PG_HOST", "127.0.0.1"),
                port=int(os.environ.get("PG_PORT", "5433")),
                dbname=os.environ.get("PG_DATABASE", "cmdb"),
                user=login, password=_password(login), connect_timeout=5, **kwargs)


_ROLE_SETTINGS: dict[str, list[tuple[str, str]]] = {}


def _become(conn, persona: str) -> None:
    """SET ROLE to the persona, and apply the persona's own settings (statement_timeout...).

    SET ROLE changes privileges only: settings from `ALTER ROLE ... SET` apply at LOGIN, so
    through the gateway an L1 analyst would get the gateway's 15 s instead of their own 5 s
    (Unit 8 found this). We read them from the catalog once and apply them ourselves."""
    from psycopg import sql

    if persona not in _ROLE_SETTINGS:
        rows = conn.execute(
            "SELECT unnest(setconfig) FROM pg_db_role_setting "
            "WHERE setrole = %s::regrole AND setdatabase IN (0, (SELECT oid FROM pg_database "
            "WHERE datname = current_database()))", (persona,)).fetchall()
        _ROLE_SETTINGS[persona] = [tuple(r[0].split("=", 1)) for r in rows]
    conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(persona)))
    for name, value in _ROLE_SETTINGS[persona]:
        conn.execute(sql.SQL("SET {} = {}").format(sql.Identifier(name), sql.Literal(value)))


# Unit 8: shared, bounded pools for the gateway (None: one connection per call, as before).
_PG_POOL = None
_OS_CLIENTS: dict | None = None
_OS_OPTIONS: dict = {}


def use_pools(pg_size: int = 10, pg_wait: float = 2.0, os_size: int = 10,
              search_budget: str | None = "10s") -> None:
    """Unit 8: from now on, gateway connections (inside `acting_for`) come from shared pools.

    pg_size     Postgres connections the gateway keeps open (min = max: no surprises)
    pg_wait     seconds a call may wait for one before it's told the server is busy
    os_size     HTTP connections per OpenSearch client (one client per persona)
    search_budget  OpenSearch cancels a search that runs longer (cancel_after_time_interval)
    """
    global _PG_POOL, _OS_CLIENTS
    from psycopg_pool import ConnectionPool

    def reset(conn):                       # back to "the gateway, holding nothing"
        conn.execute("RESET ROLE")
        conn.execute("RESET ALL")

    # autocommit: SET ROLE and the settings hold for the whole checkout, and nothing a tool
    # does (a rollback) can undo them halfway through.
    _PG_POOL = ConnectionPool(kwargs=_pg_params(GATEWAY, autocommit=True),
                              min_size=pg_size, max_size=pg_size,
                              timeout=pg_wait, reset=reset, name="gateway", open=True)
    _PG_POOL.wait(timeout=10)
    atexit.register(_PG_POOL.close)
    _OS_CLIENTS = {}
    _OS_OPTIONS.update(maxsize=os_size, search_budget=search_budget)


def pool_stats() -> dict:
    """The Postgres pool's own counters (empty without a pool)."""
    return dict(_PG_POOL.get_stats()) if _PG_POOL is not None else {}


@contextlib.contextmanager
def _pooled(persona: str):
    with _PG_POOL.connection() as conn:
        _become(conn, persona)
        yield conn


def pg_connect(persona: str = "l1_analyst", **kwargs):
    """Open a Postgres connection to the CMDB *as* a persona. Use it as `with pg_connect(...)`.

    Row-level security, column privileges, statement_timeout and the connection limit all
    follow the persona, not the code -- the database enforces them.

    Inside `acting_for(persona)` (Unit 7) it logs in as the gateway, which holds no privileges
    of its own, and `SET ROLE`s to the persona. If anything resets the role, the connection is
    left with nothing. With `use_pools()` (Unit 8) the gateway's connection comes from a
    shared pool, and goes back to it reset.
    """
    import psycopg

    gateway = _gateway_for(persona)
    if gateway and _PG_POOL is not None:
        return _pooled(persona)
    conn = psycopg.connect(**_pg_params(GATEWAY if gateway else persona, **kwargs))
    if gateway:
        _become(conn, persona)
        if not conn.autocommit:
            conn.commit()                  # so a later rollback can't undo it
    return conn


def os_client(persona: str = "l1_analyst", **kwargs):
    """An OpenSearch client authenticated *as* a persona (index, field and document
    permissions are enforced by OpenSearch's security plugin).

    Inside `acting_for(persona)` (Unit 7) it logs in as the gateway and impersonates the
    persona on every request: OpenSearch applies the persona's roles, not the gateway's. With
    `use_pools()` (Unit 8) it's one shared client per persona, not a new one per call."""
    gateway = _gateway_for(persona)
    login = GATEWAY if gateway else persona
    if gateway:
        kwargs["headers"] = {**kwargs.get("headers", {}),
                             "opendistro_security_impersonate_as": persona}
        if _OS_CLIENTS is not None and not kwargs.keys() - {"headers"}:
            # Unit 8: one long-lived client per persona, with a bounded connection pool
            if persona not in _OS_CLIENTS:
                _OS_CLIENTS[persona] = _os_build(login, kwargs, maxsize=_OS_OPTIONS["maxsize"],
                                                 search_budget=_OS_OPTIONS["search_budget"])
            return _OS_CLIENTS[persona]
    return _os_build(login, kwargs)


def _os_build(login: str, kwargs: dict, maxsize: int | None = None,
              search_budget: str | None = None):
    import logging

    from opensearchpy import OpenSearch, Transport

    # opensearch-py logs every request (and every 403) as plain text. On an MCP server that's
    # stderr, the joinable log's channel, and errors reach the caller as exceptions anyway.
    logging.getLogger("opensearch").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)   # "pool is full"

    verify = os.environ.get("OS_VERIFY_CERTS", "0").lower() in ("1", "true", "yes")
    options = dict(
        hosts=[{"host": os.environ.get("OS_HOST", "127.0.0.1"),
                "port": int(os.environ.get("OS_PORT", "9200"))}],
        http_auth=(login, _password(login)),
        use_ssl=True,
        verify_certs=verify,
        ssl_show_warn=False,
        timeout=10,
    )
    if verify and os.environ.get("OS_CA_CERTS"):
        options["ca_certs"] = os.environ["OS_CA_CERTS"]
    if maxsize:
        options["pool_maxsize"] = maxsize
    if search_budget:
        class Budgeted(Transport):
            """Every search carries cancel_after_time_interval: the cluster stops the work,
            not just the client waiting for it."""
            def perform_request(self, method, url, params=None, body=None, *args, **kw):
                if url.endswith("/_search"):
                    params = {"cancel_after_time_interval": search_budget, **(params or {})}
                return super().perform_request(method, url, params, body, *args, **kw)
        options["transport_class"] = Budgeted
    options.update(kwargs)
    return OpenSearch(**options)


def backend_refusal(e: Exception) -> tuple[str, str] | None:
    """Unit 8: is this backend error backpressure (try later) or a timeout (ask for less)?
    Returns (kind, message for the model), or None for any other error.

    The model can act on these: "busy" means wait and retry the same call; "too_slow" means
    don't retry, make the question smaller."""
    name, text = type(e).__name__, str(e).lower()
    status = getattr(e, "status_code", None)
    if "point in time contexts" in text:     # every result cursor on the cluster is taken
        return ("busy", "the log cluster has too many result cursors open (each paged search "
                        "holds one for a while after it returns). Retry in 30 seconds, or "
                        "count or aggregate instead of listing.")
    if name == "PoolTimeout" or "too many connections" in text or status == 429 or (
            "rejected execution" in text):
        return ("busy", "the server is busy (every backend connection is in use). Retry the "
                        "same call in 2 seconds; don't send other calls meanwhile.")
    if name in ("QueryCanceled", "ConnectionTimeout") or "cancelled" in text or (
            "statement timeout" in text):
        return ("too_slow", "that query ran past its time budget and was stopped. Don't retry "
                            "it as is: narrow the window, add a filter, or count before you "
                            "fetch.")
    return None


def state_client(asynchronous: bool = False):
    """Unit 9: the servers' shared state (Valkey): counters and a cache every replica sees.
    Strings in, strings out. Never a place for records: it keeps nothing across a restart.
    `asynchronous=True` gives the asyncio client, for use inside a server's event loop."""
    import redis
    import redis.asyncio

    kind = redis.asyncio.Redis if asynchronous else redis.Redis
    return kind(host=os.environ.get("VALKEY_HOST", "127.0.0.1"),
                       port=int(os.environ.get("VALKEY_PORT", "6380")),
                       password=os.environ.get("VALKEY_PASSWORD"), decode_responses=True,
                       socket_timeout=2, socket_connect_timeout=2, health_check_interval=30)


def trace_meta(session_id: str, trace_id: str, step: int) -> dict:
    """The joinable tuple for a request's `_meta` (Unit 1): a W3C `traceparent` carrying the
    trace id, plus this course's own `session_id` and `step` keys."""
    import secrets

    return {"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01",
            "session_id": session_id, "step": step}



_LOG_NOTES: contextvars.ContextVar[dict] = contextvars.ContextVar("joinable_log_notes")
_LOG_RECORD: contextvars.ContextVar[dict] = contextvars.ContextVar("joinable_log_record")

# Unit 11: more places each finished record goes, besides stderr: callables taking the record
# (a dict). A sink must be quick and must not raise; telemetry.py's queues and returns.
RECORD_SINKS: list = []


def current_record() -> dict | None:
    """The joinable record being built for the current request: session_id, trace_id, step,
    method, name (Unit 11's audit stamps its rows with them). None outside a logged request."""
    return _LOG_RECORD.get(None)


def current_notes() -> dict | None:
    """The fields logged so far for the current request (Unit 11's audit reads the caller and
    the policy verdict from here). None outside a logged request."""
    return _LOG_NOTES.get(None)


def log_note(**fields) -> None:
    """Add fields to the current request's joinable-log record (Unit 4): a tool reports what
    the call cost the backend (indexes, shards, took_ms, hits) without returning it to the
    model. Outside a logged request it does nothing."""
    notes = _LOG_NOTES.get(None)
    if notes is not None:
        notes.update(fields)


def joinable_log(server: str):
    """Unit 1's joinable-log middleware, for any server: one JSONL record per request on stderr,
    stamped with the caller's session_id / trace_id / step from `_meta`. Unit 2 moved it here so
    every server logs the same way: `mcp.middleware.append(joinable_log("tickets"))`. Unit 4
    added `log_note`, which merges a tool's own fields into the record."""
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
        if caller:                       # Unit 11: the caller's span is this request's parent
            record["parent_span_id"] = parts[2]
        notes: dict = {}
        token, record_token = _LOG_NOTES.set(notes), _LOG_RECORD.set(record)
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
            _LOG_NOTES.reset(token)
            _LOG_RECORD.reset(record_token)
            record.update({k: v for k, v in notes.items() if k not in record})
            print(json.dumps(record), file=sys.stderr, flush=True)
            for sink in RECORD_SINKS:
                try:
                    sink(record)
                except Exception:  # noqa: BLE001  telemetry never breaks a request
                    pass

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
