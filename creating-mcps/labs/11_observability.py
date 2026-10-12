import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 11 lab: the gateway's own dashboard",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 11 lab: the gateway's own dashboard

    Run a compressed day at the SOC against the observed servers
    (`examples/11/traffic.py`): an L1 analyst, an L3 responder, a contractor with no grants,
    and a team of agents, every call carrying its session, trace and step. Then read what
    the gateway recorded, the way an operator and an auditor would: a dashboard built from
    the telemetry in OpenSearch, one investigation followed end to end, and the audit log.

    It needs the estate (`python env/env.py check`). No model is needed.
    """)
    return


@app.cell
def _():
    import importlib.util
    import sys

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "11"), str(EXAMPLES / "08"), str(EXAMPLES / "07"),
                    str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    telemetry = _load("telemetry", EXAMPLES / "11" / "telemetry.py")
    audit = _load("audit", EXAMPLES / "11" / "audit.py")
    metrics = _load("metrics", EXAMPLES / "11" / "metrics.py")
    traffic = _load("traffic", EXAMPLES / "11" / "traffic.py")
    sdk_spans = _load("sdk_spans", EXAMPLES / "11" / "sdk_spans.py")
    from common_mcp import load_env, os_client

    load_env()
    return audit, metrics, mo, os_client, sdk_spans, telemetry, traffic


@app.cell
def _(mo):
    COLORS = ["#2563eb", "#dc2626", "#d97706", "#16a34a"]

    def bars(series: dict, title: str, height: int = 170):
        """Stacked bars, one per bucket: {name: [y0, y1, ...]} (same length each)."""
        w, h, left, bottom, top = 640, height, 44, 22, 24
        n = max((len(v) for v in series.values()), default=0)
        if not n:
            return mo.md(f"_{title}: nothing to show._")
        totals = [sum(v[i] for v in series.values()) for i in range(n)]
        ymax = max(totals) or 1
        bw = (w - left - 8) / n

        def py(y):
            return h - bottom - (h - bottom - top) * y / ymax

        out = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px;'
               'font:11px sans-serif">',
               f'<text x="{left}" y="10" font-weight="600">{title}</text>',
               f'<line x1="{left}" y1="{py(0)}" x2="{w - 8}" y2="{py(0)}" stroke="#999"/>',
               f'<text x="{left - 4}" y="{py(ymax) + 4}" text-anchor="end">{ymax:,.0f}</text>',
               f'<text x="{left - 4}" y="{py(0)}" text-anchor="end">0</text>']
        base = [0] * n
        legend_x = left + 8
        for k, (name, ys) in enumerate(series.items()):
            color = COLORS[k % len(COLORS)]
            for i, y in enumerate(ys):
                if y:
                    out.append(f'<rect x="{left + i * bw + 1:.1f}" y="{py(base[i] + y):.1f}" '
                               f'width="{max(1, bw - 2):.1f}" '
                               f'height="{py(base[i]) - py(base[i] + y):.1f}" fill="{color}"/>')
                base[i] += y
            out.append(f'<rect x="{legend_x}" y="{h - 14}" width="9" height="9" '
                       f'fill="{color}"/><text x="{legend_x + 12}" y="{h - 6}">{name}</text>')
            legend_x += 20 + 7 * len(name)
        out.append("</svg>")
        return mo.Html("".join(out))

    return (bars,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. A day at the SOC, compressed

    Pick how many agents join the three people, and whether the audit log fails closed (no
    row, no result) or open. Each run adds to the same telemetry indexes and audit table, so
    the dashboard below covers every run in its window.
    """)
    return


@app.cell
def _(mo):
    agents = mo.ui.slider(0, 12, value=4, label="Agents")
    seconds = mo.ui.slider(5, 40, value=15, step=5, label="Seconds")
    fail = mo.ui.dropdown(["closed", "open"], value="closed", label="Audit fails")
    go = mo.ui.run_button(label="Run the day")
    mo.hstack([agents, seconds, fail, go], justify="start")
    return agents, fail, go, seconds


@app.cell
async def _(agents, fail, go, mo, seconds, traffic):
    mo.stop(not go.value, mo.md("_Press **Run the day**._"))
    with mo.status.spinner(title="Starting the observed servers and running the day..."):
        day = await traffic.run(agents.value, seconds.value,
                                env={"MCP_AUDIT_FAIL": fail.value})
    mo.ui.table(day["summary"], selection=None)
    return (day,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. The dashboard

    Every panel is one OpenSearch aggregation over `mcp-telemetry-*`, read as the auditor.
    A refusal (the policy said no, or a limit did) is counted apart from an error: the server
    did its job by saying no. The objective counts only the calls the policy allowed.
    """)
    return


@app.cell
def _(mo):
    window = mo.ui.dropdown(["now-15m", "now-1h", "now-24h"], value="now-15m", label="Window")
    slo_ms = mo.ui.slider(100, 2000, value=500, step=100, label="Objective: answered within (ms)")
    refresh = mo.ui.button(label="Refresh")
    mo.hstack([window, slo_ms, refresh], justify="start")
    return refresh, slo_ms, window


@app.cell
def _(bars, metrics, mo, refresh, slo_ms, window):
    refresh
    _since = window.value
    _interval = {"now-15m": "30s", "now-1h": "2m", "now-24h": "1h"}[_since]
    _t = [b for b in metrics.over_time(_since, _interval)]
    _first = next((i for i, b in enumerate(_t) if b["calls"]), len(_t))
    _t = _t[_first:]
    _ok = [b["calls"] - b["errors"] - b["refused"] for b in _t]
    _slo = metrics.slo(_since, 0.99, slo_ms.value)
    _spent = _slo["budget_spent"]
    mo.vstack([
        mo.hstack([
            mo.stat(_slo["allowed_calls"], label="Allowed calls"),
            mo.stat(f"{_slo['achieved']:.2%}" if _slo["achieved"] is not None else "-",
                    label=_slo["objective"]),
            mo.stat(f"{_spent:.0%}" if _spent is not None else "-",
                    label="Error budget spent",
                    caption=f"{_slo['failed']} failed, {_slo['slow']} slow"),
        ], justify="start"),
        bars({"answered": [max(0, x) for x in _ok],
              "tool errors and failures": [b["errors"] for b in _t],
              "refused": [b["refused"] for b in _t]},
             f"tools/call per {_interval}"),
        mo.md("**By tool**"),
        mo.ui.table(metrics.by_tool(_since), selection=None),
        mo.md("**By caller**"),
        mo.ui.table(metrics.by_caller(_since), selection=None),
        mo.hstack([
            mo.vstack([mo.md("**Why calls didn't run**"),
                       mo.ui.table(metrics.refusals(_since), selection=None)]),
            mo.vstack([mo.md("**Requests by method**"),
                       mo.ui.table(metrics.methods(_since), selection=None)]),
        ], justify="start", gap=2),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. One investigation, end to end

    Pick a trace from the last run. The client's own record, the server's telemetry and the
    audit rows share its trace id, so the three line up step by step. The last column is the
    same telemetry record as an OpenTelemetry span.
    """)
    return


@app.cell
def _(day, mo):
    _traces = {}
    for _c in day["calls"]:
        _traces.setdefault(f"{_c['who']} · {_c['playbook']} · {_c['trace_id'][:8]}",
                           _c["trace_id"])
    pick = mo.ui.dropdown(_traces, value=next(iter(_traces)), label="Trace")
    pick
    return (pick,)


@app.cell
def _(audit, day, mo, os_client, pick, telemetry):
    _tid = pick.value
    _client = [{k: c[k] for k in ("step", "server", "tool", "outcome", "ms")}
               for c in day["calls"] if c["trace_id"] == _tid]
    _hits = os_client("auditor").search(index="mcp-telemetry-*", body={
        "size": 50, "query": {"bool": {"filter": [{"term": {"trace_id": _tid}},
                                                  {"term": {"method": "tools/call"}}]}},
        "sort": [{"step": "asc"}]})["hits"]["hits"]
    _server = [{k: h["_source"].get(k) for k in ("step", "server", "name", "caller", "verdict",
                                                 "rule", "outcome", "ms", "pid")}
               for h in _hits]
    _spans = [telemetry.to_span(h["_source"]) for h in _hits]
    mo.vstack([
        mo.md("**What the client recorded**"), mo.ui.table(_client, selection=None),
        mo.md("**What the servers recorded** (telemetry)"),
        mo.ui.table(_server, selection=None),
        mo.md("**What the audit log kept**"), mo.ui.table(audit.trace(_tid), selection=None),
        mo.md("**The same records as spans**"),
        mo.ui.table([{"name": s["name"], "parent": s["parent_span_id"], "status": s["status"],
                      **{k: v for k, v in s["attributes"].items()
                         if k.startswith(("mcp.", "gen_ai.", "error"))}} for s in _spans],
                    selection=None),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. The auditor's question

    "Who looked at this host, as whom, and was it allowed?" One query on `audit.tool_calls`,
    as the auditor, who may read it and nothing else.
    """)
    return


@app.cell
def _(mo):
    host = mo.ui.text(value="db-prod-01", label="Host")
    host
    return (host,)


@app.cell
def _(audit, day, host, mo):
    day
    mo.ui.table(audit.who_looked_at(host.value, "1 day"), selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. The spans the SDK already makes

    The MCP SDK opens a client span for every request it sends and a server span for every
    request it answers, and carries the trace in `_meta`. With a tracer provider configured
    (here, in memory) they're real, and they agree with the joinable record.
    """)
    return


@app.cell
async def _(mo, sdk_spans):
    _out = await sdk_spans.run()
    mo.vstack([
        mo.ui.table([{k: s[k] for k in ("name", "kind", "trace_id", "span_id",
                                        "parent_span_id")} for s in _out["spans"]],
                    selection=None),
        mo.md("\n".join(f"- {'✅' if ok else '❌'} {what}"
                        for what, ok in sdk_spans.checks(_out))),
    ])
    return


if __name__ == "__main__":
    app.run()
