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
    dropped by role validation), and a compaction record + a quality loop (did the summary
    keep the host and ticket the tail asks for?).

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

# Identifiers the tail still depends on. A good summary keeps these; the fallback marker keeps none.
NEEDED = ["/etc/app/config.yaml", "db-prod-1", "5432", "FRE-512", "compute_rollup"]


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
    """Build the recap message. role=assistant, NOT system: many stacks (and a production
    role-fixer) keep only the FIRST system message and drop any later system-role message, which
    would silently delete the recap. assistant survives in the middle of a transcript."""
    return {"role": "assistant", "content": summary if summary else MARKER_TEXT}


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

    # The quality loop: did the recap keep the identifiers the TAIL still asks for?
    survived = [s for s in NEEDED if s in kept[2]["content"]]
    lost = [s for s in NEEDED if s not in kept[2]["content"]]
    print(f"\nidentifiers kept: {survived or 'NONE'}")
    if lost:
        print(f"identifiers lost: {lost}  <- the tail's questions about these can no longer be answered")

    # The compaction record -- strategy=summarize (OBSERVABILITY.md). `referenced_later` (did a
    # kept-or-dropped identifier actually get used downstream?) is the quality flag Unit 11 adds.
    log_event(session_id, trace_id, 0, "compaction", strategy="summarize", trigger="soft",
              tokens_before=before, tokens_after=after, evicted=len(MIDDLE),
              fallback=summary is None, kept_ids=survived, lost_ids=lost)


if __name__ == "__main__":
    main()
