"""
Preflight: check what YOUR endpoint can do before you start the course.

The course assumes only an OpenAI-compatible Chat Completions endpoint -- nothing about
which inference server or model is behind it. Different servers and models behave
differently, and some change whether a lesson works at all. This script probes the
capabilities the lessons depend on (streaming, tool calling, JSON mode, seeds,
prompt-cache reporting, embeddings, reasoning fields, the temperature ceiling, the
chat template's token cost, and more) WITHOUT running the lessons. It makes a series of
tiny calls and never changes anything, then prints two short, plain summaries:

  * "What this means for your setup" -- the particular points to keep in mind.
  * "Lesson coverage" -- which sections will work, and which need attention.

    set -a; source .env; set +a
    python scripts/preflight.py

Only the OpenAI-standard Chat Completions API is required. Anything outside that
standard (for example the `/tokenize` endpoint) is treated as an optional bonus.
Every probe degrades gracefully: a missing capability is reported, not crashed on.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import requests

# Reuse the same helpers the example scripts use (examples/common.py).
sys.path.append(str(Path(__file__).resolve().parents[1] / "examples"))
from common import MODEL, get_client, ssl_verify  # noqa: E402

# --- tiny report helpers -----------------------------------------------------

PASS, WARN, INFO, FAIL = "PASS", "WARN", "INFO", "FAIL"
_counts = {PASS: 0, WARN: 0, INFO: 0, FAIL: 0}

caps: dict = {}          # capability name -> result (bool / value)
_notes: list = []        # plain points for "What this means for your setup"


def say(tag: str, msg: str) -> None:
    _counts[tag] += 1
    print(f"  [{tag}] {msg}")


def note(msg: str) -> None:
    _notes.append(msg)


def section(title: str) -> None:
    print(f"\n{title}")


def chat(client, content, **kw):
    """One tiny chat call; callers pass max_tokens etc. Raises on failure."""
    return client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": content}], **kw)


# --- required core -----------------------------------------------------------

def check_env() -> bool:
    section("Environment")
    ok = True
    for var in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "MODEL"):
        if os.environ.get(var):
            shown = os.environ[var] if var != "OPENAI_API_KEY" else "***set***"
            say(PASS, f"{var} = {shown}")
        else:
            say(FAIL, f"{var} is not set -- copy .env.example to .env and load it "
                      "(set -a; source .env; set +a)")
            ok = False
    return ok


def check_models(client) -> None:
    section("Model listing (GET /v1/models) -- Section 1")
    try:
        ids = [m.id for m in client.models.list().data]
    except Exception as err:
        caps["models"] = False
        say(WARN, f"could not list models ({type(err).__name__}); not required.")
        return
    caps["models"] = MODEL in ids
    if caps["models"]:
        say(PASS, f"{MODEL!r} is served.")
    else:
        say(WARN, f"{MODEL!r} not in the listed models. Listed: {', '.join(ids[:6])}"
                  f"{' ...' if len(ids) > 6 else ''}")


# --- behaviour probes (each maps to lessons) ---------------------------------

def check_tokens_and_overhead(client) -> None:
    section("Token counting & template overhead -- Section 3")

    def ptoks(text):
        return chat(client, text, max_tokens=1).usage.prompt_tokens

    try:
        empty, hello, upper = ptoks(""), ptoks("hello"), ptoks("HELLO")
    except Exception as err:
        say(FAIL, f"token counting failed ({type(err).__name__}: {err}).")
        return
    caps["overhead"] = empty
    say(INFO, f"empty-message overhead = {empty} tokens; 'hello'={hello}, 'HELLO'={upper} "
              f"({'casing changes the count' if hello != upper else 'casing does not change it here'}).")
    note(f"Every request carries about {empty} tokens of fixed template cost. Even a "
         "one-word prompt is at least that size.")
    if empty > 30:
        say(WARN, f"overhead is large ({empty}); Section 3 compares raw counts rather than "
                  "subtracting an empty-message baseline.")


def check_reasoning_budget(client):
    section("max_tokens behaviour & reasoning fields -- Sections 2, 3, 5")
    try:
        r = chat(client, "Write a short paragraph about the ocean.", max_tokens=16)
    except Exception as err:
        say(FAIL, f"basic completion failed ({type(err).__name__}: {err}).")
        return
    ch = r.choices[0]
    empty_on_tight = not (ch.message.content or "").strip() and ch.finish_reason == "length"
    usage = r.usage.model_dump() if hasattr(r.usage, "model_dump") else {}
    details = usage.get("completion_tokens_details") or {}
    rt_val = details.get("reasoning_tokens")
    caps["reasoning_tokens"] = rt_val is not None
    caps["reasoning_content"] = bool(getattr(ch.message, "reasoning_content", None))
    # A model is "reasoning" if a tight budget comes back empty (spent on thinking), OR
    # it exposes reasoning text, OR it reports a NON-ZERO reasoning token count. A reported
    # count of 0 (which some non-reasoning models send) does not make it a reasoning model.
    caps["reasoning_model"] = empty_on_tight or caps["reasoning_content"] or bool(rt_val)
    # Cached-token reporting is specifically usage.prompt_tokens_details.cached_tokens.
    ptd = usage.get("prompt_tokens_details")
    caps["cached_field"] = isinstance(ptd, dict) and "cached_tokens" in ptd

    if empty_on_tight:
        say(WARN, "max_tokens=16 returned EMPTY text (finish_reason='length'): the model "
                  "thinks first and spent the small budget on thinking.")
        note("Your model thinks before it answers. A small max_tokens can return empty "
             "text. Allow more output tokens (for example 256+) when you want an answer.")
    else:
        say(PASS, "max_tokens=16 returned visible text (the tight budget was not spent "
                  "entirely on hidden thinking).")
        note("A small max_tokens returns partial text that stops mid-sentence.")
    say(INFO, f"reasoning_tokens {'reported' if caps['reasoning_tokens'] else 'not reported'}, "
              f"reasoning_content {'present' if caps['reasoning_content'] else 'absent'}.")


def check_reasoning_effort(client) -> None:
    section("reasoning_effort parameter -- Section 5")
    try:
        chat(client, "hi", max_tokens=1, extra_body={"reasoning_effort": "low"})
        caps["reasoning_effort"] = True
        say(PASS, "accepted.")
    except Exception as err:
        caps["reasoning_effort"] = False
        say(INFO, f"rejected ({type(err).__name__}); Section 5's reasoning_effort dial is "
                  "not available on this endpoint.")


def check_sampling(client) -> None:
    section("Sampling: temperature ceiling, seed, n>1 -- Sections 2, 4")
    highest = None
    for temp in (0.0, 1.0, 1.5, 2.0, 2.5, 3.0):
        try:
            chat(client, "hi", temperature=temp, max_tokens=1)
            highest = temp
        except Exception:
            break
    caps["temp_max"] = highest
    if highest is None:
        say(WARN, "every temperature, including 0.0, was rejected -- check the server.")
    elif highest >= 3.0:
        say(INFO, "temperature accepted up to at least 3.0.")
    else:
        say(INFO, f"highest temperature accepted: {highest}.")
        note(f"The highest temperature your server accepts is {highest}. Keep Section 4 "
             f"values at or below it; higher values raise an error.")

    try:
        chat(client, "hi", seed=42, max_tokens=1)
        caps["seed"] = True
    except Exception:
        caps["seed"] = False
    try:
        caps["multi_n"] = len(chat(client, "hi", n=2, max_tokens=1).choices) == 2
    except Exception:
        caps["multi_n"] = False
    say(INFO, f"seed {'accepted (determinism not verified here)' if caps['seed'] else 'rejected'}; "
              f"n>1 {'supported' if caps['multi_n'] else 'not supported'}.")
    if not caps["seed"]:
        note("The seed parameter is rejected, so Section 4's reproducibility demo will "
             "not run as written.")
    if not caps["multi_n"]:
        note("Requesting several answers at once (n>1) is not supported; Section 2's "
             "n=2 challenge will not run.")


def check_streaming(client) -> None:
    section("Streaming (stream=True) -- Section 7")
    try:
        stream = chat(client, "count to three", max_tokens=8, stream=True)
        got = any(True for _ in stream)
        caps["stream"] = got
        say(PASS if got else WARN, "streaming works." if got else "no chunks received.")
    except Exception as err:
        caps["stream"] = False
        say(WARN, f"not supported ({type(err).__name__}); Section 7's streaming examples "
                  "will not run.")
        note("Streaming (stream=True) is not supported, so Section 7's live-output "
             "examples will not run on this endpoint.")


def check_json_mode(client) -> None:
    section("Structured / JSON output (response_format) -- Section 6")
    # Servers disagree on the form: some accept both; some want json_schema and reject
    # json_object (or the reverse). Try the standard variants before concluding.
    schema = {"type": "json_schema", "json_schema": {"name": "r", "schema": {
        "type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}}
    caps["json"] = False
    for rf in (schema, {"type": "json_object"}):
        try:
            r = chat(client, "Return a JSON object with a single key ok set to true.",
                     response_format=rf, max_tokens=256)
            json.loads(r.choices[0].message.content)
            caps["json"] = True
            say(PASS, f"JSON mode works (response_format {rf['type']}).")
            break
        except Exception:
            continue
    if not caps["json"]:
        say(INFO, "no server JSON mode; Section 6 still works by parsing the text and "
                  "validating with Pydantic.")
        note("Server-enforced JSON mode (response_format) is not available; Section 6 "
             "uses plain parsing plus Pydantic validation instead.")


def check_tools(client) -> None:
    section("Tool / function calling -- Sections 13, 14, 22, 24")
    tool = {"type": "function", "function": {
        "name": "get_weather", "description": "Get the weather for a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                       "required": ["city"]}}}
    msgs = [{"role": "user", "content": "What is the weather in Paris? Use the tool."}]
    # tool_choice forms vary: the object form is widely rejected. Try the standard
    # string values ("required" forces a call; "auto" lets the model decide).
    ran = caps["tools"] = False
    for tc in ("required", "auto"):
        try:
            r = client.chat.completions.create(
                model=MODEL, messages=msgs, tools=[tool], tool_choice=tc, max_tokens=256)
            ran = True
            tcs = r.choices[0].message.tool_calls
            # Require an actual tool call with a function name -- finish_reason alone
            # (or an empty tool_calls list) is not proof the server can call tools.
            if tcs and getattr(getattr(tcs[0], "function", None), "name", None):
                caps["tools"] = True
                break
        except Exception:
            continue
    if caps["tools"]:
        say(PASS, "the server returned a tool call when asked to use one.")
    elif ran:
        say(WARN, "the server accepted tools but did not return a tool call.")
        note("Tool calling did not produce a call in this test; Sections 13-14 and the "
             "agent sections may be unreliable on this endpoint.")
    else:
        say(WARN, "not supported (the tools parameter was rejected).")
        note("Tool / function calling is not supported. Sections 13-14, 22 and 24 (the "
             "agent sections and the capstone) depend on it and will not run.")


def check_caching() -> None:
    section("Prompt-cache reporting -- Section 10")
    if caps.get("cached_field"):
        say(INFO, "usage includes prompt_tokens_details (cached-token reporting present).")
    else:
        say(INFO, "no prompt_tokens_details in usage; caching may still happen unseen.")
        note("Your server does not report cached tokens, so Section 10 explains prompt "
             "caching but cannot show the savings as a number.")


def check_embeddings(client) -> None:
    section("Embeddings -- Sections 18, 19, 24")
    embed_model = os.environ.get("EMBED_MODEL", "")
    if not embed_model:
        caps["embeddings"] = "unset"
        say(INFO, "EMBED_MODEL not set -- set it before Section 18 (it is often a "
                  "different model than the chat model).")
        return
    try:
        client.embeddings.create(model=embed_model, input="hello")
        caps["embeddings"] = "ok"
        say(PASS, f"embeddings work with EMBED_MODEL={embed_model!r}.")
    except Exception as err:
        caps["embeddings"] = "fail"
        say(WARN, f"EMBED_MODEL={embed_model!r} set but embeddings failed "
                  f"({type(err).__name__}).")
        note("EMBED_MODEL is set but embeddings failed; Sections 18-19 (and parts of the "
             "capstone) need a working embedding model.")


def check_tokenize() -> None:
    section("Optional bonus: /tokenize extension (non-standard, not the OpenAI API)")
    base = os.environ["OPENAI_BASE_URL"].rstrip("/")
    root = base[:-3].rstrip("/") if base.endswith("/v1") else base
    ok = False
    try:
        r = requests.post(
            f"{root}/tokenize",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}],
                  "add_generation_prompt": True},
            timeout=20, verify=ssl_verify())
        r.raise_for_status()
        data = r.json()
        # Some servers answer 200 with an error body, not a 404, so check the fields.
        ok = isinstance(data, dict) and ("count" in data or "tokens" in data)
    except Exception:
        ok = False
    caps["tokenize"] = ok
    if ok:
        say(PASS, "present -- examples/01/show_template.py can print the exact rendered "
                  "template string as a bonus.")
    else:
        say(INFO, "not available -- this is normal. You measure prompt size with "
                  "usage.prompt_tokens instead (standard, and enough for every lesson).")


def check_local_tools() -> None:
    section("Local tools for the sandboxing sections -- Section 16 (optional)")
    caps["docker"] = bool(shutil.which("docker"))
    caps["pg"] = bool(os.environ.get("DATABASE_URL"))
    say(INFO, f"docker CLI {'found' if caps['docker'] else 'not found'}; "
              f"DATABASE_URL {'set' if caps['pg'] else 'not set'} "
              "(both optional; Section 16 skips cleanly without them).")


# --- lesson coverage roll-up -------------------------------------------------

def coverage() -> None:
    """Translate the probes into a per-section readiness list (covers all 24)."""
    section("Lesson coverage")

    def line(secs, msg):
        print(f"  {secs}: {msg}")

    line("Sections 1-2, 8-9, 11-12, 20-21, 23",
         "ready -- standard Chat Completions is all they need.")
    line("Section 3 (Tokens)",
         f"ready (template overhead is {caps.get('overhead', '?')} tokens).")
    temp = caps.get("temp_max")
    line("Section 4 (Sampling)",
         "ready -- " + (f"max temperature {temp}; " if temp is not None and temp < 3 else "")
         + ("seed accepted, " if caps.get("seed") else "seed NOT accepted, ")
         + ("n>1 ok." if caps.get("multi_n") else "n>1 not supported."))
    if caps.get("reasoning_model"):
        rdetail = ("reports reasoning tokens" if caps.get("reasoning_tokens")
                   else "returns reasoning text" if caps.get("reasoning_content")
                   else "exposes no reasoning detail")
        line("Section 5 (Reasoning)",
             f"ready -- this is a reasoning model; {rdetail}; "
             + ("reasoning_effort works." if caps.get("reasoning_effort")
                else "reasoning_effort not available."))
    else:
        line("Section 5 (Reasoning)",
             "ready to read -- your model does not think before answering, so the "
             "reasoning examples mainly explain the concept.")
    line("Section 6 (Validating responses)",
         "ready -- " + ("server JSON mode available." if caps.get("json")
                        else "no server JSON mode; uses text parsing + Pydantic."))
    line("Section 7 (Streaming)",
         "ready." if caps.get("stream") else "BLOCKED -- streaming not supported.")
    line("Section 10 (Cost & caching)",
         "ready -- " + ("cached tokens are reported." if caps.get("cached_field")
                        else "cached tokens not reported (caching may be unseen)."))
    tools_ok = caps.get("tools")
    line("Sections 13-14, 22, 24 (Tools, Agents, Capstone)",
         "ready." if tools_ok else "BLOCKED -- tool/function calling not supported.")
    line("Sections 15-16 (Sandboxing)",
         f"local & optional -- docker {'found' if caps.get('docker') else 'not found'}.")
    line("Section 17 (MCP)",
         "runs in your client, not the model endpoint -- not checked here.")
    emb = caps.get("embeddings")
    line("Sections 18-19 (Embeddings, RAG)",
         {"ok": "ready.", "fail": "needs a working EMBED_MODEL (currently failing).",
          "unset": "set EMBED_MODEL before you start them."}.get(emb, "check EMBED_MODEL."))


# --- main --------------------------------------------------------------------

def main() -> int:
    print("Course preflight -- what your endpoint can do, and what to keep in mind.")
    if not check_env():
        print("\nFix the environment first, then re-run. Stopping.")
        return 1

    client = get_client()
    try:
        chat(client, "ping", max_tokens=1)
    except Exception as err:
        section("Chat completions")
        say(FAIL, f"could not reach the chat endpoint ({type(err).__name__}: {err}). "
                  "Check OPENAI_BASE_URL (must end in /v1), the token, and the model id.")
        print("\nStopping -- nothing else can run without chat completions.")
        return 1
    section("Chat completions (required)")
    say(PASS, "reachable.")

    check_models(client)
    check_tokens_and_overhead(client)
    check_reasoning_budget(client)
    check_reasoning_effort(client)
    check_sampling(client)
    check_streaming(client)
    check_json_mode(client)
    check_tools(client)
    check_caching()
    check_embeddings(client)
    check_tokenize()
    check_local_tools()

    section("What this means for your setup")
    if not _notes:
        print("  Your endpoint matches the lessons as written -- nothing to watch out for.")
    for n in _notes:
        print(f"  - {n}")

    coverage()

    print(f"\n  ({_counts[PASS]} pass, {_counts[WARN]} warn, {_counts[INFO]} info, "
          f"{_counts[FAIL]} fail. Only standard Chat Completions is required.)")
    return 1 if _counts[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
