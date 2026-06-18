"""
Shared helpers for the Context Compression course examples.

Three small pieces the later units reuse:

  - estimate_tokens()      -- a cheap, no-tiktoken heuristic for budgeting math (Unit 1).
  - server_prompt_tokens() -- the EXACT count, from the server, for when precision matters.
  - log_event()            -- one joinable telemetry line (foundations Section 9 shape), the
                              start of this course's observability through-line.

Like the foundations examples/common.py, these scripts live in numbered folders and aren't an
importable package, so each script adds examples/ to the import path:

    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[1]))               # this course's examples/
    sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples/
    from common import get_client, MODEL
    from common_context import estimate_tokens, server_prompt_tokens, log_event

(A small on-disk blob store joins this file in Unit 8, for offloading and paging.)
"""

import json
import sys

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
    """Heuristic token count -- no tiktoken, no Hugging Face download (the course rule, §3).

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
    """The EXACT prompt token count, straight from the server (§3: ask the server, don't guess).

    Make the smallest possible completion (max_tokens=1) and read back usage.prompt_tokens --
    the number of input tokens the model actually saw. Costs one cheap call; use it to check
    the heuristic, or when a decision must be precise.
    """
    r = client.chat.completions.create(model=model, messages=messages, max_tokens=1)
    return r.usage.prompt_tokens


def log_event(session_id, trace_id, step, operation, **fields) -> None:
    """Emit one structured, JOINABLE telemetry line (foundations Section 9 shape).

    The session_id / trace_id / step tuple ties every record in a run together, so a scattered
    pile of log lines becomes a reconstructable timeline. This course's observability
    through-line is built on this one helper: each unit logs the compaction it performs with
    the same shape. Lines go to stderr as JSONL, so program output on stdout stays clean.
    """
    line = {"session_id": session_id, "trace_id": trace_id, "step": step,
            "operation": operation, **fields}
    print(json.dumps(line, sort_keys=True), file=sys.stderr)
