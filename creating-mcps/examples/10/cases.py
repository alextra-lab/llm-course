"""
Unit 10: the case registry. One entry per OWASP MCP Top 10 (2025) category, each naming the
defence in guarded_host.py (or an earlier unit) that answers it.

The negative cases, which exercise each category against the estate, are PLACEHOLDERS: the
course team writes them. A placeholder is reported as "todo" and never fails a run, so the
unit, its lab and CI work before the cases exist.

To add a case: replace its `run = None` with an async function `run(defences) -> dict` that
returns {"blocked": bool, "detail": str}, using the servers in this folder. See the
"Writing a case" section of lessons/10-attacking-and-defending-mcp.md.

    python examples/10/cases.py           # the registry, and which cases are still todo
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class Case:
    owasp: str
    category: str
    defence: str                 # the guarded_host defence (or unit) expected to answer it
    run: Callable[[set[str]], Awaitable[dict]] | None = None   # None: placeholder


CASES = [
    Case("MCP01", "Token Mismanagement & Secret Exposure", "redaction, hardened_code"),
    Case("MCP02", "Privilege Escalation via Scope Creep", "least_privilege"),
    Case("MCP03", "Tool Poisoning", "vetting"),
    Case("MCP04", "Software Supply Chain Attacks & Dependency Tampering", "vetting (pinning)"),
    Case("MCP05", "Command Injection & Execution", "hardened_code, Unit 5 guards"),
    Case("MCP06", "Intent Flow Subversion", "approvals, fencing"),
    Case("MCP07", "Insufficient Authentication & Authorization", "identity (Unit 7)"),
    Case("MCP08", "Lack of Audit and Telemetry", "audit records (Unit 11)"),
    Case("MCP09", "Shadow MCP Servers", "allowlist"),
    Case("MCP10", "Context Injection & Over-Sharing", "egress, least_privilege"),
]


async def run(defences: set[str]) -> list[dict]:
    rows = []
    for case in CASES:
        row = {"owasp": case.owasp, "category": case.category, "defence": case.defence}
        if case.run is None:
            rows.append({**row, "result": "todo", "detail": "placeholder: not written yet"})
            continue
        out = await case.run(defences)
        rows.append({**row, "result": "blocked" if out["blocked"] else "got through",
                     "detail": out.get("detail", "")})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Unit 10's case registry.")
    parser.add_argument("--defences", default="", help="comma-separated guarded_host defences")
    args = parser.parse_args()
    on = {d for d in args.defences.split(",") if d}
    for r in asyncio.run(run(on)):
        print(json.dumps(r))


if __name__ == "__main__":
    main()
