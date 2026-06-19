---
title: 'Robustness: Errors, Retries, Rate Limits, Timeouts'
linkTitle: '9. Robustness: Errors, Retries, Rate Limits, Timeouts'
weight: 9
---

**Goal:** turn a script that works on a good day into one that survives a bad one. You'll
write the error-handling ladder and a retry-with-backoff helper, and learn which failures
to retry and which to fix.

**Where this fits:** everything so far assumed the call succeeds. Real networks drop, real
servers get busy, real requests have bugs. This small layer separates a demo from
something you'd run unattended — and it sets up clean logging in Section 10.

---

## Three kinds of failure

When a call fails, it's almost always one of:

1. **Client errors (4xx)** — *your request was wrong*. Bad key, malformed body, a
   parameter out of range, a missing model. Retrying changes nothing — fix the request.
2. **Transient errors** — *temporary trouble*. Rate-limited (429), a brief server hiccup
   (5xx), a dropped/timed-out connection. The same request will likely succeed if you
   wait and retry.
3. **Timeouts** — the call took too long and you gave up.

The whole strategy follows: **retry transient failures, fix client errors.**

---

## The exception ladder

The `openai` SDK maps these to typed exceptions:

| Exception | HTTP | Retry? | Meaning |
|---|---|---|---|
| `BadRequestError` | 400 | ❌ | Malformed request — fix it. |
| `AuthenticationError` | 401 | ❌ | Bad/missing API key. |
| `NotFoundError` | 404 | ❌ | e.g. unknown model id. |
| `RateLimitError` | 429 | ✅ | Slow down, then retry. |
| `InternalServerError` | 5xx | ✅ | Server-side hiccup. |
| `APIConnectionError` | — | ✅ | Network/connection problem. |
| `APITimeoutError` | — | ✅ | Request exceeded the timeout. |

Write the ladder. Create **`work/errors.py`** — it triggers a client error on purpose
(an empty `messages` list) and catches most-specific-first:

```python
from openai import (
    APIConnectionError, APITimeoutError, AuthenticationError,
    BadRequestError, InternalServerError, RateLimitError,
)
from common import get_client, MODEL

client = get_client()

try:
    client.chat.completions.create(model=MODEL, messages=[])   # invalid -> 4xx
except BadRequestError as err:
    print("BadRequestError (400): fix the request; do NOT retry.\n ", err)
except AuthenticationError:
    print("AuthenticationError (401): bad/missing API key.")
except RateLimitError:
    print("RateLimitError (429): back off and retry.")
except (APIConnectionError, APITimeoutError, InternalServerError) as err:
    print(f"Transient ({type(err).__name__}): retry with backoff.")
```

```bash
python work/errors.py
```

*(Reference: [`examples/09/handling_errors.py`](../examples/09/handling_errors.py).)*

---

## Retry the right way: exponential backoff with jitter

Don't retry in a tight loop — you'll hammer a struggling server. Wait, and **grow the
wait** each time (1s, 2s, 4s, …), plus a little **jitter** (randomness) so many clients
don't retry in lockstep. Create **`work/retry.py`**:

```python
import random, time
from openai import (APIConnectionError, APITimeoutError,
                    InternalServerError, RateLimitError)
from common import get_client, MODEL

client = get_client()
TRANSIENT = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)

def with_retries(make_call, attempts=5):
    for attempt in range(attempts):
        try:
            return make_call()
        except TRANSIENT as err:
            if attempt == attempts - 1:
                raise                                  # out of tries
            delay = min(2 ** attempt, 30) + random.uniform(0, 1)   # backoff + jitter
            print(f"  transient {type(err).__name__}; retrying in {delay:.1f}s")
            time.sleep(delay)

response = with_retries(lambda: client.chat.completions.create(
    model=MODEL, messages=[{"role": "user", "content": "Say hello."}],
))
print("succeeded:", response.choices[0].message.content)
```

```bash
python work/retry.py
```

Note what it does **not** catch: `BadRequestError` and friends sail straight through,
because retrying them is pointless.

> **Let the SDK do it.** The client has this built in:
> `OpenAI(max_retries=5, timeout=20.0)` retries transient errors with backoff and
> enforces a per-request timeout. In real code you'll often just configure that — we
> wrote it by hand so "exponential backoff" isn't a mystery phrase. *(Reference:
> [`examples/09/retries.py`](../examples/09/retries.py).)*

---

## Rate limits and timeouts, specifically

- **Rate limits (429).** Hosted endpoints cap requests/tokens per minute; exceed it and
  you get a 429, often with a `Retry-After` header. Backoff handles it; for steady high
  volume, also throttle your own send rate.
- **Timeouts.** Always set one (`timeout=` on the client or per call). Without it, a stuck
  connection hangs forever. Reasoning requests (Section 6) can legitimately take a while —
  give them headroom, but not infinity.

> **Retrying costs tokens.** Retrying an LLM call is safe to repeat (it doesn't mutate
> server state), but every attempt that reaches the model **costs tokens**. Cap your
> attempts, and never retry client errors.

---

> **Security:** Fail closed and stay quiet about internals: on error, return a safe message, not a stack trace or raw exception — those leak file paths, versions, and sometimes secrets to whoever triggered them.

## Challenges

1. **Provoke real errors.** Run a script with `OPENAI_API_KEY=wrong` (a 401) and with
   `MODEL=nope` (a 404). *Success:* both are caught and *not* retried.
2. **Watch backoff grow.** Wrap a call so it raises `RateLimitError` on its first two
   attempts, then succeeds, and run it through `with_retries`. *Success:* you see delays
   grow ~1s → 2s → 4s.
3. **Tight timeout.** Build `OpenAI(timeout=0.001)` and call it. *Success:* you can name
   the exception you get and say whether it's transient.

---

## Recap

- Failures are **client errors (fix)**, **transient errors (retry)**, or **timeouts**.
- Catch typed exceptions; retry only `RateLimitError`, `InternalServerError`,
  `APIConnectionError`, `APITimeoutError`.
- Retry with **exponential backoff + jitter**, capped — or let the SDK do it via
  `max_retries`/`timeout`.
- Always set a **timeout**; respect **rate limits**; remember each retry costs tokens.

## Next

**Section 10 — Observability & Logging:** now that calls can fail and recover, you'll make
the system *visible* — logging the telemetry the API already gives you (usage,
`finish_reason`, latency, request ids).
