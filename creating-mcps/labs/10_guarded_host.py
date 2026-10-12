import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 10 lab: the guarded host",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 10 lab: the guarded host

    The OWASP MCP Top 10, mapped onto the gateway you've built, and a host whose defences
    switch on one at a time (`examples/10/guarded_host.py`). You'll see the tool table the
    model reads under each set of defences, the audit records the host keeps, and what the
    description screen makes of every tool in the estate.

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
    sys.path[:0] = [str(EXAMPLES / "10"), str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    guarded_host = _load("guarded_host", EXAMPLES / "10" / "guarded_host.py")
    cases = _load("cases", EXAMPLES / "10" / "cases.py")
    tour = _load("tour", EXAMPLES / "10" / "tour.py")
    from common_mcp import load_env

    load_env()
    return cases, guarded_host, mo, tour


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. The OWASP map

    Each category, the defence expected to answer it, and the state of its case. Cases still
    marked `todo` are placeholders the course team is writing.
    """)
    return


@app.cell
async def _(cases, mo):
    _rows = await cases.run(set())
    mo.ui.table([{"id": r["owasp"], "category": r["category"], "defence": r["defence"],
                  "case": r["result"]} for r in _rows], selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. The guarded host

    Switch defences on and run the tour: four everyday calls (an asset, an incident, an IP
    reputation lookup, a status change the person asked for). Compare the tool table and the
    audit records with everything off.
    """)
    return


@app.cell
def _(guarded_host, mo):
    choices = [d for d in guarded_host.DEFENCES if d != "identity"]
    defences = mo.ui.multiselect(options=choices, value=choices, label="Defences")
    go = mo.ui.run_button(label="Run the tour")
    mo.hstack([defences, go], justify="start")
    return defences, go


@app.cell
async def _(defences, go, mo, tour):
    mo.stop(not go.value, mo.md("_Press **Run the tour**._"))
    with mo.status.spinner(title="Starting the servers..."):
        result = await tour.tour(set(defences.value))
    mo.vstack([
        mo.md("**Tools the model sees:** " + ", ".join(f"`{t}`" for t in result["tools"])),
        mo.ui.table(result["calls"], selection=None),
        mo.md(f"**Audit: {len(result['audit'])} records**"),
        mo.ui.table(result["audit"], selection=None),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. The description screen

    `screen()` looks for phrases that tell a model what to do, which no honest description
    needs. Run it over every tool in the estate, honest and third-party, and read what it
    flags. Edit the text below to try your own descriptions.
    """)
    return


@app.cell
def _(mo):
    text = mo.ui.text_area(
        value="Reputation of an IP address. Use it on source or destination IPs found in logs.",
        label="A tool description", full_width=True)
    text
    return (text,)


@app.cell
def _(guarded_host, mo, text):
    _found = guarded_host.screen(text.value)
    mo.md("**Flagged:** " + ", ".join(f"`{f}`" for f in _found) if _found
          else "**Nothing flagged.**")
    return


@app.cell
async def _(guarded_host, mo, tour):
    from tempfile import TemporaryDirectory as _Tmp
    from pathlib import Path as _Path

    _rows = []
    with _Tmp() as _tmp:
        async with guarded_host.GuardedHost(tour.servers("l3_responder", set(), _Path(_tmp))) as _h:
            for _t in _h.describe():
                _rows.append({"server": _t["server"], "tool": _t["name"],
                              "flagged": ", ".join(guarded_host.screen(_t["description"]))})
    mo.ui.table(_rows, selection=None)
    return


if __name__ == "__main__":
    app.run()
