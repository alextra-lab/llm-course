"""
Unit 7 - When to Fire: Triggers & Async Compression.

Units 3-6 built the *what* (drop / summarize / head-tail / pre-pass) but left the *when* open.
This script builds the *when*: two thresholds with two urgencies, plus a re-fire cursor.

What this shows:
  - decide(): SOFT line (~0.65) -> compact ASYNC in the background, the turn does not wait;
    HARD line (~0.85) -> compact SYNC before the next call, the turn blocks; below soft -> skip.
  - the re-fire cursor: once a soft compaction fires, do not fire again until REFIRE_GAP new
    messages have arrived -- otherwise it fires every turn, each one a model call + a cache break.
  - the soft path runs on a real background thread (fire-and-forget); the hard path blocks and,
    like production (ADR-0076), ASKS the user "stop vs compress" first (guarded by isatty() so an
    unattended run does not hang).
  - a compaction record per fire, with the timing fields: trigger, fired vs skip-refire, blocking,
    and latency_ms measured ON vs OFF the critical path.

The compaction body here is deterministic and offline (head/tail kept, middle -> a STATIC marker,
as in Units 3 and 5) with a small fixed delay standing in for the real summarizer cost. The point
of this unit is the timing, not the compaction -- so the script runs fully offline, no endpoint.

    python context-compression/examples/07/triggers_and_async.py

The async win is a latency claim, so capture the telemetry and compare on- vs off-path latency:

    python context-compression/examples/07/triggers_and_async.py 2>> run.jsonl
"""

import sys
import time
import threading
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common_context import estimate_tokens, log_event

BUDGET = 4000
SOFT = 0.65         # below this: do nothing (Unit 2)
HARD = 0.85         # at/above this: the window is nearly full -- must compact before the next call
REFIRE_GAP = 4      # don't re-fire a soft compaction until this many new messages have arrived
COMPACT_COST_S = 0.05   # stand-in for the summarizer's real network latency (Unit 4)

# A static marker keeps the prefix byte-identical across turns (Unit 2's cache rule); a
# regenerated recap would not -- which is the caveat this unit carries from Unit 4.
MARKER = {"role": "assistant", "content": "[Earlier messages truncated]"}


def decide(used, budget, last_fire_index, msg_index, soft=SOFT, hard=HARD, gap=REFIRE_GAP):
    """skip / soft / hard / skip-refire -- the timing decision for one turn."""
    frac = used / budget
    if frac >= hard:
        return "hard", frac
    if frac >= soft:
        if last_fire_index is not None and (msg_index - last_fire_index) < gap:
            return "skip-refire", frac      # too soon since the last soft fire -- wait
        return "soft", frac
    return "skip", frac


def compact(messages, budget):
    """Deterministic, offline compaction: keep head (2) + tail (4), middle -> one static marker
    (Units 3/5). The fixed sleep stands in for the summarizer's real cost."""
    time.sleep(COMPACT_COST_S)
    before = estimate_tokens(messages)
    if len(messages) <= 6:
        return messages, before, before
    new = messages[:2] + [MARKER] + messages[-4:]
    return new, before, estimate_tokens(new)


def fire_soft(messages, budget, sess, trace, step):
    """Background, fire-and-forget: the turn does NOT wait for this."""
    def work():
        t0 = time.perf_counter()
        _, before, after = compact(messages, budget)
        log_event(sess, trace, step, "compaction", trigger="soft", fired=True, blocking=False,
                  latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                  tokens_before=before, tokens_after=after)
    th = threading.Thread(target=work, daemon=True)
    th.start()                                  # returns at once; the turn continues
    return th


def ask_stop_or_compress():
    """ADR-0076: at the hard line, ask before the blocking, lossy compaction."""
    if not sys.stdin.isatty():                  # unattended (CI, a pipe): don't hang -- default
        print("  (non-interactive: defaulting to 'compress')")
        return "compress"
    ans = input("  hard threshold reached -- [s]top or [c]ompress? ").strip().lower()
    return "stop" if ans.startswith("s") else "compress"


def fire_hard(messages, budget, sess, trace, step):
    """Synchronous: the turn blocks until this finishes (the window is nearly full)."""
    if ask_stop_or_compress() == "stop":
        log_event(sess, trace, step, "compaction", trigger="hard", fired=False, blocking=True,
                  decision="stop")
        return None, 0.0
    t0 = time.perf_counter()
    new, before, after = compact(messages, budget)
    ms = round((time.perf_counter() - t0) * 1000, 1)        # ON the critical path -- the turn waited
    log_event(sess, trace, step, "compaction", trigger="hard", fired=True, blocking=True,
              latency_ms=ms, tokens_before=before, tokens_after=after)
    return new, ms


# The transcript handed to compact() when a trigger fires: anchored head (system + task), a big
# old tool output in the middle, recent tail. It is genuinely over budget on its own (the tool
# output alone exceeds the window), so the compaction shows a real shrink.
HEAD = [{"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Raise the retry limit and re-run the failing test."}]
BIG_TOOL = {"role": "tool", "tool_call_id": "t1", "content": "x" * 16800}   # ~4200 tok > BUDGET
TAIL = [{"role": "assistant", "content": "Looking now."},
        {"role": "user", "content": "thanks"},
        {"role": "assistant", "content": "Found the config."},
        {"role": "user", "content": "go on"}]
OVER_BUDGET = HEAD + [BIG_TOOL] + TAIL

# Scripted METER READINGS per message index, chosen to exercise every branch deterministically:
# the window fills, crosses soft (async fire), idles while the cursor suppresses re-fires, fires
# again once the gap has elapsed, then crosses hard. In a real agent these readings come from
# running Unit 1's meter on the actual growing transcript each turn; here they are fixed so the
# run is reproducible. The cursor counts MESSAGE INDEX, so it both suppresses and later permits.
# (msg_index, used).
TURNS = [
    (2,  1800),   # 45% -- skip (under soft)
    (5,  2700),   # 68% -- crosses soft -> async fire (cursor = 5)
    (6,  2760),   # 69% -- over soft but only 1 msg later -> skip-refire
    (7,  2820),   # 70% -- 2 msg later -> skip-refire
    (10, 2950),   # 74% -- 5 msg later, gap elapsed -> async fire again (cursor = 10)
    (13, 3500),   # 88% -- crosses hard -> blocking fire (asks first)
]


def main():
    sess, trace = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    print(f"triggers  (budget {BUDGET} tokens, soft {SOFT:.0%}, hard {HARD:.0%}, re-fire gap {REFIRE_GAP})\n")
    last_fire = None
    threads = []
    for msg_index, used in TURNS:
        action, frac = decide(used, BUDGET, last_fire, msg_index)
        head = f"  msg {msg_index:<3} {used:5d} tok {frac:4.0%}"
        if action == "skip":
            print(f"{head}  -> skip (under soft)")
            log_event(sess, trace, msg_index, "compaction", trigger="none", fired=False,
                      blocking=False, fraction=round(frac, 3))
        elif action == "skip-refire":
            print(f"{head}  -> skip-refire ({msg_index - last_fire} msg since last fire < gap {REFIRE_GAP})")
            log_event(sess, trace, msg_index, "compaction", trigger="soft", fired=False,
                      blocking=False, reason="refire-gap", fraction=round(frac, 3))
        elif action == "soft":
            t0 = time.perf_counter()
            threads.append(fire_soft(OVER_BUDGET, BUDGET, sess, trace, msg_index))
            blocked_ms = (time.perf_counter() - t0) * 1000      # ~0: the turn did not wait
            print(f"{head}  -> SOFT: compact in background, turn continues (blocked {blocked_ms:.1f} ms)")
            last_fire = msg_index
        elif action == "hard":
            print(f"{head}  -> HARD: window nearly full -- must act before the next call")
            new, ms = fire_hard(OVER_BUDGET, BUDGET, sess, trace, msg_index)
            if new is None:                     # user chose to stop -- no compaction ran
                print("           user chose to stop -- session saved, nothing compacted")
                break
            print(f"           compressed -- turn blocked {ms:.1f} ms on the critical path")
            last_fire = msg_index

    for th in threads:                          # let the background compactions finish before exit
        th.join()
    print("\nSoft fires ran off the critical path; the hard line forces a synchronous choice "
          "(compress and block, or stop). Re-inserting a regenerated recap every turn would "
          "still break the cache (Unit 9).")


if __name__ == "__main__":
    main()
