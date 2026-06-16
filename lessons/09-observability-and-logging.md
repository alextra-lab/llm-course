# Section 9 — Observability & Logging

**Goal:** make your LLM calls *visible*. You'll write a small wrapper that emits one
structured log record per call, capturing the telemetry the API already hands back — then
use those records to debug, monitor latency, and account for tokens.

**Where this fits:** Section 2 introduced `usage` and `finish_reason`; Section 8 added
errors and retries. This lesson collects all of it into one record per call — the data
you'll need to compute cost in Section 10 and to understand what your app is doing.

---

## You already have the telemetry

You don't need a special platform to start. Every call gives you, for free:

- **`response.usage`** *(object)* — `prompt_tokens` *(int)*, `completion_tokens` *(int)*,
  `total_tokens` *(int)*, and (for our reasoning model)
  `completion_tokens_details.reasoning_tokens` *(int)*.
- **`response.choices[0].finish_reason`** *(str)* — track it and you'll *see* truncation
  problems instead of guessing.
- **`response.model`** *(str)* and **`response.system_fingerprint`** *(str)* — exactly
  what answered, and a marker for the underlying config.
- **`response.id`** *(str)* — for correlating a log line to a specific call.
- **Response headers** — often a **request id** (`x-request-id`, a `str`) and rate-limit
  info. What you quote in a support ticket.
- **Latency** *(int, milliseconds)* — not in the response; you measure it with a clock
  around the call.

The job is just: capture these consistently, in a form you can search and aggregate.

---

## Write a logging wrapper

Prefer **structured logs** — one JSON object per line ("JSONL") — over `print`. They're
readable by humans *and* trivially parsed by tools. Create **`work/logged.py`**. It wraps
a call, times it, and uses `with_raw_response` so it can also read the HTTP headers:

```python
import json, logging, time
from common import get_client, MODEL

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("llm")
client = get_client()

def logged_chat(**kwargs):
    start = time.perf_counter()
    raw = client.chat.completions.with_raw_response.create(**kwargs)   # keeps headers
    latency_ms = round((time.perf_counter() - start) * 1000)

    completion = raw.parse()                                            # typed object
    usage = completion.usage
    details = getattr(usage, "completion_tokens_details", None)

    log.info(json.dumps({
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
    }))
    return completion

response = logged_chat(
    model=MODEL,
    messages=[{"role": "user", "content": "Reply with a single friendly word."}],
)
print("\nanswer:", response.choices[0].message.content)
```

Run it:

```bash
python work/logged.py
```

You'll get one line like:

```json
{"event": "chat_completion", "model": "openai/gpt-oss-120b", "id": "chatcmpl-...", "finish_reason": "stop", "prompt_tokens": 18, "completion_tokens": 3, "latency_ms": 240, "system_fingerprint": "fp_...", "request_id": "..."}
```

That single line answers most "what happened?" questions later. *(Reference:
[`examples/09/log_calls.py`](../examples/09/log_calls.py).)*

---

## What the logs let you do

Once every call emits a record like that, you can:

- **Debug a specific failure** — quote the `request_id`/`id` to find the exact call.
- **Track cost** — sum `prompt_tokens` and `completion_tokens` over time (Section 10 turns
  these into dollars).
- **Watch latency** — alert if `latency_ms` creeps up; compute throughput as
  `completion_tokens / (latency_ms / 1000)`.
- **Catch regressions** — a rising rate of `finish_reason == "length"` means you're
  truncating; a changed `system_fingerprint` may explain a sudden behavior shift.

> **Log responsibly.** It's tempting to log full prompts and completions. Be deliberate:
> they can contain personal data and secrets. Decide what you store, for how long, and
> redact what you must. The *metadata* above is usually safe and high-value; the
> *content* needs a policy.

> **Server-side metrics, if you have access.** A vLLM server also exposes Prometheus
> metrics at `/metrics` (queue depth, tokens/sec, GPU use) — operations telemetry for
> whoever runs the server. Our client-side JSONL is what *you* control as an API consumer.

---

> **Security:** Logs are forever and widely read. Log what you need to audit, but never log API keys, credentials, or user PII — redact at the source.

## Challenges

1. **Make a log file.** Run `python work/logged.py >> calls.jsonl` a few times, then read
   it (`cat calls.jsonl`, or `jq . calls.jsonl` if you have `jq`). *Success:* a growing
   JSONL file of records.
2. **Add throughput.** Add a `tokens_per_sec` field computed from `completion_tokens` and
   `latency_ms`. *Success:* it appears in the record.
3. **Surface reasoning.** Confirm `reasoning_tokens` is populated for a hard prompt and
   `null` for a trivial one. *Success:* you can explain the difference from the logs alone.
4. **Find the slow ones.** Generate several records, then filter for `latency_ms > 500`.
   *Success:* you can tell whether the slow calls were the ones doing lots of reasoning.

---

## Recap

- The API already gives rich telemetry: `usage`, `finish_reason`, `model`,
  `system_fingerprint`, `id`, response headers, and (via your clock) latency.
- Emit **one structured JSON record per call**; use `with_raw_response` to capture headers
  like the request id.
- Use those records to debug, track cost, monitor latency/throughput, and spot truncation
  or config changes.
- Be deliberate about logging prompt/response **content** (privacy, secrets).

## Next

**Section 10 — Cost, Pricing & Prompt Caching:** you'll turn the token counts you've been
logging into money, then exploit **prompt caching** to make repeated prefixes cheaper and
faster — the capstone of the foundations arc.
