"""
Unit 6: an audit of error messages -- every refusal is an instruction to the model.

It sends the nine tools a set of mistakes a model really makes (a hostname with its domain,
a service by its description, a window too wide, a field that doesn't exist, a cursor it made
up, ...) and grades what comes back. A useful refusal does three things:

    problem   says what was wrong: names the argument or quotes the bad value
    allowed   says what is allowed: a limit, a format, an example, or the valid choices
    next      says what to do next: an imperative (use, pass, narrow, try, ...)

and two things it must not do:

    dangling  point at a tool, parameter or field the caller doesn't have
    leaks     expose internals: a stack trace, driver names, SQL, a library's help URL

Run it on the shipped messages, on the generic ones (styled_server ERROR_STYLE=generic), and
on the rewritten style, where some mistakes are now stopped by the schema before the tool's
own code runs, with the SDK's message instead of ours.

    python examples/06/errors.py                       # original style, instructive errors
    python examples/06/errors.py --style rewritten
    python examples/06/errors.py --errors generic
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "05"), str(HERE.parent)]
import styles  # noqa: E402
from common_mcp import load_env  # noqa: E402
from grid import _approve, unit2  # noqa: E402  (Unit 5)

MISTAKES = [
    ("cmdb", "get_asset", "hostname with its domain",
     {"hostname": "jump-prod-02.corp.example"}),
    ("cmdb", "find_assets", "a service by its description", {"service": "customer database"}),
    ("cmdb", "find_assets", "too many at once", {"limit": 500}),
    ("cmdb", "service_dependencies", "a service name misspelt", {"service": "customerdb"}),
    ("cmdb", "hosts_with_vulnerability", "a CVE id with spaces", {"cve_id": "CVE 2099 1068"}),
    ("cmdb", "count_assets", "a service by its description", {"service": "customer database"}),
    ("siem", "list_incidents", "too many at once", {"limit": 500}),
    ("siem", "get_incident", "a bare incident number", {"incident_id": "42"}),
    ("siem", "event_histogram", "before retention", {"dataset": "auth", "start": "now-60d"}),
    ("siem", "event_histogram", "an interval that doesn't exist",
     {"dataset": "auth", "start": "now-1d", "interval": "1s"}),
    ("siem", "event_histogram", "a dataset the role can't read",
     {"dataset": "edr", "start": "now-1d"}),
    ("siem", "top_values", "a field that doesn't exist",
     {"dataset": "auth", "field": "ip", "start": "now-1d"}),
    ("siem", "search_events", "a window too wide for a search",
     {"dataset": "auth", "start": "now-3d"}),
    ("siem", "search_events", "a filter on full text",
     {"dataset": "auth", "start": "now-1d", "filters": {"message": "failed"}}),
    ("siem", "search_events", "a masked field",
     {"dataset": "web", "start": "now-1d", "filters": {"user.email": "a@b.c"}}),
    ("siem", "search_events", "a window that ends before it starts",
     {"dataset": "auth", "start": "2026-10-09T05:00:00Z", "end": "2026-10-09T04:00:00Z"}),
    ("siem", "search_events", "a cursor it made up",
     {"dataset": "auth", "start": "now-1d", "cursor": "page2"}),
]

PROBLEM_WORDS = re.compile(r"\b(unknown|no |isn't|aren't|not |too |must|can't|invalid|"
                           r"should|at most|before|after)", re.I)
ALLOWED = re.compile(r"e\.g\.|\blike\b|\blook like\b|\bat most\b|\bbetween\b|\bone of\b|"
                     r"\bmax\b|\ballowed\b|\byou can\b|\byours\b|:\s*\S+,\s*\S+|\d", re.I)
NEXT = re.compile(r"\b(use|pass|narrow|try|send|call|aggregate|filter|drop|follow|list|put|"
                  r"qualify|split|start|ask|repeat|swap|omit)\b", re.I)
LEAKS = re.compile(r"Traceback|psycopg|opensearchpy|File \"|line \d+, in|\bSELECT\b.*\bFROM\b|"
                   r"https?://errors\.pydantic\.dev|\[type=", re.S)
NAMEISH = re.compile(r"(?<![\w.-])([a-z]+(?:_[a-z]+)+)(?![\w.-])")


def known_names(tools: dict) -> set[str]:
    """Everything a message may legitimately name: tools, parameters, result keys."""
    names = set()

    def walk(schema):
        if isinstance(schema, dict):
            names.update(schema.get("properties", {}))
            for v in schema.values():
                walk(v)
        elif isinstance(schema, list):
            for v in schema:
                walk(v)

    for _server, tool in tools.values():
        names.add(tool.name)
        walk(tool.input_schema)
        walk(tool.output_schema)
    return names


def grade(text: str, args: dict, known: set[str]) -> dict:
    message = re.sub(r"^error: (Error executing tool \w+: )?", "", text)
    values = [str(v) for v in args.values() if not isinstance(v, (dict, list))]
    values += [str(k) for v in args.values() if isinstance(v, dict) for k in v]
    problem = (any(len(v) >= 2 and v in message for v in values)
               or any(re.search(rf"\b{re.escape(k)}\b", message) for k in args)
               or bool(PROBLEM_WORDS.search(message)))
    dangling = sorted({n for n in NAMEISH.findall(message) if n not in known
                       and not re.search(rf"[\w.]{re.escape(n)}|{re.escape(n)}\.", message)})
    out = {"problem": problem, "allowed": bool(ALLOWED.search(message)),
           "next": bool(NEXT.search(message)), "dangling": ", ".join(dangling),
           "leaks": bool(LEAKS.search(message))}
    out["score"] = (out["problem"] + out["allowed"] + out["next"]
                    - bool(dangling) - out["leaks"])
    out["message"] = message.strip()
    return out


async def audit(style: str, persona: str, errors: str = "instructive") -> list[dict]:
    rows = []
    async with unit2.Host(styles.server_table(style, persona, errors), _approve) as host:
        known = known_names(host.tools)
        for server, tool, mistake, args in MISTAKES:
            text, ev = await host.call(f"{server}__{tool}", args, "audit", "0" * 32, 0)
            if ev.outcome == "ok":
                rows.append({"tool": tool, "mistake": mistake, "score": None,
                             "message": "(accepted: " + text[:80] + ")"})
                continue
            rows.append({"tool": tool, "mistake": mistake, **grade(text, args, known)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade the tools' error messages.")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--style", choices=styles.STYLES, default="original")
    parser.add_argument("--errors", choices=("instructive", "generic"), default="instructive")
    parser.add_argument("--full", action="store_true", help="print whole messages")
    args = parser.parse_args()
    load_env()
    rows = asyncio.run(audit(args.style, args.persona, args.errors))
    print(f"{'tool':<24} {'mistake':<34} {'score':>5}  P A N  flags")
    for r in rows:
        if r["score"] is None:
            print(f"{r['tool']:<24} {r['mistake']:<34} {'-':>5}  {r['message']}")
            continue
        flags = " ".join(f for f in (f"dangling: {r['dangling']}" if r["dangling"] else "",
                                     "leaks" if r["leaks"] else "") if f)
        marks = " ".join("✓" if r[k] else "·" for k in ("problem", "allowed", "next"))
        print(f"{r['tool']:<24} {r['mistake']:<34} {r['score']:>5}  {marks}  {flags}")
        msg = r["message"] if args.full else r["message"].splitlines()[0][:110]
        print(f"{'':<24} {msg}")
    graded = [r for r in rows if r["score"] is not None]
    print(f"\nmean score {sum(r['score'] for r in graded) / len(graded):.2f} of 3 over "
          f"{len(graded)} refusals; {sum(bool(r['dangling']) for r in graded)} dangling, "
          f"{sum(r['leaks'] for r in graded)} leaking")


if __name__ == "__main__":
    main()
