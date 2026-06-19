---
title: 'Hello World'
linkTitle: '1. Hello World'
weight: 1
---

**Goal:** *write*, by hand, your first two programs that talk to a language model — one
using raw HTTP, one using the official SDK — and build the right mental model of what's
happening on the wire. You'll finish understanding the single most important idea in the
course: the difference between the **messages** you write and the **string of tokens**
the model actually sees.

> **How this course works — read once.** You write the code. Each section walks you
> through building small scripts *yourself*, step by step, running them as you go. You'll
> write your files in the `work/` folder. A complete **reference solution** for
> everything you build lives under `examples/NN/` — peek if you get stuck, but type it
> yourself first. That's the hands-on part, and it's where the learning happens.

> **Security is a through-line.** Safety isn't one chapter at the end — every section
> closes with a short **Security** note tied to that topic, because the mistakes that
> matter (leaked keys, prompt injection, running untrusted output) show up the moment you
> use each feature. Isolation is big enough to earn its own two sections (16–17,
> Sandboxing), and the defenses come together in Section 21.

**Where this fits:** this is lesson 1 of 25 — everything else builds on it. We start at
the bottom on purpose. Once you've sent the literal request yourself, every later
abstraction will feel like a convenience, not magic.

---

## What you're talking to

Throughout this course you'll talk to a **hosted, OpenAI-compatible inference server**.
Two phrases there matter:

- **Inference server** — a program that has a model loaded and answers requests. Ours is
  hosted for you and serves `gpt-oss-120b`. We don't assume *which* server software it
  runs — only that it speaks the OpenAI API. You don't run it; you just send it HTTP
  requests.
- **OpenAI-compatible** — it speaks the same HTTP API that OpenAI popularized:
  `POST /v1/chat/completions`, `GET /v1/models`, and so on. Learn this protocol once and
  the same code talks to OpenAI, vLLM, llama.cpp, and dozens of others.

So "calling a model" is, mechanically, just **an HTTP POST with some JSON**. That's the
demystification we're after — and you're about to do it by hand.

---

## Get set up

Make sure you've done the one-time setup from the [README](../README.md): installed the
dependencies, copied `.env.example` to `.env` and filled in your endpoint details, and
created a `work/` folder. You'll write this section's code in `work/`.

Your scripts read three values from the environment:

| Variable | What it is | Example |
|---|---|---|
| `OPENAI_BASE_URL` | Where the server lives. **Ends in `/v1`.** | `https://your-host/v1` |
| `OPENAI_API_KEY`  | Your auth token, sent as `Authorization: Bearer <token>`. | `sk-...` |
| `MODEL`           | The id of the model the server serves. | `openai/gpt-oss-120b` |

Load them into your shell and confirm they're set:

```bash
set -a; source .env; set +a
python -c "import os; print('MODEL =', os.environ['MODEL'])"
```

If that prints your model id, you're ready. If it errors with a `KeyError`, your `.env`
isn't loaded — redo the `set -a; source .env; set +a` line.

> The `/v1` in the base URL is part of the path: the chat endpoint is
> `OPENAI_BASE_URL` + `/chat/completions`. And note our hosted endpoint needs a **real**
> token — unlike many *local* servers, which accept any dummy value.

---

## Write your first call — raw HTTP

We'll use only the [`requests`](https://requests.readthedocs.io/) library, so nothing is
hidden. Create a new file, **`work/hello.py`**, and write this first piece:

```python
import os
import requests

base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
api_key = os.environ["OPENAI_API_KEY"]
model = os.environ["MODEL"]

print("talking to:", base_url, "as", model)
```

Run it from the repo root:

```bash
python work/hello.py
```

You should see your endpoint and model printed. No call yet — we just confirmed the
settings load. **Run early and often; that's the rhythm of this course.**

Now add the request. Append to `work/hello.py`:

```python
payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": "You are a concise, friendly assistant."},
        {"role": "user", "content": "Say hello in one short sentence."},
    ],
}

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

response = requests.post(
    f"{base_url}/chat/completions",
    headers=headers,
    json=payload,
    timeout=30,
)
response.raise_for_status()       # turn an HTTP error into a Python exception
data = response.json()

print(data["choices"][0]["message"]["content"])
```

Run it again:

```bash
python work/hello.py
```

You should get a one-sentence greeting back. **You just called a language model by
hand.** Let's make sure you know what every piece did:

> **Hit an SSL error instead?** If this fails with `CERTIFICATE_VERIFY_FAILED` (or the
> SDK's vaguer `Connection error.`), your machine can't verify the endpoint's certificate —
> common behind a corporate proxy. See the README's
> [Troubleshooting: SSL / certificates](../README.md#troubleshooting-ssl--certificates):
> usually setting `SSL_CERT_FILE` to your CA bundle in your `.env` fixes it.

- **`payload`** is the entire request body. The important part is `messages` — a list of
  turns, each with a `role` and `content` (more on roles below).
- **`headers`** carry your token (`Authorization: Bearer ...`) and say the body is JSON.
- **`requests.post(...)`** sends it to `<base_url>/chat/completions`.
- **`response.raise_for_status()`** makes the program fail loudly if the server returned
  an error (a wrong token, a bad model id) instead of silently continuing.
- **`data["choices"][0]["message"]["content"]`** digs out the reply. We'll dissect that
  shape thoroughly in Section 2 — for now, that's where the text lives.

### See the whole response

The reply is one field of a larger object. Temporarily change your last line to print
all of it:

```python
import json
print(json.dumps(data, indent=2))
```

Run once more and read the JSON. You'll see `choices`, a `usage` block with token
counts, and more. Don't memorize it yet — Section 2 is devoted to it — just notice the
reply is a small part of a structured envelope.

> **Reference:** a complete version of what you just wrote is
> [`examples/01/raw_http.py`](../examples/01/raw_http.py) (it prints the full JSON, the
> reply, and `usage`). Compare it with your `work/hello.py`.

---

## Write the same call — with the SDK

Writing the HTTP by hand was the point. Now see what the official `openai` client buys
you: it builds the same JSON and POSTs to the same endpoint, so you skip the plumbing.

Create **`work/hello_sdk.py`**:

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
)

response = client.chat.completions.create(
    model=os.environ["MODEL"],
    messages=[
        {"role": "system", "content": "You are a concise, friendly assistant."},
        {"role": "user", "content": "Say hello in one short sentence."},
    ],
)

print(response.choices[0].message.content)
```

Run it:

```bash
python work/hello_sdk.py
```

Same greeting. Look closely at `response.choices[0].message.content` — the **identical
path** you used on the raw JSON, now as typed attributes instead of dictionary keys.
**The SDK is a thin wrapper over the HTTP you already wrote.** That's why doing it by
hand first matters: when something breaks later, you know exactly what's underneath.

> **Reference:** [`examples/01/with_sdk.py`](../examples/01/with_sdk.py). From here on
> we'll mostly use the SDK, dropping to raw HTTP whenever the wire format teaches
> something.

---

## Roles: the "sections" of a prompt

Each entry in `messages` has a **role**. You've already used the core three:

- **`system`** — standing instructions: who the assistant is, its tone, its rules. Set
  once, applies to the whole conversation.
- **`user`** — a turn from the human.
- **`assistant`** — a turn from the model. You also *write* assistant messages yourself
  to replay a conversation or to show worked examples (we'll lean on this in Section 12).

Other roles you'll meet later have become standard too:

- **`tool`** — results handed back from a tool the model asked to call. It replaced the
  older `function` role, now deprecated. (Section 14.)
- **`developer`** — a newer role from reasoning models; essentially `system`'s successor
  in an explicit *platform > developer > user* chain of command. (Section 6.)

For now: **system, user, assistant** is enough to hold a conversation.

---

## The one idea to take away: messages vs. the template

This is the most important concept in the lesson. There are **two representations of the
same conversation**:

1. **What you write** — a tidy JSON list of `{role, content}` messages.
2. **What the model receives** — a single, flat **string of tokens**. The model has no
   concept of "roles" or "a list." It only ever sees text.

The thing that converts #1 into #2 is the **chat template** — a small template (in
Jinja2) that ships *inside the model's own tokenizer configuration*. The server renders
your messages through it. For a model in the widely-used "ChatML" format (easy to read),
your two messages become this exact string:

```
<|im_start|>system
You are a concise, friendly assistant.<|im_end|>
<|im_start|>user
Say hello in one short sentence.<|im_end|>
<|im_start|>assistant
```

- `<|im_start|>` / `<|im_end|>` are **special tokens** marking boundaries; the role name
  is just text after the marker.
- The dangling `<|im_start|>assistant` at the end is the **generation prompt**: it tells
  the model "your turn — continue from here."

Different model families use **different templates**. Our model, `gpt-oss-120b`, uses
OpenAI's **harmony** format, which is richer than ChatML — it even gives the model
separate *channels* for private reasoning and the final answer (that reasoning is the
subject of Section 6). So the real rendered string for our model won't look like the tidy
ChatML above. That's the point: **same messages, a different rendered string for every
model** — which is why the template belongs to the model, not to the API. The server
applies the right one automatically; you never hand-write delimiters. But knowing they're
there is what separates "it just works" from "I understand why."

### Prove it yourself — measure the tokens

You can't easily print the rendered string without the right tokenizer, and **this
course uses no local tokenizer** (no Hugging Face downloads, no `tiktoken`). But you can
*measure* the result through the server. Create **`work/tokens.py`**:

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
)

def prompt_tokens(text: str) -> int:
    response = client.chat.completions.create(
        model=os.environ["MODEL"],
        messages=[{"role": "user", "content": text}],
        max_tokens=1,                       # we only care about the INPUT size
    )
    return response.usage.prompt_tokens     # tokens AFTER templating

print(prompt_tokens("hi"))
print(prompt_tokens("hi " * 50))
```

Run it:

```bash
python work/tokens.py
```

The second number is much bigger — your longer message became more tokens. You're
reading `usage.prompt_tokens`, the size of your messages *after* the template was
applied, straight from the server. **Section 3** is a whole lesson on the template itself,
and **Section 4** turns these counts into a budget.

> **Looking ahead:** [`examples/03/show_template.py`](../examples/03/show_template.py)
> (Section 3) goes further — *if* your server happens to expose a `/tokenize` endpoint (a
> non-standard extension some servers add, not part of the OpenAI API), it prints the actual
> rendered template string so you can see the real delimiters. Most endpoints don't expose
> it; then it just prints the token count instead. We pick this apart in Section 3.

---

> **Security:** API keys are credentials. Read them from the environment, never hard-code them in a script or commit them to git — a leaked key is a billable account someone else controls.

## Challenges

Write these from scratch (new files in `work/`). References are listed, but try first.

1. **A two-turn conversation.** Build a `messages` list with four entries — `system`,
   `user`, `assistant` (a made-up earlier reply), then a new `user` follow-up that
   refers back to it ("and what about its capital?"). Send it. *Success:* the model's
   answer clearly uses the earlier turn.
2. **Persona swap.** Copy `work/hello_sdk.py` to `work/pirate.py`, change only the
   `system` message to make the assistant answer like a pirate, and rerun. *Success:*
   the *user* message is unchanged but the style flips.
3. **A token counter.** Using `work/tokens.py` as a starting point, write a `count(text)`
   function and print the token count of: your name, a full sentence, and the same
   sentence in ALL CAPS. *Success:* you can report all three counts and say whether ALL
   CAPS changed the sentence's count on **your** model — it often does, by a little, but
   the split is model-specific, so don't assume. *(Reference idea:
   [`examples/04/count_tokens.py`](../examples/04/count_tokens.py) — used in Section 4.)*
4. **Discover the model id.** Write a script that sends `GET <base_url>/models` (with the
   `Authorization` header) and prints each model id from the JSON. *Success:* it prints
   the id you put in `MODEL`. *(Reference:
   [`examples/01/list_models.py`](../examples/01/list_models.py).)*

---

## Recap

- Calling a model is just **`POST /v1/chat/completions` with JSON** — you wrote that by
  hand in `work/hello.py`.
- The `openai` SDK is a **thin wrapper** over the same call — same fields, nicer types.
- A prompt is a list of **role-tagged messages**; the core roles are `system`, `user`,
  `assistant` (with `tool` and `developer` later).
- Your **messages** are rendered by the model's **chat template** into a **string of
  tokens** — that string is what the model truly sees, and you measured its size with
  `usage.prompt_tokens`.

## Next

**Section 2 — Anatomy of a Response:** you'll dig into the envelope your call returned —
`choices`, `finish_reason`, and the `usage` block — and learn exactly where each field
lives before we start changing the model's behavior.
