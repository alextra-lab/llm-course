"""
Unit 6: what a tool list costs, tool by tool, and where the bytes go.

For each style of the same nine tools (styles.py), it lists every tool as the Unit 2 host
sends it to the model (an OpenAI-style function: name, description, input schema) and
splits the bytes into:

    description   the prose
    schema        the input schema, of which `titles` is the "title" the SDK generates for
                  every parameter from its name ("Dataset" for dataset): pure repetition
    output        the output schema. It's in tools/list, but this host doesn't send it to
                  the model: the function-calling format has nowhere to put it

Tokens are estimated as bytes / 4, as in Units 3-5. Every one of them is paid on every model
call of every conversation.

    python examples/06/toolcost.py                     # all three styles, as l1_analyst
    python examples/06/toolcost.py --style rewritten --per-tool
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "05"), str(HERE.parent)]
import styles  # noqa: E402
from common_mcp import load_env  # noqa: E402
from grid import _approve, unit2  # noqa: E402  (Unit 5)


def _size(obj) -> int:
    return len(json.dumps(obj).encode()) if obj else 0


def _titles(schema) -> int:
    """Bytes spent on auto-generated per-property "title" entries, at any depth."""
    if isinstance(schema, dict):
        own = sum(len(f'"title": {json.dumps(p["title"])}, '.encode())
                  for p in schema.get("properties", {}).values()
                  if isinstance(p, dict) and "title" in p)
        return own + sum(_titles(v) for v in schema.values())
    if isinstance(schema, list):
        return sum(_titles(v) for v in schema)
    return 0


def strip_titles(schema):
    """The same schema without per-property titles (what a host could send instead)."""
    if isinstance(schema, dict):
        out = {k: strip_titles(v) for k, v in schema.items()
               if k != "properties"}
        if "properties" in schema:
            out["properties"] = {n: {k: strip_titles(v) for k, v in p.items() if k != "title"}
                                 for n, p in schema["properties"].items()}
        return out
    if isinstance(schema, list):
        return [strip_titles(v) for v in schema]
    return schema


async def measure(style: str, persona: str) -> list[dict]:
    """One row per tool, in the order the model sees them."""
    async with unit2.Host(styles.server_table(style, persona), _approve) as host:
        sent = {f["function"]["name"]: f for f in host.openai_tools()}
        rows = []
        for key, (_server, tool) in host.tools.items():
            rows.append({
                "tool": key,
                "description": len((tool.description or "").encode()),
                "schema": _size(tool.input_schema),
                "titles": _titles(tool.input_schema),
                "output": _size(tool.output_schema),
                "annotations": tool.annotations is not None,
                "sent": _size(sent[key]),
            })
    return rows


def total(rows: list[dict]) -> dict:
    keys = ("description", "schema", "titles", "output", "sent")
    out = {k: sum(r[k] for r in rows) for k in keys}
    out["tokens"] = out["sent"] // 4
    out["tokens_without_titles"] = (out["sent"] - out["titles"]) // 4
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Where a tool list's bytes go.")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--style", choices=styles.STYLES, action="append")
    parser.add_argument("--per-tool", action="store_true")
    args = parser.parse_args()
    load_env()
    print(f"{'style':<10} {'sent':>7} {'~tokens':>8} {'descr':>6} {'schema':>7} "
          f"{'titles':>7} {'output*':>8}  ~tokens without titles")
    for style in args.style or styles.STYLES:
        rows = asyncio.run(measure(style, args.persona))
        t = total(rows)
        print(f"{style:<10} {t['sent']:>7,} {t['tokens']:>8,} {t['description']:>6,} "
              f"{t['schema']:>7,} {t['titles']:>7,} {t['output']:>8,}  "
              f"{t['tokens_without_titles']:,}")
        if args.per_tool:
            for r in rows:
                print(f"  {r['tool']:<30} {r['sent']:>6,} {r['description']:>6,} "
                      f"{r['schema']:>7,} {r['titles']:>7,} {r['output']:>8,}")
    print("\n* output schemas are in tools/list but this host doesn't send them to the model.")


if __name__ == "__main__":
    main()
