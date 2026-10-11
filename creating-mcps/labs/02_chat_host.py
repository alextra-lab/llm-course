import marimo

__generated_with = "0.25.1"
app = marimo.App(width="full", app_title="Unit 2 lab: a chat host")


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 2 lab: a chat host

    This notebook is a host. It runs Unit 1's CMDB server and Unit 2's ticketing server, offers
    their tools to `gpt-oss-120b`, and shows every tool call in a side panel: which server,
    which tool, which argument names, the outcome, the time taken, and the size of the result.
    When a server needs an answer from you (`input_required`), the turn **pauses** and a form
    appears. Nothing goes back to the server until you answer.

    With no model endpoint configured, the chat box is replaced by a **manual** panel: you pick
    the tool and its arguments yourself, and everything else, the confirmation form included,
    works the same.
    """)
    return


@app.cell
def _():
    import asyncio
    import importlib.util
    import json
    import sys
    import threading
    import time
    import uuid

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent         # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    if str(EXAMPLES) not in sys.path:
        sys.path.append(str(EXAMPLES))

    _spec = importlib.util.spec_from_file_location("unit2_host", EXAMPLES / "02" / "host.py")
    unit2 = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(unit2)
    return asyncio, json, mo, threading, time, unit2, uuid


@app.cell
def _(asyncio, threading, time):
    class BackgroundHost:
        """Runs the Unit 2 `Host` on its own event loop in a thread.

        Two reasons. The servers stay connected while you move between cells. And when a server
        asks for input mid-call, the question can wait here for as long as you take, while the
        notebook stays responsive enough to show you the form.
        """

        def __init__(self, host_cls, servers):
            self.loop = asyncio.new_event_loop()
            threading.Thread(target=self.loop.run_forever, daemon=True).start()
            self.pending = None              # (server, params, future) while a server waits
            self.events = []                 # ToolEvents, in order
            self.host = host_cls(servers, self._ask)
            self.run(self.host.__aenter__())

        def run(self, coro, timeout=60):
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

        def submit(self, coro):
            return asyncio.run_coroutine_threadsafe(coro, self.loop)

        async def _ask(self, server, params):
            future = self.loop.create_future()
            self.pending = (server, params, future)
            try:
                return await future
            finally:
                self.pending = None

        def answer(self, result):
            future = self.pending[2]
            self.loop.call_soon_threadsafe(future.set_result, result)
            time.sleep(0.05)                 # let the loop pick it up before we poll again

        def wait(self, job, timeout=180):
            """Block until `job` finishes, or until a server is waiting for the person."""
            t0 = time.time()
            while not job.done() and self.pending is None and time.time() - t0 < timeout:
                time.sleep(0.05)
            return job.done()

    return (BackgroundHost,)


@app.cell
def _(BackgroundHost, mo, unit2, uuid):
    bg = BackgroundHost(unit2.Host, unit2.SERVERS)
    model = unit2.llm()                        # (client, model) or None
    session_id = uuid.uuid4().hex
    messages = [{"role": "system", "content": unit2.SYSTEM}]
    mo.vstack([
        mo.md(f"**Connected:** {', '.join(bg.host.clients)}. **Mode:** "
              + ("chat with the model" if model else "manual (no model endpoint configured)")),
        mo.ui.table([{"tool": k, "server": s, "description": (t.description or "").split("\n")[0]}
                     for k, (s, t) in bg.host.tools.items()], selection=None),
    ])
    return bg, messages, model, session_id


@app.cell
def _(mo):
    get_transcript, set_transcript = mo.state([])
    get_job, set_job = mo.state(None)            # the turn or call in flight, if paused
    return get_job, get_transcript, set_job, set_transcript


@app.cell
def _(bg, get_transcript, set_job, set_transcript):
    def settle(job, label):
        """After waiting: record the finished job, or keep it parked while a server waits."""
        if bg.wait(job):
            set_transcript(get_transcript() + [(label, job.result())])
            set_job(None)
        else:
            set_job(job)

    return (settle,)


@app.cell
def _(bg, json, mo, model):
    question = mo.ui.text_area(placeholder="e.g. Who owns jump-prod-02, and what depends on it? "
                               "Then open a ticket for it.", full_width=True, rows=2)
    tool_pick = mo.ui.dropdown(options=list(bg.host.tools), value=list(bg.host.tools)[0],
                               label="tool")
    tool_args = mo.ui.text(value=json.dumps({"hostname": "jump-prod-02"}), label="arguments (JSON)",
                           full_width=True)
    go = mo.ui.run_button(label="Send" if model else "Call")
    mo.vstack([question, go] if model else [mo.hstack([tool_pick, tool_args]), go])
    return go, question, tool_args, tool_pick


@app.cell
def _(
    bg,
    get_transcript,
    go,
    json,
    messages,
    mo,
    model,
    question,
    session_id,
    set_transcript,
    settle,
    tool_args,
    tool_pick,
    unit2,
    uuid,
):
    mo.stop(not go.value)
    if model:
        _client, _model = model
        set_transcript(get_transcript() + [("you", question.value)])
        messages.append({"role": "user", "content": question.value})
        _job = bg.submit(unit2.run_turn(bg.host, _client, _model, messages, session_id,
                                        on_event=bg.events.append))
        settle(_job, "assistant")
    else:
        async def _manual(key, raw):
            _text, _ev = await bg.host.call(key, json.loads(raw), session_id,
                                            uuid.uuid4().hex, len(bg.events) + 1)
            bg.events.append(_ev)
            return _text
        set_transcript(get_transcript() + [("you", f"{tool_pick.value}({tool_args.value})")])
        settle(bg.submit(_manual(tool_pick.value, tool_args.value)), "result")
    return


@app.cell
def _(bg, get_job, mo):
    get_job()                                    # re-run when a job parks or finishes
    if bg.pending is None:
        approval_fields, accept, decline = None, None, None
        _view = mo.md("")
    else:
        _server, _params, _ = bg.pending
        _props = _params.requested_schema.get("properties", {})
        approval_fields = mo.ui.dictionary({
            k: (mo.ui.dropdown(options=v["enum"], value=v["enum"][-1], label=k) if "enum" in v
                else mo.ui.text(label=k))
            for k, v in _props.items()})
        accept = mo.ui.run_button(label="Accept", kind="success")
        decline = mo.ui.run_button(label="Decline", kind="danger")
        _view = mo.callout(mo.vstack([
            mo.md(f"**The `{_server}` server is asking you:**\n\n{_params.message}"),
            approval_fields, mo.hstack([accept, decline], justify="start")]), kind="warn")
    _view
    return accept, approval_fields, decline


@app.cell
def _(accept, approval_fields, bg, decline, get_job, mo, settle):
    mo.stop(accept is None or not (accept.value or decline.value))
    from mcp.types import ElicitResult

    if accept.value:
        bg.answer(ElicitResult(action="accept", content=dict(approval_fields.value)))
    else:
        bg.answer(ElicitResult(action="decline"))
    settle(get_job(), "assistant" if get_job() is not None else "result")
    return


@app.cell
def _(bg, get_job, get_transcript, mo):
    get_job()
    _chat = [mo.md(f"**{who}:** {text}") for who, text in get_transcript()] or [
        mo.md("_Nothing yet. Ask something, or call a tool._")]
    if get_job() is not None:
        _chat.append(mo.md("_Paused: waiting for your answer above._"))
    _calls = [{"step": e.step, "server": e.server, "tool": e.tool, "args": ", ".join(e.arg_keys),
               "outcome": e.outcome, "asked you": "yes" if e.asked_user else "",
               "ms": e.ms, "bytes": e.result_bytes} for e in bg.events]
    mo.hstack([
        mo.vstack([mo.md("### Conversation")] + _chat),
        mo.vstack([mo.md("### Tool calls"),
                   mo.ui.table(_calls, selection=None) if _calls else mo.md("_None yet._")]),
    ], widths=[3, 2], align="start")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Things to try

    1. Ask who owns `jump-prod-02` and what depends on it. Count the tool calls the model needs,
       and compare its answer with what the tools actually returned.
    2. Ask it to open a ticket. When the form appears, **decline**. What does the model tell you,
       and does it try again? Then ask again and accept as P1.
    3. Ask for a host that doesn't exist (`jump-prod-2`). Does the model recover using the
       hint in the error, or does it give up?
    4. Watch the `bytes` column. `find_assets` with a high `limit` returns far more than
       `get_asset`. Everything in that column goes into the model's context.
    """)
    return


if __name__ == "__main__":
    app.run()
