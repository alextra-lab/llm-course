import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 8 lab: many callers, finite backends",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 8 lab: many callers, finite backends

    Unit 7's servers, inside an operating envelope (`examples/08/envelope_server.py`): shared
    connection pools, per-caller limits, time budgets, cost gates and per-tool caps. You'll
    feel one limit by hand, price queries before they run, then put twenty agents and one
    runaway on the servers and watch what each part of the envelope changes.

    No model is needed: the load generator's agents call tools from a fixed mix. It needs the
    estate (`python env/env.py up` and `seed`), set up for Unit 7 (`python env/env.py check`
    says if `security` still needs to run).
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
    sys.path[:0] = [str(EXAMPLES / "08"), str(EXAMPLES / "07"), str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    limits = _load("limits", EXAMPLES / "08" / "limits.py")
    cost_gates = _load("cost_gates", EXAMPLES / "08" / "cost_gates.py")
    loadgen = _load("loadgen", EXAMPLES / "08" / "loadgen.py")
    from common_mcp import load_env, os_client, pg_connect

    load_env()
    LOG_DIR = Path(tempfile.mkdtemp(prefix="unit8-lab-"))
    return LOG_DIR, cost_gates, defaultdict, limits, loadgen, mo, os_client, pg_connect


@app.cell
def _(mo):
    COLORS = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#be185d"]

    def chart(series: dict, title: str, unit: str = "", height: int = 160, bars=False):
        """A small SVG chart: {name: [(x, y), ...]}, lines (or stacked bars per x)."""
        w, h, left, bottom, top = 640, height, 44, 22, 24
        points = [p for s in series.values() for p in s]
        if not points:
            return mo.md(f"_{title}: nothing to show._")
        xmax = (max(x for x, _ in points) + (1 if bars else 0)) or 1   # a bar fills [x, x+1)
        if bars:
            totals = defaultdict_sum(series)
            ymax = max(totals.values()) or 1
        else:
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
        stack: dict = {}
        for i, (name, pts) in enumerate(series.items()):
            color = COLORS[i % len(COLORS)]
            if bars:
                for x, y in pts:
                    base = stack.get(x, 0)
                    out.append(f'<rect x="{px(x) + 1:.1f}" y="{py(base + y):.1f}" '
                               f'width="{max(1, px(x + 1) - px(x) - 2):.1f}" '
                               f'height="{py(base) - py(base + y):.1f}" fill="{color}"/>')
                    stack[x] = base + y
            elif pts:
                path = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in sorted(pts))
                out.append(f'<polyline points="{path}" fill="none" stroke="{color}" '
                           'stroke-width="1.6"/>')
            out.append(f'<rect x="{legend_x}" y="{h - 14}" width="9" height="9" '
                       f'fill="{color}"/><text x="{legend_x + 12}" y="{h - 6}">{name}</text>')
            legend_x += 20 + 7 * len(name)
        out.append("</svg>")
        return mo.Html("".join(out))

    def defaultdict_sum(series: dict) -> dict:
        totals: dict = {}
        for pts in series.values():
            for x, y in pts:
                totals[x] = totals.get(x, 0) + y
        return totals

    return (chart,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. One caller, one bucket

    A rate limit is a bucket of tokens per caller (`limits.TokenBucket`): each call takes one,
    the bucket refills at `rate` per second and holds at most `burst`. Below, one caller sends
    a burst after a quiet minute, then settles into a steady pace. Move the sliders: what the
    burst size buys, and what a pace just over the rate does (every call after the burst
    refused, or every other one?).
    """)
    return


@app.cell
def _(mo):
    rate = mo.ui.slider(1, 20, value=5, label="rate (calls/s)", show_value=True)
    burst = mo.ui.slider(1, 40, value=10, label="burst", show_value=True)
    burst_calls = mo.ui.slider(0, 60, value=25, label="calls in the opening burst",
                               show_value=True)
    pace = mo.ui.slider(1, 20, value=6, label="then calls/s", show_value=True)
    mo.vstack([mo.hstack([rate, burst], justify="start"),
               mo.hstack([burst_calls, pace], justify="start")])
    return burst, burst_calls, pace, rate


@app.cell
def _(burst, burst_calls, chart, limits, mo, pace, rate):
    _now = [0.0]             # a bucket on simulated time: 20 seconds in no time
    _bucket = limits.TokenBucket(burst.value, rate.value, clock=lambda: _now[0])
    _arrivals = [i * 0.01 for i in range(burst_calls.value)]
    _arrivals += [1 + i / pace.value for i in range(int(19 * pace.value))]
    _ok, _no = {}, {}
    for _t in _arrivals:
        _now[0] = _t
        _into = _no if _bucket.take() else _ok
        _into[int(_t)] = _into.get(int(_t), 0) + 1
    _admitted, _refused = sum(_ok.values()), sum(_no.values())
    mo.vstack([
        chart({"admitted": sorted(_ok.items()), "refused (busy: rate)": sorted(_no.items())},
              "calls per second", bars=True),
        mo.md(f"**{_admitted}** calls admitted, **{_refused}** refused. Each refusal tells "
              "the caller how long until a token is back, so a well-behaved agent waits that "
              "long instead of hammering."),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Price it before it runs

    A timeout stops an expensive query after it has used the backend for its whole budget. A
    cost gate refuses it first, from an estimate (`examples/08/cost_gates.py`). For SQL that's
    the planner's `EXPLAIN`: the query isn't run. Try the examples, then write your own and
    find where the default ceilings (50,000 cost units, 10,000 rows) put it.
    """)
    return


@app.cell
def _(cost_gates, mo):
    example = mo.ui.dropdown(options={label: sql for label, sql, _p in cost_gates.EXAMPLES[1:]},
                             value="every asset with every CVE (no join key)",
                             label="start from")
    example
    return (example,)


@app.cell
def _(example, mo):
    sql = mo.ui.code_editor(value=example.value, language="sql", min_height=80)
    sql
    return (sql,)


@app.cell
def _(cost_gates, mo, pg_connect, sql):
    try:
        with pg_connect("l3_responder") as _conn:
            _est = cost_gates.pg_cost(_conn, sql.value)
        _verdict = ("refused" if _est["cost"] > cost_gates.PG_MAX_COST
                    or _est["rows"] > cost_gates.PG_MAX_ROWS else "allowed")
        _out = mo.md(f"**{_verdict}**: estimated cost **{_est['cost']:,.0f}**, rows "
                     f"**{_est['rows']:,}**, top plan node `{_est['node']}`.")
    except Exception as e:  # noqa: BLE001
        _out = mo.callout(mo.md(f"The planner refused it: `{e}`"), kind="warn")
    _out
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    For logs, the indexes a window touches are known before the search (Unit 4), and so are
    their document counts. That's an upper bound on what the search reads, whatever the
    filters. The envelope server refuses a window above `MCP_MAX_SCAN` documents.
    """)
    return


@app.cell
def _(cost_gates, mo, os_client):
    _client = os_client("l3_responder")
    _rows = []
    for _dataset in ("auth", "web", "syslog"):
        for _start in ("now-1h", "now-24h", "now-7d", "now-21d"):
            _est = cost_gates.os_scan(_client, _dataset, _start)
            _rows.append({"dataset": _dataset, "window": f"{_start} to now",
                          "daily indexes": _est["indexes"], "documents": _est["docs"]})
    mo.ui.table(_rows, selection=None, page_size=12)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Many callers

    This starts both envelope servers with the settings below and runs the load generator
    (`examples/08/loadgen.py`) against them: `callers` agents, each its own L3 identity, each
    calling a realistic mix and thinking between calls. **runaway loops** adds one more agent
    that runs that many loops at once and never thinks: an agent stuck retrying.

    Things to try, one at a time, from the defaults:

    1. **Pools off, limits off** with 60 callers and think 0.1: this is Unit 7, a connection
       per call. Then pools on. Compare p50 and calls per second.
    2. **Limits off, 16 runaway loops.** Look at the open search contexts, and at what the
       *other* agents get back. Then limits on.
    3. **Cap a tool**: `event_histogram=3` with 40 callers and think 0.05.
    4. **Gate the scan**: max scan 2,000 documents. Who is refused, and what do they read?
    """)
    return


@app.cell
def _(mo):
    callers = mo.ui.slider(1, 80, value=20, label="callers", show_value=True)
    seconds = mo.ui.slider(4, 30, value=10, label="seconds", show_value=True)
    think = mo.ui.dropdown(options=["0.05", "0.1", "0.3", "1.0"], value="0.3",
                           label="think (s between calls)")
    runaway = mo.ui.slider(0, 32, value=0, label="runaway loops", show_value=True)
    pool = mo.ui.checkbox(label="shared pools", value=True)
    pg_pool = mo.ui.slider(2, 30, value=10, label="Postgres pool per server", show_value=True)
    use_limits = mo.ui.checkbox(label="per-caller limits", value=True)
    rate_l = mo.ui.slider(1, 50, value=5, label="rate per caller (/s)", show_value=True)
    per_caller = mo.ui.slider(1, 8, value=2, label="in flight per caller", show_value=True)
    total = mo.ui.slider(2, 64, value=16, label="in flight per server", show_value=True)
    cursors_total = mo.ui.slider(10, 300, value=100, step=10,
                                 label="open cursors per server", show_value=True)
    caps = mo.ui.text(value="", label="tool caps", placeholder="event_histogram=3")
    max_scan = mo.ui.number(start=0, stop=100_000_000, value=5_000_000, label="max scan (docs)")
    go = mo.ui.run_button(label="Run the load")
    mo.vstack([
        mo.hstack([callers, seconds, think, runaway], justify="start"),
        mo.hstack([pool, pg_pool], justify="start"),
        mo.hstack([use_limits, rate_l, per_caller, total, cursors_total], justify="start"),
        mo.hstack([caps, max_scan], justify="start"),
        go,
    ])
    return (caps, callers, cursors_total, go, max_scan, per_caller, pg_pool, pool, rate_l,
            runaway, seconds, think, total, use_limits)


@app.cell
def _(mo):
    get_runs, set_runs = mo.state([])
    return get_runs, set_runs


@app.cell
async def _(LOG_DIR, caps, callers, cursors_total, go, loadgen, max_scan, mo, per_caller,
            pg_pool, pool, rate_l, runaway, seconds, set_runs, think, total, use_limits):
    mo.stop(not go.value, mo.md("_Press **Run the load**._"))
    _env = {"MCP_POOL": "1" if pool.value else "0", "MCP_PG_POOL": str(pg_pool.value),
            "MCP_LIMITS": "1" if use_limits.value else "0", "MCP_RATE": str(rate_l.value),
            "MCP_BURST": str(2 * rate_l.value), "MCP_PER_CALLER": str(per_caller.value),
            "MCP_TOTAL": str(total.value), "MCP_CURSORS_TOTAL": str(cursors_total.value),
            "MCP_TOOL_CAPS": caps.value.strip(), "MCP_MAX_SCAN": str(int(max_scan.value))}
    with mo.status.spinner(title=f"{callers.value} agents for {seconds.value} s..."):
        run = await loadgen.run(callers.value, seconds.value, float(think.value),
                                runaway.value, _env, log_dir=LOG_DIR / f"run{id(_env)}")
    set_runs(lambda runs: runs + [run])
    return (run,)


@app.cell
def _(chart, defaultdict, mo, run):
    _end = int(max((c["t"] for c in run["calls"]), default=0))   # drop the last part-second

    def _per_second(calls, keep):
        out = defaultdict(int)
        for c in calls:
            if keep(c) and c["t"] < _end:
                out[int(c["t"])] += 1
        return sorted(out.items())

    _calls = run["calls"]
    _kinds = sorted({c["outcome"] for c in _calls} - {"ok"})
    _who = ["agents"] + (["runaway"] if run["settings"]["runaway"] else [])

    def _mine(who):
        return (lambda c: c["agent"] == "runaway") if who == "runaway" else (
            lambda c: c["agent"] != "runaway")

    _ok = {f"{w}: ok": _per_second(_calls, lambda c, m=_mine(w): m(c) and c["outcome"] == "ok")
           for w in _who}
    _refused = {k: _per_second(_calls, lambda c, k=k: c["outcome"] == k) for k in _kinds}
    _s = [s for s in run["samples"] if 0 <= s["t"] <= _end]
    _p95 = defaultdict(list)
    for c in _calls:
        if c["outcome"] == "ok" and c["t"] < _end:
            _p95[int(c["t"])].append(c["ms"])
    _p95_series = [(t, sorted(v)[int(0.95 * (len(v) - 1))]) for t, v in sorted(_p95.items())]
    mo.vstack([
        mo.ui.table(run["summary"], selection=None),
        mo.hstack([chart(_ok, "successful calls per second"),
                   chart(_refused, "refused calls per second, by reason", bars=True)
                   if _refused else mo.md("_Nothing refused._")]),
        mo.hstack([chart({"p95": _p95_series}, "p95 latency of successful calls", " ms"),
                   chart({"Postgres connections": [(s["t"], s["pg_connections"]) for s in _s],
                          "open search contexts": [(s["t"], s["os_open_contexts"])
                                                   for s in _s]},
                         "backend resources held")]),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    **What the callers read.** One example of each refusal from this run, exactly as the
    agent got it. Each says which limit, and what to do instead: wait (and for how long), or
    ask for less. A refusal that just says "error" gets retried at once, which is how one
    runaway becomes three.
    """)
    return


@app.cell
def _(mo, run):
    _seen = {}
    for _c in run["calls"]:
        if _c["outcome"] != "ok" and _c["outcome"] not in _seen:
            _seen[_c["outcome"]] = {"refusal": _c["outcome"], "tool": _c["tool"],
                                    "agent": _c["agent"], "the caller read": _c["detail"]}
    mo.ui.table(list(_seen.values()), selection=None) if _seen else mo.md(
        "_No refusals in this run._")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Every run so far

    The headline numbers of each run in this session, next to the settings that produced
    them. A good envelope keeps the agents' row steady while the runaway's row falls.
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
            _rows.append({"run": _i, "callers": _set["callers"], "runaway": _set["runaway"],
                          "pool": _set["MCP_POOL"], "limits": _set["MCP_LIMITS"],
                          "caps": _set.get("MCP_TOOL_CAPS", ""), "who": _row["who"],
                          "ok/s": _row["ok_per_s"], "p50 ms": _row["p50_ms"],
                          "p95 ms": _row["p95_ms"],
                          "refused": sum(_row["refused"].values())})
    mo.ui.table(_rows, selection=None) if _rows else mo.md("_No runs yet._")
    return


if __name__ == "__main__":
    app.run()
