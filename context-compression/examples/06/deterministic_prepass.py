"""
Unit 6 - Cheap Before Smart: the deterministic pre-pass.

Unit 5 isolated the middle to compress. But the middle is usually mostly ONE thing: a giant
tool output (a file read, a search dump). Before paying an LLM summarizer (Unit 4) to compress
it, run a free, deterministic pre-pass that replaces each large tool output with a one-line
SHAPE DESCRIPTOR. No model call, no model latency, no model cost -- and it often shrinks the middle enough
that the summarizer is never needed at all.

What this shows:
  - prepass(): replace any tool message >= THRESHOLD tokens with a one-line descriptor of its
    shape (JSON keys/length, or text lines/chars), PRESERVING tool_call_id so the tool pair
    stays valid.
  - errors are kept VERBATIM, even when large -- the model needs the real error text to recover.
  - the replacement is ALL-OR-NOTHING per output: never head/tail-truncate the middle of a tool
    output (that corrupts the file the model is reading -- the course's signature "when NOT to
    compress" case, continued in Unit 8).
  - a compaction record (strategy=prepass) and the cheap-before-smart loop: did the free pre-pass
    alone bring the prompt under budget, so the paid summarizer can be skipped?

Runs fully offline -- no endpoint needed.

    python context-compression/examples/06/deterministic_prepass.py
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common_context import estimate_tokens, log_event

BUDGET = 4000
THRESHOLD = 800   # collapse tool outputs at/above this many tokens (production's number)

# The head (system + first user message -- the task) is anchored, as in Units 3 and 5.
SYSTEM = {"role": "system", "content": "You are a coding assistant. Keep answers short and correct."}
TASK = {"role": "user", "content": "Find where retries are configured and raise the limit."}

# Two big tool outputs (a file read and a search dump) and one ERROR. Each is a real tool pair:
# an assistant message that REQUESTS the call, then the tool message that RETURNS it.
FILE = "def handler(req):\n    return do_work(req)\n" * 380          # ~2k tokens of source
SEARCH = json.dumps({"matches": [{"file": f"svc_{i}.py", "line": i} for i in range(140)]})  # big JSON
ERROR = "Error: ConnectionResetError on db-prod-1:5432 during pool warmup\n" + \
        "Traceback (most recent call last):\n" + "  File 'pool.py', line 88, in warmup\n" * 90  # large but load-bearing

MESSAGES = [
    SYSTEM, TASK,
    {"role": "assistant", "content": "Reading the service module.",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path": "service.py"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": FILE},
    {"role": "assistant", "content": "Searching for retry config.",
     "tool_calls": [{"id": "c2", "type": "function",
                     "function": {"name": "grep", "arguments": '{"q": "retries"}'}}]},
    {"role": "tool", "tool_call_id": "c2", "content": SEARCH},
    {"role": "assistant", "content": "Checking the connection pool.",
     "tool_calls": [{"id": "c3", "type": "function",
                     "function": {"name": "run", "arguments": '{"cmd": "python warm.py"}'}}]},
    {"role": "tool", "tool_call_id": "c3", "content": ERROR},   # an error -- kept verbatim
    {"role": "user", "content": "Thanks -- now bump the retry limit to 5."},
]


def _is_error(content: str) -> bool:
    """Keep errors verbatim: they are short relative to their value and the model needs the exact
    text to recover. (Production tracks an explicit error flag; here we detect the common shapes.)"""
    head = content.lstrip()
    return head.startswith("Error") or head.startswith("Traceback") or "Exception" in head[:40]


def _describe(content: str) -> str:
    """A one-line shape descriptor that replaces a large tool output. JSON -> keys/length; any other
    text -> line/char counts. The point is to keep the SHAPE (so the model knows what was there and
    can re-fetch it) while dropping the bytes."""
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            keys = ", ".join(list(obj)[:6])
            return f"<json object: {len(obj)} key(s): {keys}{', ...' if len(obj) > 6 else ''} (collapsed)>"
        if isinstance(obj, list):
            return f"<json array: {len(obj)} item(s) (collapsed)>"
    except (json.JSONDecodeError, TypeError):
        pass
    return f"<text output: {content.count(chr(10)) + 1} lines, {len(content)} chars (collapsed)>"


def prepass(messages, threshold=THRESHOLD):
    """Replace each large, non-error tool output with a one-line descriptor. Deterministic: no model
    call. The tool_call_id is preserved (we copy the message and only rewrite content), so the
    assistant-call/tool-result pair stays valid. Returns (new_messages, collapsed, tokens_saved)."""
    out, collapsed, saved = [], 0, 0
    for m in messages:
        content = m.get("content") or ""
        is_big_tool = m["role"] == "tool" and estimate_tokens(content) >= threshold
        if is_big_tool and not _is_error(content):
            new = dict(m)                                  # keeps tool_call_id
            new["content"] = _describe(content)
            saved += estimate_tokens(content) - estimate_tokens(new["content"])
            collapsed += 1
            out.append(new)
        else:
            out.append(m)                                  # small outputs and ERRORS pass through
    return out, collapsed, saved


def main():
    session_id, trace_id = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    before = estimate_tokens(MESSAGES)
    print(f"before: {len(MESSAGES)} messages, {before} tokens, {before / BUDGET:.0%} of budget "
          f"({'OVER' if before > BUDGET else 'under'})")

    kept, collapsed, saved = prepass(MESSAGES)
    after = estimate_tokens(kept)
    print(f"after pre-pass: {after} tokens, {after / BUDGET:.0%} of budget -- collapsed {collapsed} "
          f"large tool output(s), saved ~{saved} tokens (no model call)")

    print("\n--- what the pre-pass did, per tool output ---")
    for m in kept:
        if m["role"] == "tool":
            tag = "ERROR kept verbatim" if _is_error(m["content"]) else "collapsed"
            print(f"  {m['tool_call_id']}: {tag} -> {m['content'][:64].splitlines()[0]}")

    # The cheap-before-smart loop: did the free pre-pass alone fit the window? If so, the paid LLM
    # summarizer (Unit 4) is not needed this turn.
    summarizer_needed = after > BUDGET
    print(f"\nsummarizer needed after pre-pass? {summarizer_needed}  "
          f"({'still over -- run Unit 4 on the smaller middle' if summarizer_needed else 'no -- the free pass was enough'})")

    # The compaction record -- strategy=prepass (deterministic masking). `referenced_later` (was a
    # collapsed output paged back?) is the quality flag Unit 11 adds; offloading the bytes is Unit 8.
    log_event(session_id, trace_id, 0, "compaction", strategy="prepass", trigger="soft",
              tokens_before=before, tokens_after=after, collapsed=collapsed, tokens_saved=saved,
              summarizer_needed=summarizer_needed)


if __name__ == "__main__":
    main()
