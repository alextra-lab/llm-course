"""
Unit 9 - Cache-Aware Compaction.

The prompt cache reuses a prefix only when it is BYTE-FOR-BYTE identical (foundations Section 11,
Unit 2). So the cache-optimal behaviour is to never edit the prefix -- only append -- and to
compact on a SCHEDULE (rebuild once, refreeze) instead of every turn. This script shows both
halves with the course's token heuristic (no real KV cache needed).

What this shows:
  - Part A -- byte-identity reuse: simulate a growing conversation under two layouts and measure
    the byte-identical SHARED PREFIX reused turn-to-turn. Append-only-frozen keeps almost the whole
    prompt cached; compact-the-middle-every-turn resets the shared prefix to ~the head, so the
    prompt re-prefills every turn. (Proxy for cross-turn KV reuse; offline, deterministic.)
  - Part B -- the scheduled reset: the cost-optimal run length between rebuilds, L* = sqrt(2R/c)
    (rebuild cost R, per-turn carry cost c). Sweep the run length and watch total cost bottom out
    near L*. HONEST: production hardwires the quality term to 0, so c = delta_turn only -- the
    schedule is cost-only until Unit 11 supplies a real quality slope.

Runs fully offline -- no endpoint needed.

    python context-compression/examples/09/cache_aware_compaction.py
    python context-compression/examples/09/cache_aware_compaction.py 2>> run.jsonl
"""

import math
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common_context import estimate_tokens, log_event

_CHARS_PER_TOKEN = 4   # same heuristic as the Unit 1 meter


def render(messages):
    """Flatten a prompt to the bytes the cache would see (role + content, in order)."""
    return "\n".join(f"{m['role']}: {m.get('content') or ''}" for m in messages)


def shared_prefix_tokens(a, b):
    """The byte-identical leading run shared by two prompts, in heuristic tokens -- i.e. how much
    of prompt b the cache can reuse from prompt a (Unit 2's byte-identity rule)."""
    ra, rb = render(a), render(b)
    n = 0
    for ca, cb in zip(ra, rb):
        if ca != cb:
            break
        n += 1
    return n // _CHARS_PER_TOKEN


# A frozen head (system + first user message) and a big frozen middle (an early file read), then
# the conversation grows by one short exchange per turn.
HEAD = [{"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Walk the repo and fix the failing retry test."}]
FROZEN_MIDDLE = [{"role": "assistant", "content": "Reading config."},
                 {"role": "tool", "tool_call_id": "t1", "content": "config.py\n" + "setting = 1\n" * 700}]
EXCHANGES = [[{"role": "user", "content": f"step {i}: what next?"},
              {"role": "assistant", "content": f"step {i}: checked another module, continuing."}]
             for i in range(8)]


def part_a(sess, trace):
    print("Part A -- byte-identity reuse (shared prefix carried turn to turn)\n")
    print(f"  {'turn':4}  {'append-only frozen':>22}  {'compact-the-middle':>22}")
    append_prev = compact_prev = None
    append_total = compact_total = 0
    base = HEAD + FROZEN_MIDDLE
    for t, exch in enumerate(EXCHANGES):
        # Append-only: the whole prior prompt is an unchanged prefix; just add the new exchange.
        append_now = (append_prev or base) + exch
        # Compact-the-middle: rebuild the middle into a recap EVERY turn, so the prefix after the
        # head changes each turn (a fresh recap text) -- the cache resets to ~the head.
        recap = {"role": "assistant", "content": f"## Conversation Summary (rev {t}) -- {t + 1} turns so far"}
        compact_now = HEAD + [recap] + exch

        a_reuse = shared_prefix_tokens(append_prev, append_now) if append_prev else 0
        c_reuse = shared_prefix_tokens(compact_prev, compact_now) if compact_prev else 0
        if t > 0:
            append_total += a_reuse
            compact_total += c_reuse
            print(f"  {t:4}  {a_reuse:>18} tok  {c_reuse:>18} tok")
        append_prev, compact_prev = append_now, compact_now

    print(f"\n  total prefix reused across the run:  append-only {append_total} tok  vs  "
          f"compact-every-turn {compact_total} tok")
    log_event(sess, trace, 0, "compaction", strategy="frozen-reset", layout="append-only",
              prefix_reused=append_total, turns=len(EXCHANGES) - 1)
    log_event(sess, trace, 1, "compaction", strategy="frozen-reset", layout="compact-every-turn",
              prefix_reused=compact_total, turns=len(EXCHANGES) - 1)
    print("  -> freezing the layout keeps the prefix a cache hit; editing it every turn re-prefills.\n")


def cost_per_turn(R, c, L):
    """Amortized per-turn cost of resetting every L turns: one rebuild (R) plus the average extra
    carry of an un-reset, growing prompt (c per turn, averaged over the run)."""
    return R / L + c * (L - 1) / 2


def optimal_run_length(R, c):
    # L* = sqrt(2R/c) is continuous; the best integer run length is the cheaper of its two
    # neighbours (round() alone can pick the wrong one when the minimum sits between integers).
    exact = math.sqrt(2 * R / c)
    lo = max(1, math.floor(exact))
    return min(lo, lo + 1, key=lambda L: cost_per_turn(R, c, L))


def part_b(sess, trace):
    print("Part B -- the scheduled reset: cost-optimal run length L* = sqrt(2R/c)\n")
    R = 4000     # rebuild cost: re-prefill the whole layout once (tokens at full price)
    c = 200      # per-turn carry: how much more each un-reset turn costs (c = delta_turn; Q_slope=0)
    L_star = optimal_run_length(R, c)
    print(f"  rebuild cost R = {R} tok, per-turn carry c = {c} tok  ->  L* = sqrt(2R/c) = {L_star} turns\n")
    print(f"  {'run length L':>12}  {'amortized cost/turn':>20}")
    best = None
    for L in range(1, 16):
        cost = cost_per_turn(R, c, L)
        mark = "  <- L*" if L == L_star else ""
        print(f"  {L:>12}  {cost:>18.1f}{mark}")
        if best is None or cost < best[1]:
            best = (L, cost)
    log_event(sess, trace, 2, "compaction", strategy="frozen-reset", trigger="L*",
              R=R, c=c, L_star=L_star, swept_min_L=best[0])
    print(f"\n  swept minimum at L = {best[0]} (matches L* = {L_star}); precedence in production is "
          f"hard ceiling (0.50) -> anti-thrash floor -> L*.")
    print("  NOTE: c = delta_turn only -- production hardwires the quality term to 0. Unit 11 can fit it.")


def main():
    sess, trace = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    print(f"cache-aware compaction  (window heuristic; shared-prefix proxy for KV reuse)\n")
    part_a(sess, trace)
    part_b(sess, trace)


if __name__ == "__main__":
    main()
