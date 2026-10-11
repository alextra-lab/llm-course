"""
Unit 5: the four positions on the permission dial, as server tables for the Unit 2 host.

    permissive    run_sql on the CMDB  +  OpenSearch's own MCP server (raw Query DSL, any API)
    guarded       query_sql (parse-checked SELECT)  +  ppl_query (checked, bounded PPL)
    restrictive   task-shaped CMDB tools  +  Unit 4's SIEM tools
    hybrid        restrictive, plus an escape hatch that runs a guarded query only when the
                  person using the host approves it

Every server acts as the same persona, so the backends enforce the same rules underneath all
four; what changes is how much of the persona's power each one hands to the model.

OpenSearch's MCP server (opensearch-mcp-server-py) is a third-party package with its own
dependencies, so it lives in its own virtual environment:

    python3.13 -m venv .venv-osmcp
    .venv-osmcp/bin/pip install opensearch-mcp-server-py==0.12.0

Set OPENSEARCH_MCP_SERVER to its executable if you installed it somewhere else. Without it,
the permissive variant has no SIEM half and says so.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from mcp import StdioServerParameters

HERE = Path(__file__).resolve().parent
COURSE_DIR = HERE.parents[1]
sys.path.append(str(HERE.parent))
from common_mcp import PERSONAS, load_env  # noqa: E402

OSMCP_VERSION = "0.12.0"
NAMES = ("permissive", "guarded", "restrictive", "hybrid")


def osmcp_executable() -> str | None:
    """Where opensearch-mcp-server-py is installed, if anywhere."""
    for candidate in (os.environ.get("OPENSEARCH_MCP_SERVER"),
                      COURSE_DIR / ".venv-osmcp" / "bin" / "opensearch-mcp-server-py",
                      shutil.which("opensearch-mcp-server-py")):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def osmcp(persona: str) -> StdioServerParameters | None:
    """OpenSearch's own MCP server, as shipped, logged in as the persona."""
    exe = osmcp_executable()
    if exe is None:
        return None
    load_env()
    verify = os.environ.get("OS_VERIFY_CERTS", "0").lower() in ("1", "true", "yes")
    return StdioServerParameters(command=exe, args=[], env={
        "OPENSEARCH_URL": f"https://{os.environ.get('OS_HOST', '127.0.0.1')}:"
                          f"{os.environ.get('OS_PORT', '9200')}",
        "OPENSEARCH_USERNAME": persona,
        "OPENSEARCH_PASSWORD": os.environ[PERSONAS[persona]],
        "OPENSEARCH_SSL_VERIFY": "true" if verify else "false",
    })


def servers(variant: str, persona: str) -> dict:
    """The host's server table for one variant. Raises if a part isn't installed."""
    restrictive = {"cmdb": HERE / "cmdb_tasks_server.py",
                   "siem": HERE.parent / "04" / "siem_server.py"}
    if variant == "permissive":
        siem = osmcp(persona)
        if siem is None:
            raise RuntimeError("the permissive variant needs opensearch-mcp-server-py "
                               f"{OSMCP_VERSION}; see examples/05/variants.py to install it.")
        return {"cmdb": HERE / "cmdb_raw_server.py", "siem": siem}
    if variant == "guarded":
        return {"cmdb": HERE / "cmdb_guarded_server.py", "siem": HERE / "siem_guarded_server.py"}
    if variant == "restrictive":
        return restrictive
    if variant == "hybrid":
        return {**restrictive, "hatch": HERE / "hatch_server.py"}
    raise ValueError(f"unknown variant {variant!r}; one of {NAMES}")


def available() -> list[str]:
    return [v for v in NAMES if v != "permissive" or osmcp_executable()]
