"""
Section 9 - Observability: log one structured record per call.

We wrap a normal call, measure how long it took, and emit a single JSON line
capturing the telemetry the API already gives us: which model answered, the
finish_reason, token usage (including reasoning tokens), latency, the response
id, and the request id from the response headers.

JSON-per-line ("JSONL") logs are easy for both humans and tools to read.

    python examples/09/log_calls.py
"""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("llm")

client = get_client()


def logged_chat(**kwargs):
    start = time.perf_counter()
    # with_raw_response also gives us the HTTP headers (e.g. the request id).
    raw = client.chat.completions.with_raw_response.create(**kwargs)
    latency_ms = round((time.perf_counter() - start) * 1000)

    completion = raw.parse()  # the usual typed ChatCompletion
    usage = completion.usage
    details = getattr(usage, "completion_tokens_details", None)

    record = {
        "event": "chat_completion",
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


response = logged_chat(
    model=MODEL,
    messages=[{"role": "user", "content": "Reply with a single friendly word."}],
)
print("\nanswer:", response.choices[0].message.content)
