# AI Development — A Foundations-First Course

A hands-on course on building with large language models, written for people who want
to *understand* what they're doing, not just copy snippets. It assumes **entry-level
Python** but doesn't talk down to you — every lesson explains the *why*.

The approach throughout: **see the raw mechanic first, then the convenient abstraction
on top.** You'll send a literal HTTP request before you touch an SDK, compute a cosine
similarity by hand before you reach for a vector database, and write a tool-use loop
from scratch before anyone says the word "agent." Each section builds on the one before
it.

## What you'll talk to

Every example targets a **hosted, OpenAI-compatible inference server** (running vLLM)
serving **`gpt-oss-120b`**, OpenAI's open-weight reasoning model. You don't run a
server — you point three environment variables at the hosted one and go. Because the
API is OpenAI-compatible, the same code works against many providers and models.

> This course uses **no Hugging Face Hub downloads and no `tiktoken`.** When we need to
> tokenize or inspect a model's chat template, we ask the *server* (via token counts in
> the response, or vLLM's `/tokenize` endpoint when it's exposed).

## What your endpoint needs to support

The examples target a vLLM endpoint serving `gpt-oss-120b`, but a few capabilities depend
on how that endpoint is configured. **The scripts degrade gracefully** — if something
isn't available, they tell you instead of crashing — so you can always read along.

| Capability | Used in | Required? |
|---|---|---|
| Chat completions (`POST /v1/chat/completions`) | every section | **Required** |
| Model listing (`GET /v1/models`) | §1 | Recommended |
| Tokenize endpoint (`POST /tokenize`) | §1 | Optional — falls back to `usage` token counts |
| Reasoning fields (`reasoning_content`, `reasoning_tokens`, `reasoning_effort`) | §5 | Optional — the model still answers |
| Cached-token reporting (`prompt_tokens_details.cached_tokens`) | §10 | Optional — caching may still happen unseen |
| **Tool calling** (vLLM auto tool choice) | §13, §14, §18, §20 | **Required for those sections** |
| **Embeddings** (`POST /v1/embeddings` + an `EMBED_MODEL`) | §15, §16, §20 | **Required for those sections** (capstone falls back to keyword search) |

If a feature isn't enabled on your endpoint, the relevant lesson says so up front.

## Setup

You need Python 3.9+ and the hosted endpoint's details (base URL, token, model id).

```bash
# 1. (recommended) create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. install the dependencies
pip install -r requirements.txt

# 3. configure your endpoint
cp .env.example .env
#    ...edit .env and fill in OPENAI_BASE_URL, OPENAI_API_KEY, MODEL...

# 4. load those values into your current shell
set -a; source .env; set +a

# 5. make a folder for the code you'll write as you follow the lessons
mkdir work
```

From **Section 15** onward you'll also need an embedding model. Set `EMBED_MODEL` in your
`.env` to one your endpoint serves (it's usually a different model than the chat model).
If your endpoint serves only the chat model, those sections will tell you and you can read
along; the capstone falls back to keyword search automatically.

Verify it works:

```bash
python examples/01/list_models.py   # prints the model id(s) the endpoint serves
python examples/01/raw_http.py      # your first completion, the raw HTTP way
```

If `list_models.py` prints a model id and `raw_http.py` prints a greeting, you're ready.

### Troubleshooting: SSL / certificates

If that first call fails with an SSL error — `CERTIFICATE_VERIFY_FAILED`, or the SDK's
vaguer `APIConnectionError: Connection error.` — Python can't verify the endpoint's TLS
certificate. This is common on a machine behind a corporate proxy that inspects HTTPS, in
front of a self-signed endpoint, or with an out-of-date CA store. Fix it **without editing
any code** — set one of these in your `.env` (then re-run `set -a; source .env; set +a`):

- **Preferred (stays secure): trust the right CA.** Point at the CA bundle / certificate
  your network uses. The standard `SSL_CERT_FILE` works for *every* script in the course
  (both the SDK and the raw-`requests` examples):

  ```bash
  SSL_CERT_FILE=/path/to/corporate-ca.pem
  ```

  Ask your IT team for the proxy's root certificate, or export it from your system keychain.
  (`OPENAI_CA_BUNDLE` and `REQUESTS_CA_BUNDLE` are also honored by the shared client in
  `examples/common.py` and the raw-`requests` scripts; `SSL_CERT_FILE` is the one that
  additionally covers Section 1's by-hand SDK demo, so reach for it first.)

  > **Why several variables?** Outside this course's `verify=` handling they target
  > *different* HTTP stacks and don't interact: `REQUESTS_CA_BUNDLE` is read **only** by the
  > `requests` library (Sections 1 and 7), while `SSL_CERT_FILE` is an OpenSSL-level variable
  > read by `httpx` — the stack the OpenAI SDK uses (`requests` ignores `SSL_CERT_FILE`, and
  > `httpx` ignores `REQUESTS_CA_BUNDLE`). The course scripts read all three and pass
  > whichever is set as an explicit `verify=`, so you normally only need **one** — and
  > `SSL_CERT_FILE` is the safest single choice because it also reaches the plain SDK client
  > in Section 1. They don't merge: it's first-set-wins on a single bundle file, not a union
  > of CAs, so put every CA you need into one file.

- **Last resort (insecure): skip verification.**

  ```bash
  OPENAI_INSECURE=1
  ```

  This turns off certificate checking for the shared client (`examples/common.py`, used from
  Section 2 on) and the raw-`requests` scripts. It removes protection against
  man-in-the-middle attacks, so only use it on a network you trust — prefer the CA-bundle
  option above whenever you can.

## How this course works

**You write the code.** Each section guides you through building small scripts yourself,
step by step, running them as you go — that's the hands-on part, and it's where the
learning happens. Write your own files in the `work/` folder. A complete **reference
solution** for everything you build lives under `examples/NN/`; peek if you get stuck,
but type it yourself first.

## How the repo is organized

```
lessons/      # the markdown lessons, read in order
examples/NN/  # reference solutions for Section NN (run them, or compare against yours)
work/         # YOUR code as you follow along (git-ignored; create it with: mkdir work)
```

## Course outline

### Foundations (Sections 1–10)

1. **Hello World** — talk to the server; messages, roles, and the chat template.
2. **Anatomy of a Response** — `choices`, `finish_reason`, and the `usage` block.
3. **Tokens & the Context Window** — what a token is, and the input + output budget.
4. **Sampling Parameters** — `temperature`, `top_p`, `seed`; *see* randomness change.
5. **Reasoning / "Thinking" Models** — reasoning tokens and what they cost.
6. **Handling & Validating Responses** — JSON mode and validating with Pydantic.
7. **Blocking vs Streaming** — one-shot responses vs. token-by-token streaming.
8. **Robustness** — errors, retries, rate limits, timeouts.
9. **Observability & Logging** — the telemetry you get back and how to use it.
10. **Cost, Pricing & Prompt Caching** — account for spend; exploit prefix caching.

### Advanced (Sections 11–20)

11. **Prompt Engineering Fundamentals** — zero/one/few-shot, instruction design.
12. **Conversation State & Memory** — the API is stateless; managing history.
13. **Tool / Function Calling** — let the model call your code.
14. **The Tool-Use Loop** — a mini-agent built from primitives.
15. **Embeddings** — vectors and semantic similarity from scratch.
16. **Retrieval-Augmented Generation (RAG)** — grounding answers in your documents.
17. **Security & Guardrails** — prompt injection, untrusted content, permissions.
18. **Agents** — planning and multi-step tool use, composing everything above.
19. **Evaluation & Testing** — LLM-as-judge, golden sets, regression tests.
20. **Capstone** — an end-to-end retrieval-augmented, tool-using agent.

Start with [`lessons/01-hello-world.md`](lessons/01-hello-world.md).
