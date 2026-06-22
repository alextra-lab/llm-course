---
title: 'Blocking vs Streaming'
linkTitle: '8. Blocking vs Streaming'
weight: 8
---

**Goal:** learn the two ways to receive a response — **blocking** (wait for the whole
thing) and **streaming** (receive it token by token) — by building streaming yourself,
first as the raw protocol, then via the SDK. By the end you'll know exactly what a stream
is on the wire and when each mode is right.

**Where this fits:** every call in Sections 1–7 was blocking — the simple default.
Streaming is what makes chat interfaces feel responsive, and building it removes the last
bit of mystery about how these APIs work.

---

## The two modes

- **Blocking** — one request, one complete response, after the model has generated all of
  it. Simple. Right when your code needs the *whole* answer before it can act (parsing,
  decisions, batch jobs).
- **Streaming** — the server sends the answer *as it's produced*, in small chunks. The
  first words appear almost immediately. Same total content, delivered incrementally.

Streaming doesn't make generation faster — it changes *when you see it*, which greatly
improves **perceived** latency for a human watching the text appear.

---

## Build the raw stream first

When you add `"stream": true`, the server replies with **Server-Sent Events (SSE)**: it
holds the connection open and pushes lines beginning with `data: `, each carrying a small
JSON chunk with the next **delta** (piece of content). The stream ends with a literal
`data: [DONE]`.

Build it by hand with `requests`. Create **`work/stream_raw.py`**:

```python
import json
import os
import requests

base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
api_key = os.environ["OPENAI_API_KEY"]
model = os.environ["MODEL"]

resp = requests.post(
    f"{base_url}/chat/completions",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    json={
        "model": model,
        "messages": [{"role": "user", "content": "Count from 1 to 10 slowly."}],
        "stream": True,          # <-- the switch that changes everything
    },
    stream=True,                 # tell requests not to buffer the whole body
    timeout=60,
)
resp.raise_for_status()

for line in resp.iter_lines():
    if not line:
        continue
    line = line.decode("utf-8")
    if not line.startswith("data: "):
        continue
    payload = line[len("data: "):]
    if payload == "[DONE]":
        break
    chunk = json.loads(payload)
    piece = chunk["choices"][0]["delta"].get("content")
    if piece:
        print(piece, end="", flush=True)   # print as it arrives, no newline
print()
```

Run it and watch the count appear piece by piece:

```bash
python work/stream_raw.py
```

The key difference from Section 2: a streamed chunk carries
**`chunk.choices[0].delta`** *(object)* — with new text in
`chunk.choices[0].delta.content` *(str)* — not a complete `response.choices[0].message`.
The full answer is the deltas concatenated in order. That's the entire trick.
*(Reference: [`examples/08/raw_sse.py`](../examples/08/raw_sse.py).)*

---

## Now the SDK version

The SDK hides the SSE parsing — set `stream=True` and iterate. Create
**`work/stream_sdk.py`**:

```python
from common import get_client, MODEL

client = get_client()

stream = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "Count from 1 to 10 slowly."}],
    stream=True,
    stream_options={"include_usage": True},   # ask for a final usage chunk
)

pieces, usage = [], None
for chunk in stream:
    if chunk.usage is not None:
        usage = chunk.usage                   # arrives in the final chunk
    if chunk.choices and chunk.choices[0].delta.content:
        piece = chunk.choices[0].delta.content
        pieces.append(piece)
        print(piece, end="", flush=True)

print("\n\nreassembled:", len("".join(pieces)), "chars | usage:", usage)
```

```bash
python work/stream_sdk.py
```

> **Streaming usually drops `usage`.** Because the response is delivered in pieces, a
> streamed call omits the `usage` block by default. Ask for it with
> `stream_options={"include_usage": True}` and it arrives in a final chunk (the one where
> `choices` is empty) — you need this for cost accounting (Section 11) on streams.

> **Reasoning streams too.** With `gpt-oss-120b`, thinking can arrive as
> `chunk.choices[0].delta.reasoning_content` chunks before the answer's `delta.content`.
> If you're showing a stream to a user, you typically display only the `content` deltas.
> *(Reference: [`examples/08/sdk_stream.py`](../examples/08/sdk_stream.py).)*

---

## When to use which

**Stream when:** a human is watching and you want it to feel instant (chat UIs), or the
answer is long and you'd rather show progress than a spinner.

**Block when:** your code needs the complete answer before acting — especially
**structured output** (Section 7): you can't validate JSON against a schema until you've
reassembled all of it. Also for background/batch work, and when you want the simplest
code and the full response object in hand.

A common pattern: stream to the user for responsiveness, **accumulate** the deltas into
the full text, then parse/validate the assembled result.

---

> **Security:** Run your guardrails on the *assembled* message, not on partial chunks. A filter that sees half a sentence can be fooled by the other half.

## Challenges

1. **Inspect a chunk.** In `work/stream_raw.py`, `print(chunk)` for each event. *Success:*
   you can point to the `delta` and find the `finish_reason` on the final content chunk.
2. **Prove `include_usage`.** Remove `stream_options` from `work/stream_sdk.py`. *Success:*
   the `usage` is `None`; add it back and it returns.
3. **Stream then validate.** Combine with Section 7: stream a JSON answer, reassemble the
   deltas, and only then `model_validate_json` the full string. *Success:* you get a
   validated object built from a stream.

---

## Recap

- **Blocking** = one complete response; **streaming** = incremental `delta` chunks via SSE
  (`data: ...` lines ending in `[DONE]`).
- Streaming improves *perceived* latency; it doesn't speed up generation.
- Streamed chunks carry `chunk.choices[0].delta`, not `message`; reassemble deltas for the
  full answer.
- Streaming omits `usage` unless you pass `stream_options={"include_usage": True}`.
- Use blocking when you need the whole answer at once (e.g. structured output).

## Next

**[Section 9 — Robustness](09-robustness.md):** real networks and servers fail. You'll handle errors, rate
limits, timeouts, and retries so a script survives a bad day.
