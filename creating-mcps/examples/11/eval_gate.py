"""
Unit 11: Unit 6's tool-selection eval as a gate: a change to a tool's name, description or
schema doesn't merge if the model starts picking the wrong tool.

A model's choices vary from run to run, so one run proves little. The gate runs the golden set
several times and compares each metric's mean with a floor, and the worst single run with a
lower one. The floors live in eval_floors.json, next to this file, and come from your own
baseline: `--baseline` runs the eval and writes floors a margin below what it measured, so the
gate catches a real drop and not ordinary variation.

Two parts, so CI can run the first everywhere:

    consistency   the golden set still names tools and arguments that exist (no model)
    selection     the model's choices, over N runs, against the floors (needs the model;
                  without OPENAI_BASE_URL / OPENAI_API_KEY it's skipped, and says so)

    python examples/11/eval_gate.py                       # consistency, then the gate
    python examples/11/eval_gate.py --repeats 5 --baseline   # measure, write the floors
    python examples/11/eval_gate.py --history evals.jsonl    # also append this run

Exit status: 0 passed or skipped, 1 a floor was crossed or the golden set is inconsistent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "06"), str(HERE.parent / "05"), str(HERE.parent)]
import selection  # noqa: E402  (Unit 6)
from common_mcp import llm, load_env  # noqa: E402

FLOORS = HERE / "eval_floors.json"
METRICS = ("right_tool", "right_args", "call_ok")


def floors_from(row: dict, margin: float, worst_margin: float) -> dict:
    """Floors a margin below a measured baseline: the mean, and the worst run."""
    out = {"style": row["style"], "measured": time.strftime("%Y-%m-%d"),
           "repeats": row["repeats"]}
    for m in METRICS:
        worst = float(row[m + "_range"].split("-")[0].rstrip("%")) / 100
        out[m] = {"mean": round(max(0.0, row[m] - margin), 2),
                  "worst_run": round(max(0.0, worst - worst_margin), 2)}
    return out


def judge(row: dict, floors: dict) -> list[str]:
    """What fell below its floor, in words. Empty: the gate passes."""
    failed = []
    for m in METRICS:
        worst = float(row[m + "_range"].split("-")[0].rstrip("%")) / 100
        if row[m] < floors[m]["mean"]:
            failed.append(f"{m}: mean {row[m]:.0%} is below its floor {floors[m]['mean']:.0%}")
        if worst < floors[m]["worst_run"]:
            failed.append(f"{m}: worst run {worst:.0%} is below its floor "
                          f"{floors[m]['worst_run']:.0%}")
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Tool selection, as a merge gate.")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--baseline", action="store_true", help="write the floors from this run")
    parser.add_argument("--margin", type=float, default=0.05)
    parser.add_argument("--history", help="append this run's summary to a JSONL file")
    args = parser.parse_args()
    load_env()
    floors = json.loads(FLOORS.read_text())
    style = floors["style"]

    problems = asyncio.run(selection.check_all(args.persona))[style]
    print(f"consistency ({style}): " + ("; ".join(problems) if problems else "ok"))
    if problems:
        sys.exit(1)
    if llm() is None:
        print("selection: skipped (no model: set OPENAI_BASE_URL and OPENAI_API_KEY)")
        return

    results = asyncio.run(selection.run(args.persona, [style], args.repeats))
    row = selection.summarise(results)[0]
    print("selection:", json.dumps(row))
    if args.history:
        with open(args.history, "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **row}) + "\n")
    if args.baseline:
        FLOORS.write_text(json.dumps(floors_from(row, args.margin, args.margin * 2), indent=2)
                          + "\n")
        print(f"floors written to {FLOORS.name}")
        return
    failed = judge(row, floors)
    for f in failed:
        print("  FAIL", f)
    print("gate: " + ("failed" if failed else "passed"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
