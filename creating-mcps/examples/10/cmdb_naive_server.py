"""
Unit 10: the CMDB server as it often looks the week before a security review.

Unit 5's task-shaped tools, plus three that real servers grow:

    get_contact            an owner's e-mail and phone, with no ticket (Unit 7 asks for one)
    find_assets_by_owner   hosts by owner name, with the name pasted into the SQL
    server_status          a health check that returns the connection string it uses,
                           password included

MCP_HARDENED=1 is the same server after the review: the owner name is a query parameter,
server_status says whether the database answers and nothing about how it logs in. Everything
else is unchanged, so an attack that still works with it on was stopped somewhere else.

Like Unit 5, it acts for one persona, MCP_PERSONA, which is whatever its host says.

    MCP_PERSONA=l1_analyst MCP_HARDENED=1 python examples/10/cmdb_naive_server.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent))  # creating-mcps/examples/
from common_mcp import _pg_params, joinable_log, load_env, persona, pg_connect  # noqa: E402

load_env()
HARDENED = os.environ.get("MCP_HARDENED", "0").lower() in ("1", "true", "yes")

_spec = importlib.util.spec_from_file_location("unit5_cmdb_tasks",
                                               HERE.parent / "05" / "cmdb_tasks_server.py")
tasks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tasks)

mcp = MCPServer("cmdb", version="0.10.0" + ("-hardened" if HARDENED else ""),
                instructions=tasks.mcp.instructions)
mcp.middleware.append(joinable_log("cmdb_naive"))
for fn in (tasks.unit3.get_asset, tasks.unit3.find_assets, tasks.service_dependencies,
           tasks.hosts_with_vulnerability, tasks.count_assets):
    mcp.tool()(fn)


@mcp.tool()
def get_contact(hostname: str) -> dict[str, Any]:
    """How to reach the person who owns a host: their e-mail and phone, and their team's
    on-call channel."""
    with pg_connect(persona()) as conn:
        row = conn.execute(
            "SELECT a.hostname, p.full_name, p.email, p.phone, t.name, t.oncall_slack "
            "FROM cmdb.assets a JOIN cmdb.people p ON p.id = a.owner_id "
            "JOIN cmdb.teams t ON t.id = p.team_id WHERE a.hostname = %s",
            (hostname.strip().lower(),)).fetchone()
    if row is None:
        raise ToolError(f"no asset named {hostname!r}.")
    return dict(zip(("hostname", "owner", "email", "phone", "team", "oncall"), row))


@mcp.tool()
def find_assets_by_owner(name: str) -> dict[str, Any]:
    """Hosts owned by a person, by (part of) their full name, e.g. "Kim"."""
    head = ("SELECT a.hostname, a.classification, p.full_name FROM cmdb.assets a "
            "JOIN cmdb.people p ON p.id = a.owner_id WHERE p.full_name ILIKE ")
    with pg_connect(persona()) as conn:
        if HARDENED:
            rows = conn.execute(head + "%s ORDER BY 1 LIMIT 50", (f"%{name}%",)).fetchall()
        else:   # the bug: whatever the model sends becomes part of the statement
            rows = conn.execute(head + f"'%{name}%' ORDER BY 1 LIMIT 50").fetchall()
    return {"name": name, "hosts": [dict(zip(("hostname", "classification", "owner"), r))
                                    for r in rows]}


@mcp.tool()
def server_status() -> dict[str, Any]:
    """Is the CMDB server healthy? Database reachability and version."""
    params = _pg_params(persona())
    with pg_connect(persona()) as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
    status = {"ok": True, "server": mcp.name, "postgres": version, "acting_as": persona()}
    if not HARDENED:   # the bug: "useful for debugging"
        status["database"] = (f"postgresql://{params['user']}:{params['password']}@"
                              f"{params['host']}:{params['port']}/{params['dbname']}")
    return status


if __name__ == "__main__":
    mcp.run()
