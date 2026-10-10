"""
Shared helpers for the Creating MCPs course.

Like Foundations' examples/common.py, this holds the one bit of boilerplate every unit would
otherwise repeat -- here, *connecting to the estate as someone*. Every connection names a
persona, because in this course there is no such thing as an anonymous query: who is asking
decides what comes back (Unit 7 makes that the whole lesson).

Later units add to this file (the joinable trace, the per-caller limiter, the policy client).
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
    from opensearchpy import OpenSearch

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
