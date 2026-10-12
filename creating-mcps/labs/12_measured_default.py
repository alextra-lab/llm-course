import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 12 lab: the measured default",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 12 lab: the measured default

    The capstone. One gateway (`examples/12/soc_gateway.py`) puts the CMDB and the SIEM behind
    the policy, the envelope and the record, and adds one tool, `propose_containment`, that
    proposes a step for a person to approve and executes nothing. In this lab you:

    1. see what each group of the directory is offered,
    2. triage the planted incident end to end, the measured way, and read the proposals,
    3. give the same gateway to the model and compare its path with the scripted one,
    4. run the production-readiness review, and
    5. read the gateway's own dashboard for what you just did.

    It needs the estate (`python env/env.py check`). Part 3 needs the model; the rest doesn't.
    """)
    return


@app.cell
def _():
    import importlib.util
    import sys

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "12"), str(EXAMPLES / "11" / "tests"),
                    str(EXAMPLES / "11"), str(EXAMPLES / "10"), str(EXAMPLES / "09"),
                    str(EXAMPLES / "08"), str(EXAMPLES / "07"), str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    triage = _load("triage", EXAMPLES / "12" / "triage.py")
    readiness = _load("readiness", EXAMPLES / "12" / "readiness.py")
    metrics = _load("metrics", EXAMPLES / "11" / "metrics.py")
    from common_mcp import llm, load_env

    load_env()
    return llm, metrics, mo, readiness, triage


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. One gateway, three offers

    The same server, listed as three groups of the directory. An L3 responder gets every
    tool, including `propose_containment`; an L1 analyst gets the reading tools only; a
    contractor with no grant gets nothing at all, not even a refusal to read. Every tool is
    marked read-only: the gateway sits at "read, and propose" on Unit 5's dial.
    """)
    return


@app.cell
async def _(mo, readiness):
    offers = await readiness.listings()
    _names = sorted({t["name"] for v in offers.values() for t in v["tools"]})
    mo.ui.table([{"tool": n, **{g: "✓" if any(t["name"] == n for t in v["tools"]) else ""
                                for g, v in offers.items()}} for n in _names],
                selection=None)
    return (offers,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Triage INC-0042, the measured way

    Marc, the L3 responder, works the incident over HTTP with the whole stack on: verified
    tokens, the policy, the limits, telemetry and the audit log. Counts come before reads;
    the two person lookups ask him for a ticket (he gives the incident's); the last two steps
    propose containment. Every call carries the same session and trace.
    """)
    return


@app.cell
def _(mo):
    run_triage = mo.ui.run_button(label="Run the triage")
    run_triage
    return (run_triage,)


@app.cell
async def _(mo, run_triage, triage):
    mo.stop(not run_triage.value, mo.md("_Press **Run the triage**._"))
    with mo.status.spinner(title="Starting the SOC gateway and triaging INC-0042..."):
        async with triage.serving() as _srv:
            scripted = await triage.scripted(_srv["url"])
        _checks = triage.check(scripted)
    mo.vstack([
        mo.ui.table([{k: s[k] for k in ("step", "tool", "ms", "bytes", "asked", "found")}
                     for s in scripted["steps"]], selection=None),
        mo.md("\n".join(f"- {'✅' if ok else '❌'} **{what}**: {detail}"
                        for what, ok, detail in _checks)),
    ])
    return (scripted,)


@app.cell
def _(mo, scripted):
    mo.hstack([mo.callout(mo.md(
        f"**{p['proposal_id']}**: {p['action'].replace('_', ' ')} `{p['target']}`  \n"
        f"_{p['status']}_  \n"
        f"**Approver:** {p['approver']}  \n"
        f"**Affects:** {'; '.join(p['blast_radius']) or '-'}  \n"
        f"**Requires:** {'; '.join(p['requires'])}  \n"
        f"**Undo:** {p['rollback']}"), kind="warn") for p in scripted["proposals"]],
        widths="equal", gap=1)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. The model's triage

    The same gateway, over stdio as Marc, given to the model with the `triage_alert` prompt.
    Compare its path with the scripted one: did it count before it read, did it find the
    facts, did it end with a proposal, and what did that cost in calls and bytes?
    """)
    return


@app.cell
def _(llm, mo):
    repeats = mo.ui.slider(1, 5, value=2, label="Runs")
    run_model = mo.ui.run_button(label="Run the model", disabled=llm() is None)
    mo.hstack([repeats, run_model] + ([mo.md(
        "_No model: set `OPENAI_BASE_URL` and `OPENAI_API_KEY`._")] if llm() is None else []),
        justify="start")
    return repeats, run_model


@app.cell
async def _(mo, repeats, run_model, triage):
    mo.stop(not run_model.value, mo.md("_Press **Run the model**._"))
    with mo.status.spinner(title="The model is triaging INC-0042..."):
        _runs = await triage.by_model(repeats.value)
    mo.vstack([
        mo.ui.table([{k: v for k, v in r.items() if k != "answer"} for r in _runs],
                    selection=None),
        mo.md("**Its last answer**"),
        mo.md(_runs[-1]["answer"] if _runs else "-"),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. The production-readiness review

    Every unit left a check behind. The review runs them against this gateway and prints one
    scorecard: **pass**, **warn** (it works, with a caveat someone must accept), **fail**, or
    **todo** (the evidence hasn't been collected; the OWASP negative cases are placeholders,
    and the review says so instead of hiding them). It starts its own gateway and takes a
    minute or two.
    """)
    return


@app.cell
def _(mo):
    run_review = mo.ui.run_button(label="Run the review")
    run_review
    return (run_review,)


@app.cell
async def _(mo, readiness, run_review):
    mo.stop(not run_review.value, mo.md("_Press **Run the review**._"))
    with mo.status.spinner(title="Running every unit's checks against the SOC gateway..."):
        review = await readiness.review()
    _icon = {"pass": "✅", "warn": "⚠️", "fail": "❌", "todo": "📝"}
    mo.vstack([
        mo.md(f"### Verdict: {review['verdict']}"),
        mo.ui.table([{"": _icon[x["status"]], **x} for x in review["lines"]],
                    selection=None),
        mo.accordion({"The review as Markdown": mo.md(readiness.markdown(review))}),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. The dashboard, for what you just did

    Unit 11's panels over the last 15 minutes of telemetry: the triage, the review's probes
    (the identity refusals and the burst it fired to test the rate limit), and the model's
    runs, if you made them.
    """)
    return


@app.cell
def _(mo):
    refresh = mo.ui.button(label="Refresh")
    refresh
    return (refresh,)


@app.cell
def _(metrics, mo, refresh):
    refresh
    _slo = metrics.slo("now-15m", 0.99, 500)
    mo.vstack([
        mo.hstack([
            mo.stat(_slo["allowed_calls"], label="Allowed calls"),
            mo.stat(f"{_slo['achieved']:.2%}" if _slo["achieved"] is not None else "-",
                    label=_slo["objective"]),
        ], justify="start"),
        mo.md("**By tool**"), mo.ui.table(metrics.by_tool("now-15m"), selection=None),
        mo.md("**By caller**"), mo.ui.table(metrics.by_caller("now-15m"), selection=None),
        mo.md("**Why calls didn't run**"),
        mo.ui.table(metrics.refusals("now-15m"), selection=None),
    ])
    return


if __name__ == "__main__":
    app.run()
