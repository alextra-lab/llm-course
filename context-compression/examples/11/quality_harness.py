"""
Unit 11 - Measuring Compression Quality.

Every unit since Unit 1 emitted a joinable record (the §10 session_id/trace_id/step tuple). On
their own they are a pile of log lines; read back as a timeline they answer the question the whole
course deferred to here: did the compression cost us anything we needed?

This harness reads a run.jsonl (captured from earlier units with `2>> run.jsonl`) -- or generates a
small sample so it runs standalone -- and computes:
  - referenced_later misses: join the identifiers a compaction DROPPED (lost_ids) against the ones a
    later turn REFERENCED (referenced_ids). A miss is a measured quality failure the ratio hides.
  - the before/after token curve: total tokens over steps (window fills, a compaction bites, fills).
  - the under-budget waste count: compaction_decision records that compressed while under the soft
    line (Unit 2) -- cache + quality spent for nothing.
  - a NO-REGRESSION GATE: exit non-zero if quality regressed, so it can run in CI.

Runs fully offline.

    python context-compression/examples/11/quality_harness.py                 # uses a sample run
    python context-compression/examples/11/quality_harness.py run.jsonl        # your captured log
    python context-compression/examples/11/quality_harness.py ... ; echo $?    # gate exit code

(The output-change check -- run full vs compacted context and diff the answers -- needs the model
and the original texts, so it is described in the lesson rather than computed from logs here.)
"""

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))   # context-compression/examples
from common_context import log_event                        # stdlib-only; no endpoint needed

SOFT = 0.65   # the Unit 2 soft threshold; compressing below this is waste


def load_records(path):
    """Read JSONL records from a captured run; skip any non-JSON lines."""
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return sorted(out, key=lambda r: r.get("step", 0))


def sample_records():
    """A representative one-run stream when no run.jsonl is given. The window fills, a summarize
    compaction drops FRE-512, a later turn needs FRE-512 (a MISS), and one compaction fires while
    under budget (waste)."""
    s, t = "sess01", "trace01"
    base = {"session_id": s, "trace_id": t}
    return [
        {**base, "step": 0, "operation": "context_meter", "total": 1800, "budget": 8000},
        {**base, "step": 1, "operation": "compaction_decision", "decision": "compress",
         "fraction": 0.42},                                    # under soft 0.65 -> waste
        {**base, "step": 2, "operation": "compaction", "strategy": "summarize",
         "tokens_before": 3400, "tokens_after": 900,
         "kept_ids": ["db-prod-1", "5432"], "lost_ids": ["FRE-512"]},
        {**base, "step": 3, "operation": "context_meter", "total": 1100},
        {**base, "step": 4, "operation": "turn", "referenced_ids": ["db-prod-1"]},  # kept -> ok
        {**base, "step": 5, "operation": "context_meter", "total": 2600},
        {**base, "step": 6, "operation": "turn", "referenced_ids": ["FRE-512"]},    # lost -> MISS
        {**base, "step": 7, "operation": "context_meter", "total": 3000},
    ]


def referenced_later(records):
    """Identifiers dropped by a compaction and then referenced by a later turn."""
    lost, misses = {}, []
    for r in records:
        for i in r.get("lost_ids", []):
            lost.setdefault(i, r["step"])
        for i in r.get("referenced_ids", []):
            if i in lost and r["step"] > lost[i]:
                misses.append((i, lost[i], r["step"]))
    return misses


def under_budget_compactions(records):
    return [r for r in records if r.get("operation") == "compaction_decision"
            and r.get("decision") == "compress" and r.get("fraction", 1.0) < SOFT]


def token_curve(records):
    return [(r["step"], r["total"]) for r in records
            if r.get("operation") == "context_meter" and "total" in r]


def gate(report):
    """Non-zero (CI failure) on a real quality REGRESSION: a dropped thing was needed later.
    Under-budget compaction is waste (Unit 2) -- reported as a warning, not a build-breaker."""
    return 1 if report["referenced_later_misses"] > 0 else 0


def main():
    if len(sys.argv) > 1:
        records = load_records(sys.argv[1])
        source = sys.argv[1]
    else:
        records = sample_records()
        source = "(built-in sample)"

    misses = referenced_later(records)
    waste = under_budget_compactions(records)
    curve = token_curve(records)

    print(f"quality harness  (reading {source}; {len(records)} records)\n")

    print("  token curve (total tokens by step):")
    for step, total in curve:
        bar = "#" * (total // 100)
        print(f"    step {step:>2}  {total:>5}  {bar}")

    print(f"\n  referenced-later misses: {len(misses)}")
    for ident, dropped, needed in misses:
        print(f"    MISS  {ident!r} dropped at step {dropped}, referenced at step {needed}")

    print(f"\n  under-budget compactions (waste, warning only): {len(waste)}")
    for r in waste:
        print(f"    step {r['step']}: compressed at {r['fraction']:.0%} (< soft {SOFT:.0%})")

    peak = max((total for _, total in curve), default=0)
    report = {"referenced_later_misses": len(misses), "under_budget_compactions": len(waste),
              "peak_tokens": peak}
    code = gate(report)
    print(f"\n  no-regression gate: {'PASS' if code == 0 else 'FAIL'} (exit {code})")

    # Emit the report joined to the run it analyzed (its session_id/trace_id), as a final step.
    sid = next((r.get("session_id") for r in records if r.get("session_id")), "harness")
    tid = next((r.get("trace_id") for r in records if r.get("trace_id")), "harness")
    last = max((r.get("step", 0) for r in records), default=0)
    log_event(sid, tid, last + 1, "quality_report", gate=("pass" if code == 0 else "fail"), **report)
    sys.exit(code)


if __name__ == "__main__":
    main()
