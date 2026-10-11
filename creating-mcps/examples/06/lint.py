"""
Unit 6: a linter for tool definitions -- what the model will read, checked before it reads it.

It connects through the Unit 2 host, takes every tool exactly as tools/list returns it, and
applies the unit's rules. Each finding has a rule id, a level and a one-line fix:

    N1 error  the name isn't snake_case, 3-64 characters
    N2 warn   the name is a bare generic verb (search, query, run, ...): ambiguous next to
              other servers' tools
    D1 error  no description, or one too short to say what the tool answers (< 40 chars)
    D2 warn   a description over 700 characters: paid on every call
    D3 warn   the description neither says when to use it nor names another tool to use
              instead
    P1 warn   a parameter with no description that the tool description doesn't mention
    P2 warn   a numeric parameter whose range is stated in prose but not in the schema
    P3 warn   a string parameter whose fixed choices are listed in prose but not as an enum
    O1 warn   no output schema: the result's shape is undocumented and unvalidated
    A1 warn   no annotations: a host can't tell a read-only tool from a destructive one
    A2 error  annotated read-only, but the name says it changes something

The rules are heuristics. A finding is a question to answer, not a verdict.

    python examples/06/lint.py                          # the original style
    python examples/06/lint.py --style terse --style rewritten
    python examples/06/lint.py --variant guarded        # one of Unit 5's variants
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
import variants  # noqa: E402
from common_mcp import load_env  # noqa: E402
from grid import _approve, unit2  # noqa: E402  (Unit 5)

GENERIC = {"search", "query", "run", "get", "list", "execute", "call", "fetch", "do", "find"}
WRITES = re.compile(r"(^|_)(create|update|delete|remove|close|set|run|exec|execute|write|"
                    r"kill|isolate|restart|drop)(_|$)")
RANGE = re.compile(r"\(\s*\d+\s*[-–]\s*\d+\s*\)|\bat most\b|\bbetween \d+ and \d+\b|"
                   r"\b\d+ to \d+\b")
USE = re.compile(r"\buse (it|this|them)?\b|\binstead\b|\bfirst\b|\bthen\b|\bonly when\b|"
                 r"\blast resort\b", re.I)


def _props(schema: dict) -> dict:
    return (schema or {}).get("properties", {})


def _types(spec: dict) -> set[str]:
    """The JSON types a property accepts, looking through anyOf (Optional)."""
    found = {spec.get("type")} if spec.get("type") else set()
    for alt in spec.get("anyOf", []):
        found |= _types(alt)
    return found - {"null"}


def _has(spec: dict, key: str) -> bool:
    return key in spec or any(_has(alt, key) for alt in spec.get("anyOf", []))


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", text) is not None


def lint_tool(tool, others: set[str]) -> list[dict]:
    """Findings for one tool. `others` are the other tools' names on the same table."""
    name, desc = tool.name, (tool.description or "").strip()
    out = []

    def add(rule, level, message):
        out.append({"tool": name, "rule": rule, "level": level, "message": message})

    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", name):
        add("N1", "error", "use a snake_case name of 3-64 characters")
    if name in GENERIC:
        add("N2", "warn", f"'{name}' alone is ambiguous next to other servers' tools; say "
                          "what it acts on (search_events, find_assets)")
    if len(desc) < 40:
        add("D1", "error", "say what the tool answers, in a sentence: the model has little "
                           "more than the name")
    elif len(desc) > 700:
        add("D2", "warn", f"{len(desc)} characters, paid on every call: move shared "
                          "conventions to one place and cut repetition")
    if desc and not USE.search(desc) and not any(_mentions(desc, o) for o in others):
        add("D3", "warn", "say when to use it, or which tool to use instead")
    for pname, spec in _props(tool.input_schema).items():
        described = _has(spec, "description")
        if not described and not _mentions(desc, pname) and not _has(spec, "enum"):
            add("P1", "warn", f"describe `{pname}` (format, an example, or what it means)")
        prose = desc + " " + str(spec.get("description", ""))
        if _types(spec) & {"integer", "number"} and not (_has(spec, "maximum")
                                                          or _has(spec, "minimum")):
            near = re.search(rf"{re.escape(pname)}\W[^.]{{0,40}}", desc)
            if RANGE.search(spec.get("description", "")) or (near and RANGE.search(near[0])):
                add("P2", "warn", f"`{pname}`'s range is in the prose only: put it in the "
                                  "schema (minimum/maximum) so the model sees it as a rule")
        if "string" in _types(spec) and not _has(spec, "enum"):
            if re.search(rf"{re.escape(pname)}\W[^.]{{0,30}}\bone of\b", prose):
                add("P3", "warn", f"`{pname}` has fixed choices listed in prose: make them "
                                  "an enum")
    if not tool.output_schema:
        add("O1", "warn", "declare the result's shape (a TypedDict or model return type)")
    ann = tool.annotations
    if ann is None:
        add("A1", "warn", "add annotations (readOnlyHint, destructiveHint): hints a host "
                          "can show or use to ask before acting, never enforcement")
    elif ann.read_only_hint and WRITES.search(name):
        add("A2", "error", "annotated read-only, but the name says it changes something")
    return out


async def lint(servers: dict) -> list[dict]:
    async with unit2.Host(servers, _approve) as host:
        names = {t.name for _s, t in host.tools.values()}
        return [f for _s, t in host.tools.values()
                for f in lint_tool(t, names - {t.name})]


def summary(findings: list[dict]) -> dict:
    out = {"errors": sum(f["level"] == "error" for f in findings),
           "warnings": sum(f["level"] == "warn" for f in findings)}
    for f in findings:
        out[f["rule"]] = out.get(f["rule"], 0) + 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Lint tool definitions.")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--style", choices=styles.STYLES, action="append")
    parser.add_argument("--variant", choices=variants.NAMES, action="append")
    parser.add_argument("--quiet", action="store_true", help="only the summaries")
    args = parser.parse_args()
    load_env()
    tables = {f"variant {v}": variants.servers(v, args.persona) for v in args.variant or []}
    for s in args.style or ([] if args.variant else ["original"]):
        tables[f"style {s}"] = styles.server_table(s, args.persona)
    for label, servers in tables.items():
        findings = asyncio.run(lint(servers))
        print(f"== {label}: {summary(findings)}")
        if not args.quiet:
            for f in findings:
                print(f"  {f['level']:<5} {f['rule']} {f['tool']:<26} {f['message']}")


if __name__ == "__main__":
    main()
