"""
Unit 8 - Offloading & Paging: Gist Memory.

Some artifacts are too big to keep in the window and too important to summarize (a file the agent
must edit, an API payload it needs verbatim later). The third option, beyond drop (Unit 3) and
summarize (Unit 4): OFFLOAD the bytes to a store, leave a compact REFERENCE in the window, and
PAGE the exact bytes back on demand. Unlike summarizing or truncating, offloading is LOSSLESS.

What this shows:
  - offload(): write a giant tool output to a content-addressed blob (SHA-256), keep only a
    one-line reference (Unit 6's shape descriptor + the handle) in the window. The meter shows the
    window reclaimed.
  - page_in(): read the exact bytes back, VERIFIED byte-identical by re-hashing -- this is why
    offloading is the SAFE alternative to Unit 6's parked, corrupting tool-result truncation.
  - the read->edit hazard: act on the bytes you paged in, never on the stale gist. Content-
    addressing means a swapped/corrupt blob fails its check instead of poisoning a later turn.
  - a compaction record (strategy=offload) and a page_in event (integrity_ok), joinable on
    trace_id so paging churn and the lossless guarantee are both visible.

The offline core (offload -> reference -> page_in + byte-identity) always runs. The AGENT demo is
OPT-IN: set OPENAI_BASE_URL (your foundations .env) and the agent pages the bytes back itself with
a read_blob tool call; offline that section skips cleanly.

    python context-compression/examples/08/offloading_and_paging.py
    python context-compression/examples/08/offloading_and_paging.py 2>> run.jsonl
"""

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # context-compression/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common import get_client, MODEL
from common_context import estimate_tokens, log_event, describe, offload, page_in

BUDGET = 4000

# A giant tool output (a "read_file" result) with one specific fact buried in the middle -- the
# kind of thing too big to keep but needed exactly later.
SECRET_LINE = "CONFIG: retry_limit=7, db_host=db-prod-1, db_port=5432"


def make_big_file():
    lines = [f"line {i:03d}: configuration and logging boilerplate, padding text" for i in range(760)]
    lines.insert(400, SECRET_LINE)              # the one line a later turn will actually need
    return "\n".join(lines)


def reference(handle, content):
    """The compact reference left in the window: Unit 6's shape descriptor + the handle."""
    return f'[offloaded: {describe(content)} | handle={handle} | page in with read_blob(handle)]'


HEAD = [{"role": "system", "content": "You are a coding assistant. Cite values exactly."},
        {"role": "user", "content": "Read config.py, then tell me the production db port when I ask."}]
TAIL = [{"role": "assistant", "content": "Read the file."},
        {"role": "user", "content": "ok"}]


def read_blob(handle: str, known_handle: str) -> tuple[str, bool]:
    """The agent's tool: page the full bytes for an offloaded handle back into context.
    Tolerates a model that echoes a shortened handle by falling back to the known one."""
    h = handle if len(handle) == 64 else known_handle
    try:
        return page_in(h), True
    except Exception:
        return "ERROR: blob not found or failed integrity check", False


def run_agent(client, messages_offloaded, handle, sess, trace, start_step, max_steps=4):
    """OPT-IN: an agent that pages the bytes back itself with a read_blob tool call (§23).

    A bounded tool-use loop, following the foundations §23 pattern: call the model, run any tool
    calls it makes, feed the results back, repeat until it answers or we hit max_steps.
    """
    tools = [{"type": "function", "function": {
        "name": "read_blob",
        "description": "Page the full bytes for an offloaded reference back into context.",
        "parameters": {"type": "object",
                       "properties": {"handle": {"type": "string", "description": "the blob handle"}},
                       "required": ["handle"]}}}]
    convo = messages_offloaded + [
        {"role": "user", "content": "What is the production db_port? Page in the offloaded file if you need it."}]
    step = start_step
    for _ in range(max_steps):
        r = client.chat.completions.create(model=MODEL, messages=convo, tools=tools)
        msg = r.choices[0].message
        if not msg.tool_calls:
            print(f"  agent answer: {msg.content}")
            return
        convo.append({"role": "assistant", "content": msg.content,
                      "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            content, ok = read_blob(args.get("handle", ""), handle)
            log_event(sess, trace, step, "page_in", handle=handle, bytes_returned=len(content),
                      integrity_ok=ok, via="tool_call")
            step += 1
            print(f"  agent called read_blob -> paged in {len(content)} chars (integrity_ok={ok})")
            convo.append({"role": "tool", "tool_call_id": tc.id, "content": content})
    print("  (stopped: reached max_steps)")


def main():
    sess, trace = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    big = make_big_file()

    # Before: the giant tool output sits in the window.
    full = HEAD + [{"role": "tool", "tool_call_id": "t1", "content": big}] + TAIL
    before = estimate_tokens(full)

    # Offload it: the bytes go to the store, a one-line reference stays in the window.
    handle = offload(big)
    ref = reference(handle, big)
    offloaded = HEAD + [{"role": "tool", "tool_call_id": "t1", "content": ref}] + TAIL
    after = estimate_tokens(offloaded)
    log_event(sess, trace, 0, "compaction", strategy="offload", tokens_before=before,
              tokens_after=after, bytes_offloaded=len(big), handle=handle)

    print(f"offloading  (budget {BUDGET} tokens)")
    print(f"  before:  {before:5d} tok {before / BUDGET:4.0%} of budget  (giant tool output in the window)")
    print(f"  after:   {after:5d} tok {after / BUDGET:4.0%} of budget  -- offloaded {len(big)} bytes, kept a {len(ref)}-char reference")
    print(f"  ref in context: {ref[:78]}…\n")

    # Page it back: byte-identical, proven by re-hashing.
    paged = page_in(handle)
    ok = paged == big
    log_event(sess, trace, 1, "page_in", handle=handle, bytes_returned=len(paged), integrity_ok=ok)
    print(f"  page_in: returned {len(paged)} chars, byte-identical to the original: {ok}")
    print(f"  (summarizing would have lost '{SECRET_LINE}'; offloading returns it exactly)\n")

    have_endpoint = os.environ.get("OPENAI_BASE_URL") and os.environ.get("OPENAI_API_KEY")
    if not have_endpoint:
        print("  (OPENAI_BASE_URL/OPENAI_API_KEY not set -- skipping the agent paging demo; the offline core above ran)")
        return
    print("  agent demo (endpoint set): asking for a value only in the offloaded file --")
    run_agent(get_client(), offloaded, handle, sess, trace, start_step=2)


if __name__ == "__main__":
    main()
