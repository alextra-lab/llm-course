"""
Section 9 - Observability: log one structured record per call.

We wrap a normal call, measure how long it took, and emit a single JSON line
capturing the telemetry the API already gives us: which model answered, the
finish_reason, token usage (including reasoning tokens), latency, the response
id, and the request id from the response headers.

We also stamp each record with a session_id / trace_id / step -- the "joining"
ids you assign yourself so related calls can be tied back together (one
conversation, or one agent run). The server's id/request_id identify a single
call; these tie many calls into one story. See Section 22 for where this pays off.

JSON-per-line ("JSONL") logs are easy for both humans and tools to read.

    python examples/09/log_calls.py
"""

import json
import logging
import sys
import time
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

# Structured logs go to stdout (so you can redirect them to a file); the
# human-readable answer goes to stderr. Keep machine output and chatter separate.
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
log = logging.getLogger("llm")

client = get_client()


def logged_chat(session_id, trace_id, step, **kwargs):
    start = time.perf_counter()
    # with_raw_response also gives us the HTTP headers (e.g. the request id).
    raw = client.chat.completions.with_raw_response.create(**kwargs)
    latency_ms = round((time.perf_counter() - start) * 1000)

    completion = raw.parse()  # the usual typed ChatCompletion
    usage = completion.usage
    details = getattr(usage, "completion_tokens_details", None)

    record = {
        "event": "chat_completion",
        "session_id": session_id,  # the whole conversation / user session
        "trace_id": trace_id,      # one logical operation (e.g. an agent run)
        "step": step,              # ordering within the trace
        "model": completion.model,
        "id": completion.id,
        "finish_reason": completion.choices[0].finish_reason,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "reasoning_tokens": getattr(details, "reasoning_tokens", None) if details else None,
        "total_tokens": usage.total_tokens,
        "latency_ms": latency_ms,
        "system_fingerprint": getattr(completion, "system_fingerprint", None),
        "request_id": raw.headers.get("x-request-id"),
    }
    log.info(json.dumps(record))
    return completion


session_id = uuid.uuid4().hex[:8]  # reuse this across a whole conversation
trace_id = uuid.uuid4().hex[:8]    # one per logical operation (here, a single call)
response = logged_chat(
    session_id, trace_id, 0,
    model=MODEL,
    messages=[{"role": "user", "content": "Reply with a single friendly word."}],
)
print("\nanswer:", response.choices[0].message.content, file=sys.stderr)
