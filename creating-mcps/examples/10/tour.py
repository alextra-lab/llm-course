"""
Unit 10: a tour of the guarded host on ordinary requests. It starts the unit's servers with
the defences you choose, prints the tool table the model would see, makes a few everyday
calls, and prints the host's audit records for them.

    python examples/10/tour.py
    python examples/10/tour.py --defences allowlist,vetting,redaction,fencing
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import StdioServerParameters

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from guarded_host import DEFENCES, GuardedHost, Server, person_who_expects  # noqa: E402


def servers(persona: str, defences: set[str], work: Path) -> dict[str, Server]:
    def params(path: Path, **env: str) -> StdioServerParameters:
        return StdioServerParameters(command=sys.executable, args=[str(path)],
                                     env={"MCP_PERSONA": persona, **env})
    return {
        "cmdb": Server(params(HERE / "cmdb_naive_server.py",
                              MCP_HARDENED="1" if "hardened_code" in defences else "0")),
        "siem": Server(params(HERE.parent / "04" / "siem_server.py")),
        "actions": Server(params(HERE / "actions_server.py", MCP_OUTBOX=str(work / "outbox"),
                                 MCP_APPROVALS="1" if "approvals" in defences else "0")),
        "intel": Server(params(HERE / "third_party_server.py",
                               MCP_THIRD_PARTY_MODE="honest",
                               MCP_EXFIL_LOG=str(work / "received")),
                        trusted=False, approved={"lookup_ip"}),
    }


ASK = "Is 203.0.113.77 known bad, and who owns jump-prod-02? Mark INC-0042 in progress."
CALLS = [("cmdb", "get_asset", {"hostname": "jump-prod-02"}),
         ("siem", "get_incident", {"incident_id": "INC-0042"}),
         ("intel", "lookup_ip", {"ip": "203.0.113.77"}),
         ("actions", "set_incident_status", {"incident_id": "INC-0042", "status": "in_progress",
                                             "note": "investigating"})]


async def tour(defences: set[str], persona: str = "l3_responder") -> dict:
    with tempfile.TemporaryDirectory(prefix="unit10-") as tmp:
        person = person_who_expects("INC-0042 to in_progress")    # what they asked for
        async with GuardedHost(servers(persona, defences, Path(tmp)), defences,
                               ask_person=person) as host:
            host.request = ASK
            results = []
            for server, tool, args in CALLS:
                r = await host.call(host.key_for(server, tool), args)
                results.append({"server": server, "tool": tool, "outcome": r["outcome"],
                                "reads": r["text"][:160]})
            return {"tools": [t["name"] for t in host.describe()], "calls": results,
                    "audit": host.audit.records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--defences", default=",".join(d for d in DEFENCES if d != "identity"))
    args = parser.parse_args()
    out = asyncio.run(tour({d for d in args.defences.split(",") if d}))
    print("tools:", ", ".join(out["tools"]))
    for row in out["calls"]:
        print(json.dumps(row))
    print(f"audit: {len(out['audit'])} records")
    for rec in out["audit"]:
        print(" ", json.dumps(rec))


if __name__ == "__main__":
    main()
