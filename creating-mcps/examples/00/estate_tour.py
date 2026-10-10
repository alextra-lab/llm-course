"""
Unit 0 reference solution: the same question, asked by three people.

Connects to the estate as each persona and prints what Postgres and OpenSearch let them see.
None of the differences come from this code -- row-level security, column privileges, index
permissions, field-level security and field masking are all enforced by the backends.

Run from creating-mcps/ after `python env/env.py up && python env/env.py seed`:

    python examples/00/estate_tour.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import os_client, pg_connect  # noqa: E402

PERSONAS = ["l1_analyst", "l3_responder", "auditor"]


def cmdb_view(persona):
    """(assets visible, can read people.email?) as this persona."""
    with pg_connect(persona) as conn:
        assets = conn.execute("SELECT count(*) FROM cmdb.assets").fetchone()[0]
        try:
            conn.execute("SELECT email FROM cmdb.people LIMIT 1")
            email = "readable"
        except Exception:
            conn.rollback()
            email = "denied"
    return assets, email


def siem_view(persona):
    """(log docs visible, EDR index readable?, what user.email looks like) as this persona."""
    client = os_client(persona)
    try:
        logs = client.count(index="logs-*")["count"]
    except Exception:
        return 0, "denied", "-"
    try:
        client.count(index="edr-events-*")
        edr = "readable"
    except Exception:
        edr = "denied"
    hit = client.search(index="logs-web-*", body={
        "size": 1, "query": {"exists": {"field": "user.name"}}})["hits"]["hits"]
    email = hit[0]["_source"].get("user", {}).get("email", "(field hidden)") if hit else "-"
    return logs, edr, email[:24]


def main():
    print(f"{'persona':<14} {'assets':>6}  {'people.email':<12} {'log docs':>8}  "
          f"{'EDR':<8} user.email")
    for persona in PERSONAS:
        try:
            assets, pg_email = cmdb_view(persona)
        except Exception as e:
            assets, pg_email = f"({type(e).__name__})", "-"
        try:
            logs, edr, os_email = siem_view(persona)
        except Exception as e:
            logs, edr, os_email = f"({type(e).__name__})", "-", "-"
        print(f"{persona:<14} {assets!s:>6}  {pg_email:<12} {logs!s:>8}  {edr:<8} {os_email}")


if __name__ == "__main__":
    main()
