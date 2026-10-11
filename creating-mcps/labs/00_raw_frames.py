import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 0 lab: raw frames and three personas",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 0 lab: raw frames and three personas

    Two experiments, neither of which needs the model endpoint.

    1. **Raw frames.** Start Foundations §18's hand-rolled server and send it JSON-RPC
       requests one at a time. Every frame in both directions lands in a table with its
       round-trip time. This is the 2024-era protocol, handshake and all; Unit 1 puts the
       2026-07-28 version next to it.
    2. **Three personas.** The same two questions asked of the estate as different people.
       This half needs the estate (`python env/env.py up && python env/env.py seed`).

    Run it from `creating-mcps/`: `cp labs/00_raw_frames.py work/ && marimo edit work/00_raw_frames.py`.
    """)
    return


@app.cell
def _():
    import importlib.util
    import json
    import subprocess
    import sys
    import time

    import marimo as mo

    # The notebook lives in labs/ or, once copied, in work/. Either way its parent is
    # creating-mcps/, and the repo root (with Foundations' examples/) is one level above that.
    COURSE_DIR = mo.notebook_dir().parent
    REPO_DIR = COURSE_DIR.parent
    for _p in (mo.notebook_dir(), COURSE_DIR / "examples"):
        if str(_p) not in sys.path:
            sys.path.append(str(_p))
    return COURSE_DIR, REPO_DIR, importlib, json, mo, subprocess, sys, time


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Part 1: raw frames

    The cell below spawns `examples/18/mcp_server.py` as a child process, exactly as a host
    would: the server reads one JSON object per line on stdin and answers on stdout. Pick a
    request and press **Send**. Things to try:

    - Call `tools/list` *before* `initialize`. This little server doesn't care; a real 2024
      server would refuse. Unit 1 explains why the 2026 protocol dropped the handshake.
    - Send `notifications/initialized`. No reply comes back, because a JSON-RPC message
      without an `id` is a notification.
    - Call `calculate` with `__import__('os')`. The tool fails, but the server doesn't:
      the error comes back as a *result* with `isError: true` (Foundations §21).
    """)
    return


@app.cell
def _(REPO_DIR, mo, subprocess, sys):
    SERVER = REPO_DIR / "examples" / "18" / "mcp_server.py"
    server = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
    )
    mo.md(f"Server started: `{SERVER.relative_to(REPO_DIR)}`, pid {server.pid}.")
    return (server,)


@app.cell
def _(mo):
    get_frames, set_frames = mo.state([])
    get_next_id, set_next_id = mo.state(1)
    return get_frames, get_next_id, set_frames, set_next_id


@app.cell
def _(mo):
    request_kind = mo.ui.dropdown(
        options=[
            "initialize",
            "notifications/initialized",
            "tools/list",
            "tools/call calculate",
            "tools/call doc_search",
            "unknown/method",
        ],
        value="initialize",
        label="request",
    )
    argument = mo.ui.text(value="6 * 7", label="argument (for tools/call)", full_width=True)
    send = mo.ui.run_button(label="Send")
    mo.hstack([request_kind, argument, send], justify="start", align="end")
    return argument, request_kind, send


@app.cell
def _(argument, request_kind):
    def build_request(kind, arg, req_id):
        """The JSON-RPC frame for one dropdown choice (None id = a notification)."""
        if kind == "initialize":
            return {"jsonrpc": "2.0", "id": req_id, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "unit0-lab", "version": "0.1"}}}
        if kind == "notifications/initialized":
            return {"jsonrpc": "2.0", "method": "notifications/initialized"}
        if kind == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "method": "tools/list"}
        if kind.startswith("tools/call"):
            tool = kind.split()[1]
            key = "expression" if tool == "calculate" else "query"
            return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                    "params": {"name": tool, "arguments": {key: arg}}}
        return {"jsonrpc": "2.0", "id": req_id, "method": kind}

    preview = build_request(request_kind.value, argument.value, "<next id>")
    return build_request, preview


@app.cell
def _(
    argument,
    build_request,
    get_frames,
    get_next_id,
    json,
    request_kind,
    send,
    server,
    set_frames,
    set_next_id,
    time,
):
    if send.value:
        _req_id = get_next_id()
        _request = build_request(request_kind.value, argument.value, _req_id)
        _new = []
        _t0 = time.perf_counter()
        server.stdin.write(json.dumps(_request) + "\n")
        server.stdin.flush()
        _new.append({"dir": "->", "id": _request.get("id", "-"), "method": _request["method"],
                     "ms": "", "frame": json.dumps(_request)})
        if "id" in _request:              # only requests get an answer; notifications don't
            _line = server.stdout.readline()
            _ms = (time.perf_counter() - _t0) * 1000
            _new.append({"dir": "<-", "id": _req_id, "method": _request["method"],
                         "ms": f"{_ms:.2f}", "frame": _line.strip()})
            set_next_id(_req_id + 1)
        set_frames(get_frames() + _new)
    return


@app.cell
def _(get_frames, json, mo, preview):
    _frames = get_frames()
    mo.vstack([
        mo.md("**Next frame to send:**"),
        mo.ui.code_editor(value=json.dumps(preview, indent=2),
                          language="json", disabled=True),
        mo.md(f"**Frames so far:** {len(_frames)}"),
        mo.ui.table(_frames, selection=None, page_size=10) if _frames
        else mo.md("_Nothing sent yet._"),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    Notice the round-trip times: a local stdio call costs well under a millisecond of
    protocol. In later units the time goes to the backend (a query, a search), the
    model, and the network. The protocol is almost never the bottleneck. *What the tool
    does* is.

    ## Part 2: the same question, three answers

    This is `estate_tour.py` with a persona picker. If you wrote `work/estate_tour.py` in the
    lesson, the lab uses yours; otherwise it uses the reference in `examples/00/`.
    """)
    return


@app.cell
def _(COURSE_DIR, importlib, mo):
    _mine = COURSE_DIR / "work" / "estate_tour.py"
    TOUR_FILE = _mine if _mine.exists() else COURSE_DIR / "examples" / "00" / "estate_tour.py"
    _spec = importlib.util.spec_from_file_location("estate_tour", TOUR_FILE)
    tour = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(tour)
    persona = mo.ui.dropdown(options=tour.PERSONAS + ["mcp_service"], value="l1_analyst",
                             label="ask as")
    mo.vstack([mo.md(f"Using `{TOUR_FILE.relative_to(COURSE_DIR)}`."), persona])
    return persona, tour


@app.cell
def _(mo, persona, tour):
    def _ask(fn):
        try:
            return fn(persona.value), None
        except Exception as e:  # the estate is down, or this persona is refused outright
            return None, f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"

    _cmdb, _cmdb_err = _ask(tour.cmdb_view)
    _siem, _siem_err = _ask(tour.siem_view)

    _cards = []
    if _cmdb:
        _cards += [mo.stat(value=str(_cmdb[0]), label="CMDB assets visible"),
                   mo.stat(value=_cmdb[1], label="people.email")]
    if _siem:
        _cards += [mo.stat(value=f"{_siem[0]:,}", label="log documents visible"),
                   mo.stat(value=_siem[1], label="EDR telemetry"),
                   mo.stat(value=_siem[2], label="user.email looks like")]
    _notes = [mo.callout(mo.md(f"**{name}:** `{err}`"), kind="warn")
              for name, err in (("Postgres", _cmdb_err), ("OpenSearch", _siem_err)) if err]
    if _notes and not _cards:
        _notes.append(mo.md(
            "Is the estate up? From `creating-mcps/`: `python env/env.py status`, then "
            "`python env/env.py up && python env/env.py seed`. `python env/env.py check` "
            "says what's wrong and which units it affects."))
    mo.vstack([mo.hstack(_cards, justify="start", wrap=True)] + _notes)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    Try `mcp_service`, the gateway's own login. Postgres and OpenSearch both refuse it: it
    holds no privileges of its own and must act as a caller first (`SET ROLE` in Postgres,
    impersonation in OpenSearch; Challenge 3). That is the pattern Unit 7 builds on. A server that connects as a superuser throws away everything
    this table just showed you.
    """)
    return


if __name__ == "__main__":
    app.run()
