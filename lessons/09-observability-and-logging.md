---
title: 'Observability & Logging'
linkTitle: '9. Observability & Logging'
weight: 9
---

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
- **`response.id`** *(str)* — a server-assigned id for *one* call; the basis for
  correlating a log line to that exact request. (Tying *many* calls together into a
  conversation or an agent run takes ids *you* assign — see "Make your logs joinable.")
- **Response headers** — often a **request id** (`x-request-id`, a `str`) and rate-limit
  info. What you quote in a support ticket.
- **Latency** *(int, milliseconds)* — not in the response; you measure it with a clock
  around the call.

The job is just: capture these consistently, in a form you can search and aggregate.

---

## Write a logging wrapper

Prefer **structured logs** — one JSON object per line ("JSONL") — over `print`. They're
readable by humans *and* trivially parsed by tools. Create **`work/logged.py`**. It wraps
a call, times it, and uses `with_raw_response` so it can also read the HTTP headers.
The first three arguments — `session_id`, `trace_id`, and `step` — are the *joining* ids
we explain right after:

```python
import json, logging, sys, time, uuid
from common import get_client, MODEL

# Structured logs go to stdout (so you can redirect them to a file); the
# human-readable answer goes to stderr. Keep machine output and chatter separate.
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
log = logging.getLogger("llm")
client = get_client()

def logged_chat(session_id, trace_id, step, **kwargs):
    start = time.perf_counter()
    raw = client.chat.completions.with_raw_response.create(**kwargs)   # keeps headers
    latency_ms = round((time.perf_counter() - start) * 1000)

    completion = raw.parse()                                            # typed object
    usage = completion.usage
    details = getattr(usage, "completion_tokens_details", None)

    log.info(json.dumps({
        "event": "chat_completion",
        "session_id": session_id,   # the whole conversation / user session
        "trace_id": trace_id,       # one logical operation (e.g. an agent run)
        "step": step,               # ordering within the trace
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

session_id = uuid.uuid4().hex[:8]   # reuse this across a whole conversation
trace_id = uuid.uuid4().hex[:8]     # one per logical operation (here, a single call)
response = logged_chat(
    session_id, trace_id, 0,
    model=MODEL,
    messages=[{"role": "user", "content": "Reply with a single friendly word."}],
)
print("\nanswer:", response.choices[0].message.content, file=sys.stderr)
```

Run it:

```bash
python work/logged.py
```

You'll get one line like:

```json
{"event": "chat_completion", "session_id": "9f3a2b1c", "trace_id": "7c1d4e8a", "step": 0, "model": "openai/gpt-oss-120b", "id": "chatcmpl-...", "finish_reason": "stop", "prompt_tokens": 18, "completion_tokens": 3, "latency_ms": 240, "system_fingerprint": "fp_...", "request_id": "..."}
```

That single line answers most "what happened?" questions later. *(Reference:
[`examples/09/log_calls.py`](../examples/09/log_calls.py).)*

---

## Make your logs joinable

One record per call lets you find *a* call. But the questions that matter later span many
calls: *"show me everything in this user's conversation"* or *"replay this agent run —
every model call and tool result, in order."* You can have rich per-call logs and still
not answer those, because the records share no key. That's not a missing-log problem; it's
a **missing-foreign-key problem.**

The server already stamps each call with `id` and `request_id` — but those identify *one
request*, not the conversation or run it belongs to. The joining ids are ones **you**
assign and thread through every record:

- **`session_id`** — the whole conversation / user session. Stable across many turns (the
  message history from Section 12 is one session).
- **`trace_id`** — one logical operation: a single agent run, which may make a dozen model
  calls and tool executions. A fresh id per run.
- **`step`** — an integer sequence ordering events within a trace, so a multi-step run
  reads back in order.

They nest: one **session** holds many **traces** (runs); one trace holds many **steps**;
each step is a model call or a tool execution. Stamp *every* record with the same
`trace_id` and a scattered pile of log lines becomes a reconstructable timeline:

```bash
grep '"trace_id": "7c1d4e8a"' calls.jsonl   # the whole run, in order
```

Two disciplines make this actually hold up:

- **Stamp identity at the point you write the record — not "where convenient."** An
  *optional* id is one you'll discover is missing exactly when you need it (debugging a
  bad run at 2am). If a log line can be emitted without the tuple, you don't actually know
  your data is joinable.
- **Every emit site uses the same shape.** A model call and a tool result that carry the
  *same* `trace_id`/`step` join up; if only model calls are stamped, the tool half of the
  story is invisible. (Section 22 makes both halves emit the tuple.)

Production tracing tools — OpenTelemetry, and hosted ones like LangSmith — formalize this
with **spans**: each unit of work gets a `span_id` and a `parent_span_id`, so sub-steps
nest into a tree rather than a flat sequence. That's the same idea as `trace_id` + `step`,
with parent/child links added. Start with the flat ids; reach for spans when runs get deep
enough that ordering alone isn't enough.

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
5. **Join a trace.** Generate one `trace_id`, make two `logged_chat` calls with it (steps
   `0` and `1`), and capture the records with `python work/logged.py >> calls.jsonl` (the
   JSONL is on stdout). Then `grep '"trace_id": "..."' calls.jsonl`. *Success:* both lines
   come back in `step` order — one run reconstructed from a shared key.

---

## Recap

- The API already gives rich telemetry: `usage`, `finish_reason`, `model`,
  `system_fingerprint`, `id`, response headers, and (via your clock) latency.
- Emit **one structured JSON record per call**; use `with_raw_response` to capture headers
  like the request id.
- Stamp every record with a **`session_id` / `trace_id` / `step`** so related calls
  *join* — the foundation for debugging multi-step agents (Section 22). Server ids
  (`id`, `request_id`) identify one call; these tie the whole run together.
- Use those records to debug, track cost, monitor latency/throughput, and spot truncation
  or config changes.
- Be deliberate about logging prompt/response **content** (privacy, secrets).

## Next

**Section 10 — Cost, Pricing & Prompt Caching:** you'll turn the token counts you've been
logging into money, then exploit **prompt caching** to make repeated prefixes cheaper and
faster — the capstone of the foundations arc.
