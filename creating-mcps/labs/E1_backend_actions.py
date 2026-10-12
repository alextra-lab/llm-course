import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="E1 lab: backend APIs with approvals",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # E1 lab: backend APIs with approvals

    Unit 12's gateway proposed containment and did nothing. This one carries the step out,
    through two backend APIs: an EDR that isolates hosts and a ticketing system that records
    changes (`examples/E1/backends.py` stands in for both, with faults you can switch on). In
    this lab you:

    1. break a backend on purpose and watch retries, idempotency keys and a circuit breaker
       keep one request from becoming two side effects, or a slow failure from becoming many,
    2. see when a production host may be changed,
    3. run the change drill: approvals, a declined approval, a task, a client without the
       Tasks extension, lost replies and an EDR outage, each checked against the ledger of
       what really happened, and
    4. read a task on the wire.

    It needs the estate (`python env/env.py check`). No model is needed.
    """)
    return


@app.cell
def _():
    import importlib.util
    import sys
    from datetime import datetime, timedelta

    import httpx
    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "E1"), str(EXAMPLES / "07"), str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    drill = _load("e1_drill", EXAMPLES / "E1" / "drill.py")
    resilience = _load("resilience", EXAMPLES / "E1" / "resilience.py")
    actions = _load("e1_actions", EXAMPLES / "E1" / "actions_gateway.py")
    from common_mcp import load_env

    load_env()
    return actions, datetime, drill, httpx, mo, resilience, timedelta


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. A backend that fails

    Ten isolations of ten different hosts, through `resilience.Backend`, against an EDR you
    break with the sliders. **Refused** answers 503 before doing anything; **lost reply**
    does the work and then answers 503, so the client can't tell it was done. Every POST
    carries an `Idempotency-Key`, so a retry of a request that was done is a *replay*: the
    EDR answers again and does nothing. Compare *requests* with *isolations in the ledger*:
    without the key, every lost reply would have isolated a host twice.

    Push the failure rate up and the circuit breaker opens: after three failures in a row it
    refuses at once, sending nothing, until the cooldown has passed. A failing backend then
    costs callers milliseconds, not timeouts, and gets room to recover.
    """)
    return


@app.cell
def _(mo):
    fail_rate = mo.ui.slider(0, 1, step=0.1, value=0.2, label="Refused")
    lose_reply = mo.ui.slider(0, 1, step=0.1, value=0.3, label="Lost reply")
    run_faults = mo.ui.run_button(label="Isolate ten hosts")
    mo.hstack([fail_rate, lose_reply, run_faults], justify="start")
    return fail_rate, lose_reply, run_faults


@app.cell
async def _(drill, fail_rate, httpx, lose_reply, mo, resilience, run_faults):
    mo.stop(not run_faults.value, mo.md("_Press **Isolate ten hosts**._"))
    import asyncio as _asyncio

    def _ten(url):
        edr = resilience.Backend("EDR", url, base_delay=0.05, max_delay=0.4,
                                 breaker=resilience.CircuitBreaker(threshold=3, cooldown=1.0))
        outcomes = []
        for i in range(10):
            host = f"lab-{i:02d}"
            try:
                edr.request("POST", f"/edr/hosts/{host}/isolate", json={"ticket": "INC-0042"},
                            key=resilience.idempotency_key("INC-0042", "isolate", host))
                outcomes.append({"host": host, "outcome": "isolated"})
            except resilience.BackendError as e:
                outcomes.append({"host": host, "outcome": str(e)})
        return edr, outcomes

    with mo.status.spinner(title="Starting the backends and isolating..."):
        async with drill.serving(gateway=False) as _srv:
            async with httpx.AsyncClient(base_url=_srv["backends"]) as _http:
                await _http.post("/_faults", json={"fail_rate": fail_rate.value,
                                                   "lose_reply": lose_reply.value})
                _edr, _outcomes = await _asyncio.to_thread(_ten, _srv["backends"])
                _ledger = (await _http.get("/_ledger")).json()
    _isolations = [e for e in _ledger if e["effect"] == "isolate"]
    _per_host = {}
    for _e in _isolations:
        _per_host[_e["host"]] = _per_host.get(_e["host"], 0) + 1
    mo.vstack([
        mo.hstack([
            mo.stat(len(_edr.calls), label="Requests sent"),
            mo.stat(sum(c["replay"] for c in _edr.calls), label="Replays"),
            mo.stat(len(_isolations), label="Isolations in the ledger"),
            mo.stat(max(_per_host.values(), default=0), label="Most isolations of one host"),
            mo.stat(sum(o["outcome"] == "isolated" for o in _outcomes), label="Succeeded"),
        ], justify="start"),
        mo.md("**Each host**"), mo.ui.table(_outcomes, selection=None),
        mo.md("**Every attempt**"), mo.ui.table(_edr.calls, selection=None),
        mo.md("**The breaker**: " + (", ".join(f"{a} → {b}" for _, a, b in
                                              _edr.breaker.transitions) or "stayed closed")),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. When a production host may change

    The gateway refuses a production change outside the change window
    (`MCP_CHANGE_WINDOW`, 22:00 to 05:00 UTC here), *before* anyone is asked to approve it.
    An intrusion can't wait for the evening, so a high-severity incident allows an
    emergency change: the caller says so (`emergency=true`), and the change record says so
    too. Staging and development hosts change any time.
    """)
    return


@app.cell
def _(actions, datetime, mo, timedelta):
    _day = datetime.fromisoformat("2026-10-12T00:00:00+00:00")
    _rows = [{"UTC": f"{(_day + timedelta(hours=h)):%H:%M}",
              "in the window": "✓" if actions.in_window(_day + timedelta(hours=h)) else "",
              "prod, no emergency": "allowed" if actions.in_window(_day + timedelta(hours=h))
              else "refused, nobody asked",
              "prod, emergency (high severity)": "asked for approval"}
             for h in range(0, 24, 2)]
    mo.ui.table(_rows, selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. The change drill

    The backends and the actions gateway, with the clock pinned at 14:00 UTC (outside the
    window) and an EDR that takes two seconds. Priya (L1) and Marc (L3) go through ten
    steps, from "not in my list" to an EDR outage, and every outcome is checked against the
    backends' ledger: what the gateway *says* happened is compared with what *did*. It
    takes about half a minute.
    """)
    return


@app.cell
def _(mo):
    run_drill = mo.ui.run_button(label="Run the drill")
    run_drill
    return (run_drill,)


@app.cell
async def _(drill, mo, run_drill):
    mo.stop(not run_drill.value, mo.md("_Press **Run the drill**._"))
    with mo.status.spinner(title="Starting the backends and the gateway, running the drill..."):
        run = await drill.run_all()
    _checks = drill.check(run)

    def _row(s):
        if "down" in s:
            return {"step": s["step"], "name": s["name"],
                    "says": " | ".join(c["says"][:70] for c in s["down"])
                    + " | then: " + s["recovered"]["says"][:60], "ledger": len(s["ledger"])}
        return {"step": s["step"], "name": s["name"], "asked": s.get("asked", ""),
                "task": " > ".join(drill._squash(s.get("task", []))),
                "ms": s.get("ms", ""),
                "says": s.get("result", {}).get("status") or s.get("says", ", ".join(
                    s.get("tools", [])))[:110],
                "ledger": len(s["ledger"])}

    mo.vstack([
        mo.ui.table([_row(s) for s in run["steps"]], selection=None),
        mo.md("\n".join(f"- {'✅' if ok else '❌'} **{what}**: {detail}"
                        for what, ok, detail in _checks)),
    ])
    return (run,)


@app.cell
def _(mo, run):
    mo.ui.table([{"change": t["ticket_id"], "title": t["title"], "by": t["opened_by"],
                  "outcome": "; ".join(c["text"] for c in t["comments"])}
                 for t in run["tickets"].values()], selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. A task on the wire

    Step 5's isolation took longer than the gateway waits (`MCP_TASK_AFTER`, half a second
    here), and Marc's client had declared the Tasks extension, so the `tools/call` answered at
    once with `resultType: "task"` and a task id; the client then polled `tasks/get` until the
    task was `completed` and handed back the tool's result as if it had been synchronous.
    The approval came first, on the original call, as the spec advises: a task never stops
    halfway to ask. Step 7's client hadn't declared the extension, and simply waited.
    """)
    return


@app.cell
def _(mo, run):
    _step = next(s for s in run["steps"] if s["name"] == "emergency, approved")
    mo.vstack([
        mo.md(f"**What the client saw while it waited**: {' > '.join(_step['task'])}"),
        mo.md("**The result it handed back**"),
        mo.json(_step["result"]),
    ])
    return


if __name__ == "__main__":
    app.run()
