"""
Unit 12 - The Measured Default (capstone).

Wire the whole course into one layered policy that does the LEAST that works each turn, surfaces
what it did with a session meter, and flags the case no compaction can fix: a turn too big to
compress, which must be DECOMPOSED instead.

The decision tree (cheapest first), with the four-mechanism taxonomy on the meter:
  - under budget            -> do nothing                              (Unit 2)
  - soft <= usage < hard    -> B: pre-pass -> offload -> head/tail     (Units 6, 8, 5/4)  meter: B
  - usage >= hard           -> D: cache-aware frozen reset (rebuild)   (Units 7, 9)        meter: D
  - still over after B/D    -> A: coarse last-resort drop              (Unit 3)            meter: A (alert)
  - one message > budget    -> DECOMPOSE: no mechanism fits            (Unit 12 thesis)

A is DORMANT by design (its firing is an alert, not a routine). C (tool-result middle-truncation)
is PARKED and never run -- truncating a read corrupts the file (Units 6, 8).

Runs fully offline -- no endpoint needed. Reuses common_context (estimate_tokens / describe /
offload / log_event).

    python context-compression/examples/12/measured_default.py
    python context-compression/examples/12/measured_default.py 2>> run.jsonl
"""

import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common_context import estimate_tokens, describe, offload, log_event

BUDGET = 4000
SOFT, HARD = 0.65, 0.85
TARGET = int(SOFT * BUDGET)     # compact back to under the soft line, restoring headroom
PREPASS_THRESHOLD = 800
MARKER = {"role": "assistant", "content": "[Earlier messages truncated]"}


def _head_end(msgs):
    i = 0
    while i < len(msgs) and msgs[i]["role"] == "system":
        i += 1
    if i < len(msgs) and msgs[i]["role"] == "user":
        i += 1
    return i


def prepass(msgs):
    """Unit 6: collapse big tool outputs to a one-line descriptor (errors kept verbatim)."""
    out = []
    for m in msgs:
        content = m.get("content") or ""
        if m["role"] == "tool" and estimate_tokens(content) >= PREPASS_THRESHOLD and "error" not in content.lower():
            out.append({**m, "content": f"<offloadable: {describe(content)}>"})
        else:
            out.append(m)
    return out


def offload_giants(msgs, store):
    """Unit 8: move any still-large tool output to the blob store, keep a short reference."""
    out = []
    for m in msgs:
        content = m.get("content") or ""
        if m["role"] == "tool" and estimate_tokens(content) >= PREPASS_THRESHOLD:
            h = offload(content, store)
            out.append({**m, "content": f"[offloaded {describe(content)} handle={h[:12]}]"})
        else:
            out.append(m)
    return out


def head_tail(msgs):
    """Units 5/4: keep head + recent tail verbatim, replace the middle with a recap marker."""
    he = _head_end(msgs)
    tail = msgs[max(he, len(msgs) - 4):]
    return msgs[:he] + [MARKER] + tail if len(msgs) - he > 4 else msgs


def coarse_drop(msgs):
    """Unit 3: the last-resort net -- collapse history to leading system messages + the last user
    message (the task anchor and the live turn), dropping everything between."""
    i = 0
    while i < len(msgs) and msgs[i]["role"] == "system":
        i += 1
    systems = msgs[:i]
    last_user = next((m for m in reversed(msgs) if m["role"] == "user"), None)
    return systems + [MARKER] + ([last_user] if last_user else [])


def policy(msgs, meter, store, sess, trace, step):
    """The layered decision tree: return (new_msgs, action). The cheapest action that gets back
    under the soft target wins; if neither the soft path (B) nor the hard reset (D) fits, the
    dormant coarse drop (A) fires as an alert."""
    used = estimate_tokens(msgs)
    frac = used / BUDGET

    # No mechanism can fit a single message larger than the whole budget -> decompose the task.
    if estimate_tokens([max(msgs, key=lambda m: estimate_tokens([m]))]) > BUDGET:
        meter["decompose"] += 1
        log_event(sess, trace, step, "compaction", strategy="decompose",
                  reason="a single message exceeds the budget", tokens=used)
        return msgs, "DECOMPOSE (a turn too big to compress -- split the task)"

    if frac < SOFT:
        log_event(sess, trace, step, "compaction_decision", decision="skip", fraction=round(frac, 2))
        return msgs, "skip (under budget)"

    if frac >= HARD:                                    # D: cache-aware scheduled reset (Units 7, 9)
        new = head_tail(msgs)
        if estimate_tokens(new) <= TARGET:
            meter["D"] += 1
            log_event(sess, trace, step, "compaction", strategy="frozen-reset",
                      tokens_before=used, tokens_after=estimate_tokens(new))
            return new, "D: cache-aware frozen reset"
    else:
        # B: soft path, cheapest and least-lossy first -- pre-pass (free) -> offload -> head/tail.
        for mech, fn in (("prepass", prepass), ("offload", lambda m: offload_giants(m, store)), ("head-tail", head_tail)):
            new = fn(msgs)
            if estimate_tokens(new) <= TARGET:
                meter["B"] += 1
                log_event(sess, trace, step, "compaction", strategy=mech,
                          tokens_before=used, tokens_after=estimate_tokens(new))
                return new, f"B: {mech}"

    new = coarse_drop(msgs)                              # A: nothing above fit -> the dormant net fires
    meter["A"] += 1
    log_event(sess, trace, step, "compaction", strategy="drop", trigger="last-resort",
              tokens_before=used, tokens_after=estimate_tokens(new))
    return new, "A: coarse drop (ALERT -- B/D did not keep up)"


def render_meter(msgs, meter):
    frac = estimate_tokens(msgs) / BUDGET
    quality = "DEGRADED" if (meter["A"] or meter["decompose"]) else "OK"
    return (f"session meter | window {frac:4.0%} | B compactions {meter['B']} | D resets {meter['D']} "
            f"| A alerts {meter['A']} | quality {quality}")


# A scripted session that exercises every layer. (token estimates ~ chars/4 + 4 per message)
SYS = {"role": "system", "content": "You are a coding assistant."}
TASK = {"role": "user", "content": "Fix the failing retry test and ship it."}
SMALL = lambda i: [{"role": "user", "content": f"step {i}?"}, {"role": "assistant", "content": f"on step {i}."}]
BIG_TOOL = {"role": "tool", "tool_call_id": "t1", "content": "config.py\n" + "setting=1\n" * 700}  # ~1.7k tok
MED = {"role": "assistant", "content": "reasoning: " + "z" * 4400}                                  # ~1.1k tok
BIG_TURN = lambda i: {"role": "assistant", "content": f"analysis {i}: " + "y" * 3000}               # ~0.75k tok, not a tool
HUGE_TURN = {"role": "user", "content": "Review this entire vendored dependency in one go:\n" + "x" * 18000}  # >budget


def main():
    sess, trace = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    meter = {"B": 0, "D": 0, "A": 0, "decompose": 0}
    print(f"measured default  (budget {BUDGET} tokens, soft {SOFT:.0%}, hard {HARD:.0%})\n")

    timeline = [
        ("small turns", [SYS, TASK] + SMALL(1)),                                  # under budget -> skip
        ("a big file read", [SYS, TASK, BIG_TOOL, MED]),                          # soft -> B pre-pass
        ("two big reads in the middle", [SYS, TASK, BIG_TOOL, BIG_TOOL] + SMALL(2) + SMALL(3)),  # hard -> D reset
        ("irreducible recent history", [SYS, TASK, BIG_TURN(1), BIG_TURN(2), BIG_TURN(3), BIG_TURN(4)]),  # B can't shrink -> A
        ("one giant turn", [SYS, TASK, HUGE_TURN]),                               # > budget alone -> decompose
    ]
    for step, (label, msgs) in enumerate(timeline):
        new, action = policy(msgs, meter, f"/tmp/cc_capstone_blobs", sess, trace, step)
        print(f"  {label:28} -> {action}")
        print(f"     {render_meter(new, meter)}")

    print("\nLayered, cheapest-first: do nothing -> B (pre-pass/offload/head-tail) -> D (reset) -> "
          "A (alert). When one turn alone won't fit, DECOMPOSE -- the cheapest tokens are the ones "
          "you never generate.")
    log_event(sess, trace, len(timeline), "session_summary", **meter)


if __name__ == "__main__":
    main()
