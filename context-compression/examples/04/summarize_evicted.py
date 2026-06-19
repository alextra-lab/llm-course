"""
Unit 4 - Summarizing Evicted Turns: keep a lossy trace instead of throwing turns away.

Unit 3 dropped the oldest middle turns outright. This unit replaces that delete with a
*summarize-then-evict*: compress the slice you are about to lose into a compact, structured
recap, then drop the originals. The window shrinks like a drop, but the memory does not go
blank -- the recap keeps the identifiers a later turn may still need.

What this shows:
  - the 4-section working-summary schema, detected downstream by its "## Conversation Summary"
    header (Decisions / Entities / Facts / Open Items), <=200 words, identifiers verbatim;
  - a cheap "compressor" model (temp=0.2, timeout=25s, max_tokens=512) with a GRACEFUL
    FALLBACK to the "[Earlier messages truncated]" marker on any failure;
  - the recap inserted as role="assistant" (NOT system -- a non-first system message is
    dropped by role validation), a compaction record + a quality loop (did the summary keep
    the host and ticket the tail asks for?), and a cache check that shows why re-inserting a
    regenerated recap every turn breaks the prompt cache (the static marker does not).

The summarizer call is OPT-IN: set OPENAI_BASE_URL (your foundations .env) for a real
structured summary; offline it skips cleanly by exercising the fallback marker.

    python context-compression/examples/04/summarize_evicted.py
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common import get_client, MODEL
from common_context import estimate_tokens, log_event

BUDGET = 4000
MARKER_TEXT = "[Earlier messages truncated]"

# A dedicated cheap compressor is the production pattern (a small "compressor" model -- think
# gpt-5.4-nano-class). We only have the foundations model here, so default to it; COMPRESSOR_MODEL
# is the seam where a cheaper model would plug in.
COMPRESSOR_MODEL = os.environ.get("COMPRESSOR_MODEL", MODEL)

# The summarizer is told to emit EXACTLY the 4-section schema. The "## Conversation Summary"
# header is load-bearing: downstream code detects a summary by `startswith` on it. The <=200-word
# rule is GUIDANCE to the model; the hard bound is max_tokens=512 on the call (see summarize()).
SUMMARIZER_SYSTEM = (
    "You compress a slice of an assistant conversation into a compact working summary.\n"
    "Output EXACTLY this structure and nothing else:\n"
    "## Conversation Summary\n"
    "- **Decisions:** <choices that were made>\n"
    "- **Entities:** <files, services, people, identifiers involved>\n"
    "- **Facts:** <durable facts established>\n"
    "- **Open Items:** <unfinished work or open questions>\n\n"
    "Rules: at most 200 words. Include ONLY information present in the messages -- invent "
    "nothing. Preserve identifiers VERBATIM: file paths, ticket IDs, function names, model "
    "ids, hosts, ports. If the messages already contain a '## Conversation Summary' block, "
    "fold it into your output rather than repeating it."
)

# The head (system + first user message -- the task) is anchored, exactly as in Unit 3.
SYSTEM = {"role": "system", "content": "You are a coding assistant. Keep answers short and correct."}
TASK = {"role": "user", "content": "Wire up the nightly rollup job and get it green."}

# The MIDDLE: older turns we will summarize, then evict. They carry identifiers the tail needs.
# The read is a real tool pair (assistant tool_calls -> tool result), kept whole inside the
# middle so summarizing the slice never orphans a tool result (the Unit 3 tool-pair rule).
CONFIG = "db_host: db-prod-1\ndb_port: 5432\nrollup_window: 24h\nretries: 3\n" * 320  # ~5k tokens
MIDDLE = [
    {"role": "assistant", "content": "I'll read the config first.",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path": "/etc/app/config.yaml"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": CONFIG},
    {"role": "assistant",
     "content": "Config points the warehouse at db-prod-1:5432; the rollup is built by compute_rollup()."},
    {"role": "user", "content": "Good. Track this under FRE-512 and make compute_rollup idempotent."},
    {"role": "assistant", "content": "Done -- compute_rollup is now idempotent; tracked under FRE-512."},
]

# The recent TAIL: kept verbatim. It literally asks for facts that live in the middle -- so the
# summary's quality is testable: did it keep db-prod-1:5432 and FRE-512?
TAIL = [
    {"role": "user", "content": "Remind me which host the rollup writes to?"},
    {"role": "assistant", "content": "It writes to the warehouse configured for the project."},
    {"role": "user", "content": "And which ticket is this tracked under?"},
]

MESSAGES = [SYSTEM, TASK] + MIDDLE + TAIL

# Identifiers worth preserving across the eviction. A good summary keeps these; the marker keeps none.
NEEDED = ["/etc/app/config.yaml", "db-prod-1", "5432", "FRE-512", "compute_rollup"]
# The subset the TAIL literally asks for -- losing these blocks a later turn outright.
TAIL_NEEDS = ["db-prod-1", "FRE-512"]


def render(messages) -> str:
    """Flatten a message slice into plain text for the summarizer (tool calls shown inline)."""
    lines = []
    for m in messages:
        content = m.get("content") or ""
        if m.get("tool_calls"):
            calls = ", ".join(f"{tc['function']['name']}({tc['function']['arguments']})"
                              for tc in m["tool_calls"])
            content = f"{content} [calls: {calls}]".strip()
        lines.append(f"{m['role']}: {content}")
    return "\n".join(lines)


def summarize(client, slice_messages):
    """Compress a slice into the 4-section schema. Returns the summary text, or None on ANY
    failure -- so the caller can fall back to the plain marker. temp low for stable output;
    max_tokens caps the cost even if the model ignores the 200-word ask; timeout bounds the wait
    (production fires this fire-and-forget; the trigger machinery is Unit 7)."""
    try:
        r = client.chat.completions.create(
            model=COMPRESSOR_MODEL,
            messages=[{"role": "system", "content": SUMMARIZER_SYSTEM},
                      {"role": "user", "content": render(slice_messages)}],
            temperature=0.2,
            max_tokens=512,
            timeout=25,
        )
        text = (r.choices[0].message.content or "").strip()
        # If the model did not produce the schema, treat it as a failure and fall back.
        return text if text.startswith("## Conversation Summary") else None
    except Exception as e:                       # network, timeout, bad output -- never crash a turn
        print(f"(compressor failed: {e!r} -- falling back to the marker)", file=sys.stderr)
        return None


def recap_message(summary):
    """Build the recap message. role=assistant, NOT system: some transcript validators (and a
    production role-fixer) keep only the FIRST system message and drop any later system-role
    message, which would silently delete the recap. assistant survives in the middle of a
    transcript."""
    return {"role": "assistant", "content": summary if summary else MARKER_TEXT}


def _serialize(messages) -> str:
    """Flatten a prompt to the bytes the cache sees (good enough to measure a shared prefix)."""
    return "\n".join(f"{m['role']}: {m.get('content') or ''}" for m in messages)


def _shared_prefix_tokens(a: str, b: str) -> int:
    """Longest common byte prefix of two serialized prompts, in estimated tokens -- a proxy for
    how much of the KV cache survives unchanged from one turn to the next (Unit 2's byte-identity
    rule). The server reuses the cache only up to the first byte that differs."""
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n // 4   # _CHARS_PER_TOKEN, the same heuristic the Unit 1 meter uses


# Two successive re-summarizations: the same load-bearing header, but a rewritten body -- what a
# real compressor produces turn to turn. They share only the header before they diverge. The marker,
# by contrast, is byte-identical every turn. (Illustrative strings so the demo runs offline too.)
RECAP_T0 = ("## Conversation Summary\n- **Decisions:** read /etc/app/config.yaml; track FRE-512\n"
            "- **Entities:** db-prod-1:5432, compute_rollup\n- **Facts:** warehouse db-prod-1:5432\n"
            "- **Open Items:** confirm the job is green")
RECAP_T1 = ("## Conversation Summary\n- **Decisions:** made compute_rollup idempotent; track FRE-512\n"
            "- **Entities:** db-prod-1:5432, compute_rollup\n- **Facts:** warehouse db-prod-1:5432\n"
            "- **Open Items:** bump retries to 5")


def cache_break_demo():
    """Why re-inserting a regenerated recap every turn breaks the cache -- and a static marker does
    not. Simulate the next turn (a new user message appended) two ways, and measure the prefix
    tokens still valid: (a) the recap is the unchanged marker; (b) the recap is re-summarized, so
    its body changes from the header onward. Returns (marker_reuse, recap_reuse)."""
    head, next_user = [SYSTEM, TASK], {"role": "user", "content": "One more: bump retries to 5."}
    # (a) static marker, identical both turns -> only the appended tail differs, so all of the
    # prior prompt is still a valid prefix.
    m0 = _serialize(head + [recap_message(None)] + TAIL)
    m1 = _serialize(head + [recap_message(None)] + TAIL + [next_user])
    # (b) a regenerated recap -> the recap body changes, so the prefix breaks at the recap.
    r0 = _serialize(head + [{"role": "assistant", "content": RECAP_T0}] + TAIL)
    r1 = _serialize(head + [{"role": "assistant", "content": RECAP_T1}] + TAIL + [next_user])
    return _shared_prefix_tokens(m0, m1), _shared_prefix_tokens(r0, r1)


def main():
    session_id, trace_id = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    before = estimate_tokens(MESSAGES)
    print(f"before: {len(MESSAGES)} messages, {before} tokens, {before / BUDGET:.0%} of budget "
          f"({'OVER' if before > BUDGET else 'under'})")

    client = get_client() if os.environ.get("OPENAI_BASE_URL") else None
    if client is None:
        print("(OPENAI_BASE_URL not set -- skipping the compressor call and exercising the "
              "graceful fallback, exactly as production does on any failure.)")

    summary = summarize(client, MIDDLE) if client else None

    # Rebuild: anchored head + the recap (in the evicted middle's place) + the verbatim tail.
    kept = [SYSTEM, TASK, recap_message(summary)] + TAIL
    after = estimate_tokens(kept)
    print(f"after summarize+evict: {len(kept)} messages, {after} tokens, {after / BUDGET:.0%} "
          f"of budget -- evicted {len(MIDDLE)} middle message(s)")

    print("\n--- recap inserted (role=assistant) ---")
    print(kept[2]["content"])

    # The quality loop: did the recap keep the identifiers worth preserving -- and, specifically,
    # the ones the TAIL literally asks for?
    recap_text = kept[2]["content"]
    survived = [s for s in NEEDED if s in recap_text]
    lost = [s for s in NEEDED if s not in recap_text]
    blocked = [s for s in TAIL_NEEDS if s not in recap_text]
    print(f"\nidentifiers kept: {survived or 'NONE'}")
    if lost:
        print(f"identifiers lost: {lost}")
    if blocked:
        print(f"  -> the tail asks for {blocked} -- those questions can no longer be answered")

    # The cache check: re-inserting a regenerated recap every turn busts the cache; a static marker
    # does not (Unit 2's byte-identity rule; full treatment in Unit 9).
    marker_reuse, recap_reuse = cache_break_demo()
    print("\n--- cache check: prefix tokens still valid on the next turn ---")
    print(f"  static marker (unchanged): {marker_reuse} tokens reused")
    print(f"  regenerated recap:         {recap_reuse} tokens reused  "
          f"(re-summarizing invalidates the cache from the recap onward)")

    # The compaction record -- strategy=summarize (OBSERVABILITY.md). `referenced_later` (did a
    # kept-or-dropped identifier actually get used downstream?) is the quality flag Unit 11 adds.
    log_event(session_id, trace_id, 0, "compaction", strategy="summarize", trigger="soft",
              tokens_before=before, tokens_after=after, evicted=len(MIDDLE),
              fallback=summary is None, kept_ids=survived, lost_ids=lost, tail_blocked=blocked,
              cache_reuse_marker=marker_reuse, cache_reuse_recap=recap_reuse)


if __name__ == "__main__":
    main()
