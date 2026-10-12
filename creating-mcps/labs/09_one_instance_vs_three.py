import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 9 lab: one instance vs three",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 9 lab: one instance vs three

    Unit 8's servers, packaged to run as several replicas (`examples/09/scaled_server.py`):
    the same port, N processes, limits and an optional cache in Valkey so the replicas share
    them. You'll compare the two transports, find what actually limits throughput before
    adding replicas, watch a cache in the wrong place leak, and measure four OpenSearch
    choices.

    It needs the estate with Valkey (`python env/env.py up`; `check` says if it's missing).
    The numbers in the lesson come from the large seed (`python env/env.py reset --size
    large`); the small one shows the same shapes, smaller.
    """)
    return


@app.cell
def _():
    import importlib.util
    import sys
    import tempfile
    from collections import defaultdict
    from pathlib import Path

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "09"), str(EXAMPLES / "08"), str(EXAMPLES / "07"),
                    str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    loadgen = _load("loadgen", EXAMPLES / "08" / "loadgen.py")
    transports = _load("transports", EXAMPLES / "09" / "transports.py")
    cache_probes = _load("cache_probes", EXAMPLES / "09" / "cache_probes.py")
    os_perf = _load("os_perf", EXAMPLES / "09" / "os_perf.py")
    from common_mcp import load_env

    load_env()
    SCALED = EXAMPLES / "09" / "scaled_server.py"
    LOG_DIR = Path(tempfile.mkdtemp(prefix="unit9-lab-"))
    return (LOG_DIR, SCALED, cache_probes, defaultdict, loadgen, mo, os_perf, transports)


@app.cell
def _(mo):
    COLORS = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#be185d"]

    def chart(series: dict, title: str, unit: str = "", height: int = 160):
        """A small SVG line chart: {name: [(x, y), ...]}."""
        w, h, left, bottom, top = 640, height, 44, 22, 24
        points = [p for s in series.values() for p in s]
        if not points:
            return mo.md(f"_{title}: nothing to show._")
        xmax = max(x for x, _ in points) or 1
        ymax = max(y for _, y in points) or 1

        def px(x):
            return left + (w - left - 8) * x / xmax

        def py(y):
            return h - bottom - (h - bottom - top) * y / ymax

        out = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px;'
               'font:11px sans-serif">',
               f'<text x="{left}" y="10" font-weight="600">{title}</text>',
               f'<line x1="{left}" y1="{py(0)}" x2="{w - 8}" y2="{py(0)}" stroke="#999"/>',
               f'<text x="{left - 4}" y="{py(ymax) + 4}" text-anchor="end">'
               f'{ymax:,.0f}{unit}</text>',
               f'<text x="{left - 4}" y="{py(0)}" text-anchor="end">0</text>',
               f'<text x="{w - 8}" y="{h - 6}" text-anchor="end">{xmax:,.0f} s</text>']
        legend_x = left + 8
        for i, (name, pts) in enumerate(series.items()):
            color = COLORS[i % len(COLORS)]
            if pts:
                path = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in sorted(pts))
                out.append(f'<polyline points="{path}" fill="none" stroke="{color}" '
                           'stroke-width="1.6"/>')
            out.append(f'<rect x="{legend_x}" y="{h - 14}" width="9" height="9" '
                       f'fill="{color}"/><text x="{legend_x + 12}" y="{h - 6}">{name}</text>')
            legend_x += 20 + 7 * len(name)
        out.append("</svg>")
        return mo.Html("".join(out))

    return (chart,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. One server, two transports

    Unit 7's CMDB server, as priya, the same `get_asset` call a hundred times
    (`examples/09/transports.py`): over stdio (a process per host), over HTTP with one client
    kept open, and over HTTP with a new client per call. Per call, the transports are close.
    Look at the other columns: what it costs to start, and what twenty agents cost.
    """)
    return


@app.cell
def _(mo):
    go_transports = mo.ui.run_button(label="Measure both transports")
    go_transports
    return (go_transports,)


@app.cell
async def _(go_transports, mo, transports):
    mo.stop(not go_transports.value, mo.md("_Press **Measure both transports**._"))
    with mo.status.spinner(title="Starting servers and calling..."):
        _rows = await transports.run(100)
    mo.ui.table(_rows, selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Find the bottleneck, then scale it

    This runs the load generator against the scaled servers with the settings below, and
    samples where the machine's CPU goes while it does: OpenSearch, the MCP servers, and the
    load generator itself (all as % of the whole machine).

    Things to try, one at a time:

    1. **Both servers, 1 replica, then 3.** Throughput barely moves. Which line in the CPU
       chart is at the top?
    2. **Cache shared**, 1 replica. The same agents, the same questions; count the hits.
    3. **Round "now" off**, cache shared. What happens to the hit rate, and why.
    4. **CMDB only**, think 0.02, 1 replica then 3. Now the server is the bottleneck, and a
       second and third replica help.
    5. **3 replicas, a runaway with 16 loops, shared limits off**, then on. Compare the
       runaway's successful calls with Unit 8's 128.
    """)
    return


@app.cell
def _(mo):
    only = mo.ui.dropdown(options={"both servers": "both", "CMDB only": "cmdb",
                                   "SIEM only": "siem"}, value="both servers", label="calls to")
    callers = mo.ui.slider(1, 80, value=60, label="callers", show_value=True)
    think = mo.ui.dropdown(options=["0.02", "0.05", "0.1", "0.3"], value="0.05",
                           label="think (s)")
    seconds = mo.ui.slider(4, 20, value=10, label="seconds", show_value=True)
    workers = mo.ui.slider(1, 4, value=1, label="replicas", show_value=True)
    cache = mo.ui.dropdown(options=["off", "local", "shared"], value="off", label="cache")
    round_now = mo.ui.checkbox(label='round "now" to the minute', value=True)
    shared_limits = mo.ui.checkbox(label="limits shared in Valkey", value=True)
    runaway = mo.ui.slider(0, 32, value=0, label="runaway loops", show_value=True)
    budget = mo.ui.slider(4, 80, value=20, step=2, label="Postgres budget per server",
                          show_value=True)
    go = mo.ui.run_button(label="Run the load")
    mo.vstack([
        mo.hstack([only, callers, think, seconds], justify="start"),
        mo.hstack([workers, cache, round_now], justify="start"),
        mo.hstack([shared_limits, runaway, budget], justify="start"),
        go,
    ])
    return (budget, cache, callers, go, only, round_now, runaway, seconds, shared_limits,
            think, workers)


@app.cell
def _(mo):
    get_runs, set_runs = mo.state([])
    return get_runs, set_runs


@app.cell
async def _(LOG_DIR, SCALED, budget, cache, callers, go, loadgen, mo, only, round_now,
            runaway, seconds, set_runs, shared_limits, think, workers):
    mo.stop(not go.value, mo.md("_Press **Run the load**._"))
    _env = {"MCP_WORKERS": str(workers.value), "MCP_CACHE": cache.value,
            "MCP_ROUND_NOW": "60" if round_now.value else "0",
            "MCP_SHARED_LIMITS": "1" if shared_limits.value else "0",
            "MCP_PG_BUDGET": str(budget.value)}
    if not runaway.value:              # measure throughput, not Unit 8's rate limit
        _env.update(MCP_RATE="100", MCP_BURST="200")
    try:
        with mo.status.spinner(title=f"{callers.value} agents, {workers.value} replica(s)..."):
            run = await loadgen.run(callers.value, seconds.value, float(think.value),
                                    runaway.value, _env, script=SCALED,
                                    only=None if only.value == "both" else only.value,
                                    log_dir=LOG_DIR / f"run{id(_env)}")
    except RuntimeError as e:
        mo.stop(True, mo.callout(mo.md(f"The servers didn't start: `{e}`. If you raised the "
                                       "Postgres budget, read the log it names: the "
                                       "gateway login has a connection limit too."),
                                 kind="danger"))
    set_runs(lambda runs: runs + [run])
    return (run,)


@app.cell
def _(chart, defaultdict, mo, run):
    _calls = run["calls"]
    _end = int(max((c["t"] for c in _calls), default=0))
    _ok = defaultdict(int)
    _p95 = defaultdict(list)
    for _c in _calls:
        if _c["outcome"] == "ok" and _c["t"] < _end:
            _ok[int(_c["t"])] += 1
            _p95[int(_c["t"])].append(_c["ms"])
    _s = [s for s in run["samples"] if 0 <= s["t"] <= _end]
    _cpu = {name: [(s["t"], s[key]) for s in _s if key in s]
            for name, key in (("OpenSearch", "cpu_opensearch"), ("MCP servers", "cpu_servers"),
                              ("load generator", "cpu_loadgen"))}
    mo.vstack([
        mo.ui.table(run["summary"], selection=None),
        mo.hstack([chart({"ok": sorted(_ok.items())}, "successful calls per second"),
                   chart({"p95": [(t, sorted(v)[int(0.95 * (len(v) - 1))])
                                  for t, v in sorted(_p95.items())]},
                         "p95 latency of successful calls", " ms")]),
        mo.hstack([chart(_cpu, "CPU, % of the machine", "%"),
                   chart({"Postgres connections": [(s["t"], s["pg_connections"]) for s in _s],
                          "open search contexts": [(s["t"], s["os_open_contexts"])
                                                   for s in _s]},
                         "backend resources held")]),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. A cache in the wrong place

    Four calls (`examples/09/cache_probes.py`), with the cache where it belongs (inside the
    policy, keyed by persona) and with `UnsafeCache` (in front of the policy, keyed by the
    arguments only). Read the `LEAK` rows: what priya saw, and what kim got without a ticket.
    """)
    return


@app.cell
def _(mo):
    go_probes = mo.ui.run_button(label="Run the probes, both ways")
    go_probes
    return (go_probes,)


@app.cell
async def _(cache_probes, go_probes, mo):
    mo.stop(not go_probes.value, mo.md("_Press **Run the probes, both ways**._"))
    _rows = []
    with mo.status.spinner(title="Probing..."):
        for _mode in ("shared", "unsafe"):
            _rows += await cache_probes.run(_mode)
    mo.ui.table([{**r, "ok": "ok" if r["ok"] else "LEAK"} for r in _rows], selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Four OpenSearch choices

    The same request many times each (`examples/09/os_perf.py`). Watch the request cache hit
    counts in the first rows: as admin, and as a persona.
    """)
    return


@app.cell
def _(mo):
    go_os = mo.ui.run_button(label="Measure")
    go_os
    return (go_os,)


@app.cell
def _(go_os, mo, os_perf):
    mo.stop(not go_os.value, mo.md("_Press **Measure**._"))
    with mo.status.spinner(title="Searching..."):
        _rows = os_perf.run(20)
    mo.ui.table(_rows, selection=None, page_size=12)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. Every run so far
    """)
    return


@app.cell
def _(get_runs, mo):
    _rows = []
    for _i, _r in enumerate(get_runs(), 1):
        _set = _r["settings"]
        for _row in _r["summary"]:
            if _row["who"] == "everyone" and len(_r["summary"]) > 1:
                continue
            _rows.append({"run": _i, "to": _set["only"], "callers": _set["callers"],
                          "replicas": _set.get("MCP_WORKERS"), "cache": _set.get("MCP_CACHE"),
                          "round now": _set.get("MCP_ROUND_NOW"), "runaway": _set["runaway"],
                          "who": _row["who"], "ok/s": _row["ok_per_s"],
                          "p50 ms": _row["p50_ms"], "p95 ms": _row["p95_ms"],
                          "refused": sum(_row["refused"].values())})
    mo.ui.table(_rows, selection=None) if _rows else mo.md("_No runs yet._")
    return


if __name__ == "__main__":
    app.run()
