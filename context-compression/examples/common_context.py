"""
Shared helpers for the Context Compression course examples.

Three small pieces the later units reuse:

  - estimate_tokens()      -- a cheap, no-tiktoken heuristic for budgeting math (Unit 1).
  - server_prompt_tokens() -- the EXACT count, from the server, for when precision matters.
  - log_event()            -- one joinable telemetry line (foundations Section 10 shape), the
                              start of this course's observability through-line.
  - describe/offload/page_in() -- a tiny content-addressed blob store (Unit 8): move big bytes
                              out of the window, keep a handle in it, page the EXACT bytes back.

Like the foundations examples/common.py, these scripts live in numbered folders and aren't an
importable package, so each script adds examples/ to the import path:

    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[1]))               # this course's examples/
    sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples/
    from common import get_client, MODEL
    from common_context import estimate_tokens, server_prompt_tokens, log_event, offload, page_in
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

# Rough bytes-per-token for English text. A rule of thumb, not a tokenizer: real tokens vary
# with language, code, whitespace, and the model's vocabulary. Good enough to decide "are we
# near the budget?"; when the exact number matters, ask the server (server_prompt_tokens).
_CHARS_PER_TOKEN = 4
# Each message also costs a few tokens of structure the content doesn't show: the role, and
# the delimiters the chat template wraps around it. A small fixed add-on per message.
_PER_MESSAGE_OVERHEAD = 4


def _content_str(content) -> str:
    """A message's content can be a plain string or a structured list (tool calls, parts).
    Stringify the structured case so we can size it the same way."""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False) if content is not None else ""


def estimate_tokens(messages_or_text) -> int:
    """Heuristic token count -- no tiktoken, no Hugging Face download (the course rule, §4).

    Pass a string, or a list of chat messages. For a message list we add a small per-message
    overhead for the role and chat-template framing. This is for BUDGETING (is the window
    filling up?), not billing; it is typically within ~10-20% of the true count.
    """
    if isinstance(messages_or_text, str):
        return len(messages_or_text) // _CHARS_PER_TOKEN
    total = 0
    for m in messages_or_text:
        total += len(_content_str(m.get("content"))) // _CHARS_PER_TOKEN + _PER_MESSAGE_OVERHEAD
    return total


def server_prompt_tokens(client, messages, model) -> int:
    """The EXACT prompt token count, straight from the server (§4: ask the server, don't guess).

    Make the smallest possible completion (max_tokens=1) and read back usage.prompt_tokens --
    the number of input tokens the model actually saw. Costs one cheap call; use it to check
    the heuristic, or when a decision must be precise.
    """
    r = client.chat.completions.create(model=model, messages=messages, max_tokens=1)
    return r.usage.prompt_tokens


def log_event(session_id, trace_id, step, operation, **fields) -> None:
    """Emit one structured, JOINABLE telemetry line (foundations Section 10 shape).

    The session_id / trace_id / step tuple ties every record in a run together, so a scattered
    pile of log lines becomes a reconstructable timeline. This course's observability
    through-line is built on this one helper: each unit logs the compaction it performs with
    the same shape. Lines go to stderr as JSONL, so program output on stdout stays clean.
    """
    line = {"session_id": session_id, "trace_id": trace_id, "step": step,
            "operation": operation, **fields}
    print(json.dumps(line, sort_keys=True), file=sys.stderr)


# --- A tiny content-addressed blob store (Unit 8: offloading and paging) -----------------------
#
# Offloading moves big bytes OUT of the context window and leaves a compact reference IN it, to be
# paged back on demand. Unlike summarizing (Unit 4) or truncating (Unit 6), offloading is
# LOSSLESS: page_in() returns the exact bytes that were offloaded. We make that a *verifiable*
# property by addressing each blob by the SHA-256 of its content -- the handle IS the integrity
# check, and identical bytes naturally share one handle (free dedup).

_DEFAULT_BLOB_DIR = Path(tempfile.gettempdir()) / "cc_blobs"


def describe(content) -> str:
    """A one-line SHAPE descriptor of a blob -- what it is and how big (Unit 6's idea).

    This is the human/model-readable half of the reference left in the window: enough to know
    what was offloaded and that it can be fetched again, without the bytes themselves.
    """
    text = content if isinstance(content, str) else _content_str(content)
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        return f"json object: {len(obj)} keys ({len(text)} chars)"
    if isinstance(obj, list):
        return f"json array: {len(obj)} items ({len(text)} chars)"
    return f"text: {text.count(chr(10)) + 1} lines, {len(text)} chars"


def offload(content, store=_DEFAULT_BLOB_DIR) -> str:
    """Write text content to a content-addressed file and return its handle (the SHA-256 hex).

    The handle -- not the bytes -- is what stays in the context window. We hash and store the raw
    UTF-8 bytes (str in, or structured message content stringified first), so the handle is exactly
    the SHA-256 of what is on disk. Writing is idempotent: identical bytes always produce the same
    handle and the same file, so re-offloading is free (content-addressing gives dedup for nothing).
    """
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else _content_str(content)
    raw = text.encode("utf-8")
    handle = hashlib.sha256(raw).hexdigest()
    blob = store / f"{handle}.txt"
    if not blob.exists():
        blob.write_bytes(raw)
    return handle


def page_in(handle, store=_DEFAULT_BLOB_DIR) -> str:
    """Read the bytes for a handle back into the window, VERIFYING integrity (re-hash == handle).

    This is the lossless half of the contract: the bytes read back are byte-for-byte identical to
    what went out (checked by re-hashing), and the text returned is the exact content offloaded. A
    mismatch means the store was corrupted or the wrong bytes were served -- raise, don't return a
    quietly-wrong blob (that would be exactly the read->edit hazard this unit warns about).
    """
    raw = (Path(store) / f"{handle}.txt").read_bytes()
    if hashlib.sha256(raw).hexdigest() != handle:
        raise ValueError(f"blob {handle[:12]} failed its integrity check -- bytes do not match handle")
    return raw.decode("utf-8")
