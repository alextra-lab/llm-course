import marimo

__generated_with = "0.25.1"
app = marimo.App(width="medium", app_title="Unit 1 lab: a protocol inspector")


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 1 lab: a protocol inspector

    Two servers run side by side as child processes:

    - **§18's hand-rolled server**, which speaks the 2024 protocol: an `initialize` handshake
      first, then bare requests.
    - **Your Unit 1 CMDB server** (`examples/01/cmdb_server.py`), built on the SDK v2. It speaks
      2026-07-28: no handshake, and every request carries its own `_meta` envelope.

    Part 1 lets you hand-write requests to either one and see every frame. Part 2 runs the real
    SDK client through the frame tap, so you can see what a client actually sends. Neither part
    needs the model endpoint or the estate.
    """)
    return


@app.cell
def _():
    import json
    import subprocess
    import sys
    import tempfile
    import time
    from pathlib import Path

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    REPO_DIR = COURSE_DIR.parent
    EXAMPLES = COURSE_DIR / "examples"
    LOG_DIR = Path(tempfile.mkdtemp(prefix="unit1-lab-"))
    return EXAMPLES, LOG_DIR, REPO_DIR, json, mo, subprocess, sys, time


@app.cell
def _(json, subprocess, time):
    class RawServer:
        """A stdio server we talk to by hand: one JSON object per line each way."""

        def __init__(self, label, command, stderr_path):
            self.label = label
            self._err = open(stderr_path, "w")
            self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=self._err, text=True, bufsize=1)

        def send(self, frame):
            """Write one frame; read the reply unless it's a notification. Returns (reply, ms)."""
            t0 = time.perf_counter()
            self.proc.stdin.write(json.dumps(frame) + "\n")
            self.proc.stdin.flush()
            if "id" not in frame:
                return None, None
            line = self.proc.stdout.readline()
            return json.loads(line), (time.perf_counter() - t0) * 1000

    return (RawServer,)


@app.cell
def _(EXAMPLES, LOG_DIR, REPO_DIR, RawServer, mo, sys):
    LEGACY = "§18 server (2024)"
    MODERN = "Unit 1 server (2026-07-28)"
    servers = {
        LEGACY: RawServer(LEGACY, [sys.executable, str(REPO_DIR / "examples" / "18" / "mcp_server.py")],
                          LOG_DIR / "legacy.stderr"),
        MODERN: RawServer(MODERN, [sys.executable, str(EXAMPLES / "01" / "cmdb_server.py")],
                          LOG_DIR / "modern.stderr"),
    }
    mo.md(f"Started **{LEGACY}** (pid {servers[LEGACY].proc.pid}) and "
          f"**{MODERN}** (pid {servers[MODERN].proc.pid}).")
    return LEGACY, MODERN, servers


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Part 1: hand-written frames

    Pick a request and a target, then press **Send**. Things to try, in order:

    1. Send `tools/list` to both. The 2024 server answers a bare request. The 2026 server
       refuses it unless the `_meta` envelope is there. Turn the envelope off and on.
    2. Send `initialize` to the 2026 server. It refuses: a connection that started on
       2026-07-28 does not take the old handshake. (Restart the notebook and send `initialize`
       *first*, and the same server will speak the old protocol. The SDK serves both eras,
       decided by the first frame.)
    3. Send `server/discover` to the 2026 server and read what comes back: supported versions,
       capabilities, instructions, and cache hints (`ttlMs`, `cacheScope`).
    4. Change the protocol version to `2099-01-01`. You get error `-32022` with the versions
       the server does support.
    5. Call `get_asset` with a typo in the hostname. A tool failure is a *result* with
       `isError: true`, written so a model can recover from it. It is not a protocol error.
    """)
    return


@app.cell
def _(LEGACY, MODERN, mo):
    request_kind = mo.ui.dropdown(
        options=["initialize", "server/discover", "tools/list", "tools/call get_asset",
                 "resources/read cmdb://services", "prompts/get triage_host"],
        value="tools/list", label="request")
    target = mo.ui.radio(options=[LEGACY, MODERN, "both"], value="both", label="send to")
    hostname = mo.ui.text(value="jump-prod-02", label="hostname")
    envelope = mo.ui.checkbox(value=True, label="2026: include the _meta envelope")
    version = mo.ui.dropdown(options=["2026-07-28", "2025-06-18", "2099-01-01"],
                             value="2026-07-28", label="2026: protocolVersion")
    send = mo.ui.run_button(label="Send")
    mo.vstack([
        mo.hstack([request_kind, hostname, send], justify="start", align="end"),
        mo.hstack([target], justify="start"),
        mo.hstack([envelope, version], justify="start", align="center"),
    ])
    return envelope, hostname, request_kind, send, target, version


@app.cell
def _(LEGACY):
    def build_frame(server, kind, req_id, host, with_envelope, protocol_version):
        """The JSON-RPC frame for one request, in the dialect of the server's era."""
        if kind == "initialize":
            return {"jsonrpc": "2.0", "id": req_id, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "unit1-lab", "version": "0.1"}}}
        method, _, target_name = kind.partition(" ")
        params = {}
        if method == "tools/call":
            key = "hostname" if server != LEGACY else "query"
            name = target_name if server != LEGACY else "doc_search"
            params = {"name": name, "arguments": {key: host}}
        elif method == "resources/read":
            params = {"uri": target_name}
        elif method == "prompts/get":
            params = {"name": target_name, "arguments": {"hostname": host}}
        if server != LEGACY and with_envelope:
            params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": protocol_version,
                "io.modelcontextprotocol/clientInfo": {"name": "unit1-lab", "version": "0.1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

    return (build_frame,)


@app.cell
def _(mo):
    get_frames, set_frames = mo.state([])
    get_next_id, set_next_id = mo.state(1)
    return get_frames, get_next_id, set_frames, set_next_id


@app.cell
def _(
    LEGACY,
    MODERN,
    build_frame,
    envelope,
    get_frames,
    get_next_id,
    hostname,
    json,
    request_kind,
    send,
    servers,
    set_frames,
    set_next_id,
    target,
    version,
):
    if send.value:
        _rows = []
        _req_id = get_next_id()
        for _name in ([LEGACY, MODERN] if target.value == "both" else [target.value]):
            _frame = build_frame(_name, request_kind.value, _req_id, hostname.value,
                                 envelope.value, version.value)
            _reply, _ms = servers[_name].send(_frame)
            _rows.append({"server": _name, "dir": "->", "method": _frame["method"],
                          "ms": "", "bytes": len(json.dumps(_frame)), "frame": json.dumps(_frame)})
            if _reply is not None:
                _kind = ("error " + str(_reply["error"]["code"]) if "error" in _reply
                         else "isError" if _reply.get("result", {}).get("isError") else "result")
                _rows.append({"server": _name, "dir": "<-", "method": _kind,
                              "ms": f"{_ms:.2f}", "bytes": len(json.dumps(_reply)),
                              "frame": json.dumps(_reply)})
        set_next_id(_req_id + 1)
        set_frames(get_frames() + _rows)
    return


@app.cell
def _(get_frames, json, mo):
    _frames = get_frames()
    _last = _frames[-1]["frame"] if _frames else None
    mo.vstack([
        mo.ui.table(_frames, selection=None, page_size=10) if _frames
        else mo.md("_Nothing sent yet._"),
        mo.md("**Last reply, pretty-printed:**") if _last else mo.md(""),
        mo.ui.code_editor(value=json.dumps(json.loads(_last), indent=2), language="json",
                          disabled=True) if _last else mo.md(""),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    Notice the byte column. A 2026 request is several times the size of its 2024 twin,
    because it carries the version, client info and capabilities every time. That is the
    price of statelessness: any server instance can answer any request, because nothing
    about the caller lives on the server between requests. Unit 9 cashes that in when you
    run three instances behind a load balancer.

    The Unit 1 server also writes one joinable log line per request to **stderr**. Its stdout
    is the protocol channel, so a stray `print()` there would corrupt the stream. Here are its
    log lines so far (refresh by sending another request):
    """)
    return


@app.cell
def _(LOG_DIR, get_frames, json, mo):
    get_frames()  # re-run whenever a frame is sent
    _records = []
    for _line in (LOG_DIR / "modern.stderr").read_text().splitlines():
        if _line.startswith("{"):
            _records.append(json.loads(_line))
    mo.ui.table(_records, selection=None, page_size=5) if _records else mo.md(
        "_No log lines yet._")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Part 2: what a real client sends

    `examples/01/client.py` drives the same server with the SDK v2 `Client`. Here it runs
    through `examples/01/frame_tap.py`, which records every frame. Questions to answer from the
    table:

    - What is the very first request, and what does the client do with the answer?
    - `client.py` calls `list_tools()` **twice**. How many `tools/list` frames went over the
      wire, and why?
    - Which requests carry a `traceparent`? Find the same `trace_id` in the server's log
      lines below.
    """)
    return


@app.cell
def _(mo):
    run_client = mo.ui.run_button(label="Run client.py through the tap")
    run_client
    return (run_client,)


@app.cell
def _(EXAMPLES, LOG_DIR, json, mo, run_client, subprocess, sys):
    mo.stop(not run_client.value, mo.md("_Press the button to run the client._"))
    _tap = LOG_DIR / "client-frames.jsonl"
    _tap.unlink(missing_ok=True)
    _run = subprocess.run([sys.executable, str(EXAMPLES / "01" / "client.py"), "--tap", str(_tap)],
                          capture_output=True, text=True, timeout=60)
    _frames = []
    for _line in _tap.read_text().splitlines() if _tap.exists() else []:
        _f = json.loads(_line)
        _body = _f["frame"] if isinstance(_f["frame"], dict) else {}
        _meta = (_body.get("params") or {}).get("_meta") or {}
        _frames.append({"t_ms": _f["t_ms"], "dir": _f["dir"],
                        "method": _body.get("method", "(reply)"), "id": _body.get("id"),
                        "bytes": _f["bytes"], "traceparent": "yes" if "traceparent" in _meta else "",
                        "frame": json.dumps(_body)})
    _logs = [json.loads(_l) for _l in _run.stderr.splitlines() if _l.startswith("{")]
    mo.vstack([
        mo.md(f"**client.py output** (exit {_run.returncode}):"),
        mo.plain_text(_run.stdout),
        mo.md(f"**{len(_frames)} frames on the wire:**"),
        mo.ui.table(_frames, selection=None, page_size=8),
        mo.md(f"**{len(_logs)} joinable log lines from the server:**"),
        mo.ui.table(_logs, selection=None, page_size=8),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    The second `tools/list` never reached the server: the first answer said `ttlMs=300000`,
    `cacheScope=public`, and the SDK client honoured it. Caching hints are how a stateless
    protocol avoids paying for the same list on every turn. Unit 3 uses the same mechanism for
    the catalog, where the list is far bigger and costs real tokens.
    """)
    return


if __name__ == "__main__":
    app.run()
