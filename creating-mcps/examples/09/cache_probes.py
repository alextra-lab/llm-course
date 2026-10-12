"""
Unit 9: what a cache in the wrong place leaks.

Runs the same four calls against the scaled servers twice: with the result cache where it
belongs (inside the policy, keyed by persona) and with UnsafeCache (in front of the policy,
keyed by the arguments only). Each probe says what should happen, and what did.

    1. marc (L3) looks up db-prod-01, a restricted host: allowed.
    2. priya (L1) looks up the same host. Row-level security hides it from her, so the answer
       should be "no asset named". An unsafe cache hands her marc's row.
    3. marc asks for the owner's contact details and gives ticket INC-0042: allowed.
    4. kim (also L3) asks for the same contact and declines to give a ticket. The policy should
       refuse. An unsafe cache answers before the policy runs: no ticket, no verdict logged.

    python examples/09/cache_probes.py               # both, as a table
    python examples/09/cache_probes.py --mode shared # one mode only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "07"), str(HERE.parent)]
import issuer as idp  # noqa: E402
import refusals  # noqa: E402
from common_mcp import load_env  # noqa: E402

PROBES = [
    # (who, groups, server, tool, args, answer, expected when the cache is safe)
    ("marc", None, "cmdb", "get_asset", {"hostname": "db-prod-01"}, "accept", "allowed"),
    ("priya", None, "cmdb", "get_asset", {"hostname": "db-prod-01"}, "accept", "refused"),
    ("marc", None, "cmdb", "get_contact", {"hostname": "jump-prod-02"}, "accept",
     "asked:accept"),
    ("kim", ["soc-l3"], "cmdb", "get_contact", {"hostname": "jump-prod-02"}, "decline",
     "asked:decline"),
]


async def call(url: str, who: str, groups, tool: str, args: dict, answer: str):
    from mcp.types import ElicitResult

    asked = []

    async def on_elicit(_ctx, params):
        asked.append(params.message)
        if answer == "accept":
            return ElicitResult(action="accept", content={"ticket": "INC-0042"})
        return ElicitResult(action="decline")

    token = idp.issue(who, url, groups=groups)
    async with refusals.connect(url, token, on_elicit) as client:
        r = await client.call_tool(tool, args)
    says = json.dumps(r.structured_content) if r.structured_content else " ".join(
        c.text for c in r.content if getattr(c, "text", None))
    return (f"asked:{answer}" if asked else ("refused" if r.is_error else "allowed")), says


def verdicts(log: Path, who: str) -> int:
    """Policy verdicts the server logged for `who` (the audit trail)."""
    n = 0
    for line in log.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("caller") == who and rec.get("verdict"):
            n += 1
    return n


async def run(mode: str, log_dir: Path | None = None) -> list[dict]:
    load_env()
    env = {"MCP_CACHE": mode, "MCP_WORKERS": "1"}
    rows = []
    async with refusals.servers(log_dir, script=HERE / "scaled_server.py", env=env) as urls:
        for who, groups, server, tool, args, answer, expected in PROBES:
            got, says = await call(urls[server], who, groups, tool, args, answer)
            rows.append({"cache": mode, "who": who, "tool": tool, "args": args,
                         "expected": expected, "got": got, "ok": got == expected,
                         "says": says[:120]})
        log = Path(urls["logs"][server])
    await asyncio.sleep(0.2)
    rows[-1]["verdicts_logged"] = verdicts(log, "kim")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=("shared", "local", "unsafe"))
    args = parser.parse_args()
    for mode in [args.mode] if args.mode else ["shared", "unsafe"]:
        rows = asyncio.run(run(mode))
        print(f"\nMCP_CACHE={mode}")
        for r in rows:
            mark = "ok  " if r["ok"] else "LEAK"
            extra = f"  (policy verdicts logged for kim: {r['verdicts_logged']})" if (
                "verdicts_logged" in r) else ""
            print(f"  {mark} {r['who']:<6} {r['tool']:<12} expected {r['expected']:<14} "
                  f"got {r['got']:<14}{extra}")
        bad = [r for r in rows if not r["ok"]]
        if mode != "unsafe" and bad:
            raise SystemExit(f"the {mode} cache leaked: {bad}")


if __name__ == "__main__":
    main()
