import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 4 lab: investigate the planted incident",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 4 lab: investigate the planted incident

    Somewhere in three weeks of logs, someone broke into a jump host. This notebook drives the
    Unit 4 SIEM server (`examples/04/siem_server.py`) through the Unit 2 host, one tool call
    at a time, the way a model would: **incident, then histogram (when), then top values
    (who), then search (what)**. Every call shows what it cost.

    At the end, the model runs the same investigation by itself, and you compare its path with
    yours. It needs the estate (`python env/env.py up` and `seed`); the last section also needs
    the model endpoint.
    """)
    return


@app.cell
def _():
    import asyncio
    import importlib.util
    import json
    import sys
    import threading
    import uuid
    from datetime import timedelta

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "04"), str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    windows = _load("unit4_windows", EXAMPLES / "04" / "windows.py")
    cost = _load("unit4_cost", EXAMPLES / "04" / "cost.py")
    investigate = _load("unit4_investigate", EXAMPLES / "04" / "investigate.py")
    catalog = _load("unit3_catalog", EXAMPLES / "03" / "catalog.py")
    from common_mcp import llm, load_env

    load_env()
    return (asyncio, catalog, cost, investigate, json, llm, mo, threading, timedelta,
            uuid, windows)


@app.cell
def _(asyncio, investigate, json, threading, uuid):
    class Siem:
        """The Unit 4 server behind the Unit 2 host, kept connected on its own event loop.
        `call` returns the parsed result (or {"error": ...}) and remembers what it cost."""

        def __init__(self, persona):
            self.loop = asyncio.new_event_loop()
            threading.Thread(target=self.loop.run_forever, daemon=True).start()
            self.host = investigate.unit2.Host(investigate.SERVERS, investigate._declined,
                                               env={"MCP_PERSONA": persona})
            self._run(self.host.__aenter__())
            self.session, self.calls = uuid.uuid4().hex, []

        def _run(self, coro):
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result(60)

        def call(self, tool, **args):
            text, ev = self._run(self.host.call(f"siem__{tool}", args, self.session,
                                                uuid.uuid4().hex, len(self.calls) + 1))
            self.calls.append({"step": len(self.calls) + 1, "tool": tool,
                               "args": json.dumps(args)[:120], "outcome": ev.outcome,
                               "bytes": ev.result_bytes, "ms": ev.ms})
            return {"error": text.removeprefix("error: ")} if ev.outcome != "ok" else json.loads(text)

    hosts = {}
    return Siem, hosts


@app.cell
def _(mo):
    persona = mo.ui.dropdown(options=["l1_analyst", "l3_responder"], value="l1_analyst",
                             label="act as")
    persona
    return (persona,)


@app.cell
def _(Siem, hosts, persona):
    if persona.value not in hosts:
        hosts[persona.value] = Siem(persona.value)
    siem = hosts[persona.value]
    return (siem,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. Start from the incident

    `list_incidents` shows what's active. Pick one: `get_incident` returns it with a
    **suggested window**, an hour either side of when it was raised. That window is where
    every later call starts.
    """)
    return


@app.cell
def _(mo, siem):
    _active = siem.call("list_incidents", status=["open", "in_progress"], limit=20)
    incident_id = mo.ui.dropdown(options=[i["id"] for i in _active["incidents"]],
                                 value="INC-0042", label="incident")
    mo.vstack([mo.md(f"**{_active['total']} active incidents** (newest first):"),
               mo.ui.table(_active["incidents"], selection=None, page_size=6), incident_id])
    return (incident_id,)


@app.cell
def _(incident_id, mo, siem):
    incident = siem.call("get_incident", incident_id=incident_id.value)
    mo.callout(mo.md("\n".join(f"- **{k}**: {v}" for k, v in incident.items())), kind="info")
    return (incident,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Choose the window

    Every log call takes a window. Edit it: try `now-24h`, a time with no offset (it's read in
    the time zone you give), `2026-10-09T04:00+02:00`, or a window ending before it starts.
    The same window can be fine for an aggregation and refused for a search, which may cover
    at most 24 hours. Only the daily indexes it touches are searched.
    """)
    return


@app.cell
def _(incident, mo):
    start = mo.ui.text(value=incident["suggested_window"]["start"], label="start")
    end = mo.ui.text(value=incident["suggested_window"]["end"], label="end")
    tz = mo.ui.text(value="UTC", label="tz")
    mo.hstack([start, end, tz], justify="start")
    return end, start, tz


@app.cell
def _(end, mo, start, timedelta, tz, windows):
    def _verdict(max_span, tool):
        try:
            w = windows.window(start.value, end.value, tz=tz.value, max_span=max_span,
                               tool=tool)
        except windows.WindowError as e:
            return {"tool": tool, "verdict": "refused", "detail": str(e)}
        return {"tool": tool, "verdict": "ok",
                "detail": f"{w.describe()} -- indexes: "
                          + ", ".join(windows.daily_indexes("logs-auth", w))}

    mo.ui.table([_verdict(windows.RETENTION, "event_histogram / top_values"),
                 _verdict(timedelta(hours=24), "search_events")], selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. When? Count before you read

    A histogram costs the same few hundred tokens whether a million events match or ten.
    Filters are exact values on catalogued fields, as JSON: a list means any-of, and
    `{"gte": 500}` is a range. Find the spike.
    """)
    return


@app.cell
def _(catalog, mo, persona):
    _mine = [d for d, p in {"auth": "logs-auth-*", "web": "logs-web-*",
                            "syslog": "logs-syslog-*", "edr": "edr-events-*"}.items()
             if catalog.describe(p, persona.value)]
    dataset = mo.ui.dropdown(options=_mine, value="auth", label="dataset")
    filters_text = mo.ui.text_area(value='{"event.outcome": "failure"}', label="filters",
                                   rows=2)
    interval = mo.ui.dropdown(options=["auto", "1m", "5m", "15m", "30m", "1h", "3h", "1d"],
                              value="auto", label="interval")
    mo.hstack([dataset, filters_text, interval], justify="start")
    return dataset, filters_text, interval


@app.cell
def _(filters_text, json, mo):
    try:
        filters = json.loads(filters_text.value or "{}")
        _bad = None if isinstance(filters, dict) else "filters must be a JSON object"
    except json.JSONDecodeError as _e:
        filters, _bad = {}, f"filters aren't valid JSON: {_e}"
    mo.stop(_bad is not None, mo.callout(mo.md(str(_bad)), kind="danger"))
    return (filters,)


@app.cell
def _(mo):
    def bars(buckets, height=120, width=640):
        """A bar chart as plain SVG: one bar per bucket, tallest bar labelled."""
        if not buckets:
            return mo.md("_No buckets._")
        top = max(b["count"] for b in buckets) or 1
        step = width / len(buckets)
        rects = "".join(
            f'<rect x="{i * step:.1f}" y="{height - b["count"] / top * height:.1f}" '
            f'width="{max(step - 1, 1):.1f}" height="{b["count"] / top * height:.1f}" '
            f'fill="currentColor" opacity="0.7"><title>{b["start"]}: {b["count"]}'
            f'</title></rect>' for i, b in enumerate(buckets))
        return mo.Html(f'<svg viewBox="0 0 {width} {height + 18}" width="100%" '
                       f'style="max-width:{width}px">{rects}<text x="0" y="{height + 14}" '
                       f'font-size="11" fill="currentColor">{buckets[0]["start"]}</text>'
                       f'<text x="{width}" y="{height + 14}" font-size="11" '
                       f'text-anchor="end" fill="currentColor">{buckets[-1]["start"]}</text>'
                       "</svg>")

    return (bars,)


@app.cell
def _(bars, dataset, end, filters, interval, mo, siem, start, tz):
    _h = siem.call("event_histogram", dataset=dataset.value, start=start.value,
                   end=end.value, tz=tz.value, filters=filters, interval=interval.value)
    mo.stop("error" in _h, mo.callout(_h.get("error", ""), kind="warn"))
    mo.vstack([
        mo.hstack([mo.stat(f"{_h['total']:,}", label="matching events"),
                   mo.stat(_h["interval"], label="bucket"),
                   mo.stat(f"{_h.get('peak', {}).get('count', 0):,}",
                           label=f"peak at {_h.get('peak', {}).get('start', '-')}"),
                   mo.stat(f"{siem.calls[-1]['bytes']:,} B", label="this answer")],
                  justify="start"),
        bars(_h["buckets"]),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Who? The top values behind it

    Same window, same filters. Which addresses, accounts or hosts make up the spike? Try
    `source.ip`, then `user.name`. As `l3_responder` on `web`, try `user.email`: you get
    stable hashes you can count but not filter on.
    """)
    return


@app.cell
def _(catalog, dataset, mo, persona):
    _prefix = {"auth": "logs-auth-*", "web": "logs-web-*", "syslog": "logs-syslog-*",
               "edr": "edr-events-*"}[dataset.value]
    _fields = sorted(f for f, s in catalog.describe(_prefix, persona.value)["fields"].items()
                     if s["use"] in ("filter", "join", "id"))
    field = mo.ui.dropdown(options=_fields,
                           value="source.ip" if "source.ip" in _fields else _fields[0],
                           label="field")
    size = mo.ui.slider(3, 20, value=10, label="how many")
    mo.hstack([field, size], justify="start")
    return field, size


@app.cell
def _(dataset, end, field, filters, mo, siem, size, start, tz):
    _t = siem.call("top_values", dataset=dataset.value, field=field.value, start=start.value,
                   end=end.value, tz=tz.value, filters=filters, size=size.value)
    mo.stop("error" in _t, mo.callout(_t.get("error", ""), kind="warn"))
    mo.vstack([
        mo.md(f"**{_t['total']:,}** matching events, about **{_t['distinct_approx']:,}** "
              f"distinct values; **{_t['other_count']:,}** events have some other value. "
              f"This answer: {siem.calls[-1]['bytes']:,} bytes."),
        mo.ui.table(_t["values"], selection=None),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. What? Read the events, a page at a time

    Now narrow the filters (add the address you just found, or `"event.outcome": "success"`)
    and read. Every answer says how many events match in all; **next page** sends the cursor
    back with the same arguments. Change the filters while a cursor is live and see what the
    server says.
    """)
    return


@app.cell
def _(mo):
    get_cursor, set_cursor = mo.state(None)      # (the search it belongs to, the cursor)
    page_size = mo.ui.slider(5, 50, value=10, label="page size")
    page_size
    return get_cursor, page_size, set_cursor


@app.cell
def _(dataset, end, filters, get_cursor, json, mo, page_size, siem, start, tz):
    _args = {"dataset": dataset.value, "start": start.value, "end": end.value,
             "tz": tz.value, "filters": filters}
    search_key = json.dumps(_args, sort_keys=True)
    _saved = get_cursor()
    _cursor = _saved[1] if _saved and _saved[0] == search_key else None
    page = siem.call("search_events", **_args, limit=page_size.value,
                     **({"cursor": _cursor} if _cursor else {}))
    mo.stop("error" in page, mo.callout(page.get("error", ""), kind="warn"))
    mo.vstack([
        mo.md(f"Page **{page['page']}**: {page['returned']} of **{page['total']:,}** matching "
              f"events ({siem.calls[-1]['bytes']:,} bytes, of which the cursor is "
              f"{len(page.get('next_cursor', '')):,})."
              + (f"\n\n> {page['hint']}" if "hint" in page else "")),
        mo.ui.table(page["events"], selection=None, page_size=page_size.value),
    ])
    return page, search_key


@app.cell
def _(mo, page, search_key, set_cursor):
    mo.hstack([
        mo.ui.button(label="next page", disabled="next_cursor" not in page,
                     on_click=lambda _: set_cursor((search_key, page["next_cursor"]))),
        mo.ui.button(label="first page", on_click=lambda _: set_cursor(None)),
    ], justify="start")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. What would raw hits have cost?

    The same question asked five ways on the incident's day: a naive tool that returns raw
    documents (with and without a window), then the three Unit 4 tools. Tokens are bytes / 4.
    """)
    return


@app.cell
def _(mo):
    run_cost = mo.ui.run_button(label="Measure")
    run_cost
    return (run_cost,)


@app.cell
async def _(cost, mo, persona, run_cost):
    mo.stop(not run_cost.value, mo.md("_Press **Measure**._"))
    mo.ui.table(await cost.measure(persona.value), selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 7. Your path, the lesson's path, and the model's

    Below is every call you made in this notebook, as this persona. Next to it: the path the
    lesson takes (`investigate.by_hand`), and, with a model endpoint, the path `gpt-oss-120b`
    takes when asked to triage the incident by itself, several times, scored against facts
    computed live from the estate.
    """)
    return


@app.cell
def _(mo):
    refresh = mo.ui.run_button(label="Show my path")
    refresh
    return (refresh,)


@app.cell
def _(mo, refresh, siem):
    mo.stop(not refresh.value, mo.md("_Press **Show my path** after you've investigated._"))
    mo.vstack([mo.md(f"**{len(siem.calls)} calls, {sum(c['bytes'] for c in siem.calls):,} "
                     "bytes** of results so far:"),
               mo.ui.table(siem.calls, selection=None, page_size=8)])
    return


@app.cell
async def _(investigate, mo, persona):
    _steps = await investigate.by_hand(persona.value)
    mo.vstack([mo.md(f"**The lesson's path:** {len(_steps)} calls, "
                     f"{sum(s['bytes'] for s in _steps):,} bytes. Facts a triage must state: "
                     f"`{investigate.facts(persona.value)}`"),
               mo.ui.table(_steps, selection=None)])
    return


@app.cell
def _(llm, mo):
    repeats = mo.ui.slider(1, 5, value=3, label="runs")
    run_model = mo.ui.run_button(label="Let the model investigate", disabled=llm() is None)
    mo.vstack([mo.hstack([repeats, run_model], justify="start"),
               mo.md("_No model endpoint configured: set OPENAI_BASE_URL and OPENAI_API_KEY "
                     "to run it._") if llm() is None else mo.md("")])
    return repeats, run_model


@app.cell
async def _(investigate, mo, persona, repeats, run_model):
    mo.stop(not run_model.value, mo.md("_Press **Let the model investigate**._"))
    _runs = await investigate.by_model(persona.value, repeats.value)
    mo.vstack([
        mo.md("**Summary** (score = share of the facts its answer states; read the range):"),
        mo.ui.table([investigate.summarise(_runs)], selection=None),
        mo.md("**Every run** (the path, and what it missed):"),
        mo.ui.table([{k: v for k, v in r.items() if k != "answer"} for r in _runs],
                    selection=None),
        mo.accordion({f"run {r['repeat']}: the answer": mo.md(r["answer"]) for r in _runs}),
    ])
    return


if __name__ == "__main__":
    app.run()
