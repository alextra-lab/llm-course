import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 6 lab: write tools for a model reader",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 6 lab: write tools for a model reader

    Nine tools, the same code, four descriptions (`examples/06/styles.py`):

    | style | what the model reads |
    |---|---|
    | **terse** | a few words per tool, nothing per parameter: what many servers ship |
    | **original** | the docstrings Units 3-5 wrote |
    | **rewritten** | this unit's rules: what it answers, when to use it, the limits, an example; every parameter described; bounds and choices in the schema; annotations |
    | **lean** | rewritten, with shared conventions said once |

    You'll see what each costs, lint them, grade their error messages, rewrite one yourself,
    and (with a model endpoint) measure tool selection and recovery from mistakes. It needs
    the estate (`python env/env.py up` and `seed`).
    """)
    return


@app.cell
def _():
    import importlib.util
    import json
    import sys

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "06"), str(EXAMPLES / "05"), str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    styles = _load("styles", EXAMPLES / "06" / "styles.py")
    toolcost = _load("unit6_toolcost", EXAMPLES / "06" / "toolcost.py")
    lint = _load("unit6_lint", EXAMPLES / "06" / "lint.py")
    errors = _load("unit6_errors", EXAMPLES / "06" / "errors.py")
    selection = _load("unit6_selection", EXAMPLES / "06" / "selection.py")
    recovery = _load("unit6_recovery", EXAMPLES / "06" / "recovery.py")
    from common_mcp import llm, load_env

    load_env()
    unit2 = toolcost.unit2
    return (errors, json, lint, llm, mo, recovery, selection, styles, toolcost, unit2)


@app.cell
def _(mo, styles):
    persona = mo.ui.dropdown(options=["l1_analyst", "l3_responder"], value="l1_analyst",
                             label="act as")
    style = mo.ui.dropdown(options=list(styles.STYLES), value="original", label="style")
    mo.hstack([persona, style], justify="start")
    return persona, style


@app.cell
async def _(lint, persona, styles, unit2):
    async def _tables(p):
        out = {}
        for s in styles.STYLES:
            async with unit2.Host(styles.server_table(s, p), lint._approve) as host:
                out[s] = dict(host.tools)
        return out

    tools_by_style = await _tables(persona.value)
    return (tools_by_style,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. What the tool list costs

    Every model call carries the whole list. `titles` are the per-parameter titles the SDK
    generates from the names ("Dataset" for `dataset`): repetition a host could strip.
    Output schemas are in `tools/list`, but this host can't send them to the model.
    """)
    return


@app.cell
async def _(mo, persona, style, styles, toolcost):
    _rows = {s: await toolcost.measure(s, persona.value) for s in styles.STYLES}
    mo.vstack([
        mo.ui.table([{"style": s, **toolcost.total(r)} for s, r in _rows.items()],
                    selection=None),
        mo.md(f"**{style.value}**, tool by tool (bytes):"),
        mo.ui.table(_rows[style.value], selection=None, page_size=10),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Read what the model reads

    Pick a tool and compare the four styles side by side. Look at what each one lets the
    model *know* before its first call: a limit, a format, which tool comes first, which
    datasets this persona may use.
    """)
    return


@app.cell
def _(mo, styles):
    tool_name = mo.ui.dropdown(options=list(styles.TOOLS), value="search_events", label="tool")
    tool_name
    return (tool_name,)


@app.cell
def _(json, mo, styles, tool_name, tools_by_style):
    def _show(s):
        key = f"{styles.TOOLS[tool_name.value]['server']}__{tool_name.value}"
        t = tools_by_style[s][key][1]
        return mo.md(f"**{s}**\n\n{t.description}\n\n```json\n"
                     f"{json.dumps(t.input_schema, indent=1)}\n```")

    mo.ui.tabs({s: _show(s) for s in styles.STYLES})
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Lint them

    The linter's rules are heuristics: each finding is a question, not a verdict. Switch the
    style above and watch the findings change.
    """)
    return


@app.cell
def _(lint, mo, style, tools_by_style):
    _tools = tools_by_style[style.value]
    _names = {t.name for _s, t in _tools.values()}
    _findings = [f for _s, t in _tools.values() for f in lint.lint_tool(t, _names - {t.name})]
    mo.vstack([mo.md(f"**{style.value}**: `{lint.summary(_findings)}`"),
               mo.ui.table(_findings, selection=None, page_size=10)])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Grade the error messages

    Seventeen mistakes a model really makes, sent to the tools. A good refusal names the
    **p**roblem, says what's **a**llowed, and says what to do **n**ext; it never points at a
    tool the caller doesn't have, or leaks internals. Try the generic errors: every refusal
    says only "invalid request".
    """)
    return


@app.cell
def _(mo):
    error_style = mo.ui.radio(options=["instructive", "generic"], value="instructive",
                              label="error messages", inline=True)
    error_style
    return (error_style,)


@app.cell
async def _(error_style, errors, mo, persona, style):
    _rows = await errors.audit(style.value, persona.value, error_style.value)
    _graded = [r for r in _rows if r["score"] is not None]
    mo.vstack([
        mo.md(f"Mean score **{sum(r['score'] for r in _graded) / len(_graded):.2f}** of 3 "
              f"({style.value}, {error_style.value})."),
        mo.ui.table(_rows, selection=None, page_size=9),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. Rewrite one yourself

    Edit the description of the tool you picked in section 2 (it starts as the original).
    The cost and the linter update as you type. No model is needed; section 6 measures
    whether it helps.
    """)
    return


@app.cell
def _(mo, styles, tool_name, tools_by_style):
    _key = f"{styles.TOOLS[tool_name.value]['server']}__{tool_name.value}"
    mine = mo.ui.text_area(value=tools_by_style["original"][_key][1].description,
                           label=f"your description of {tool_name.value}", rows=6,
                           full_width=True)
    mine
    return (mine,)


@app.cell
def _(json, lint, mine, mo, styles, tool_name, tools_by_style):
    _key = f"{styles.TOOLS[tool_name.value]['server']}__{tool_name.value}"
    _tools = tools_by_style["original"]
    _orig = _tools[_key][1]
    _yours = _orig.model_copy(update={"description": mine.value})
    _names = {t.name for _s, t in _tools.values()} - {_orig.name}

    def _cost(t):
        return len(json.dumps({"name": t.name, "description": t.description,
                               "parameters": t.input_schema}).encode())

    mo.vstack([
        mo.md(f"Original **{_cost(_orig):,}** bytes (~{_cost(_orig) // 4} tokens); yours "
              f"**{_cost(_yours):,}** bytes (~{_cost(_yours) // 4} tokens), on every call."),
        mo.ui.table(lint.lint_tool(_yours, _names) or [{"rule": "-", "message": "no findings"}],
                    selection=None),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. Does the model pick the right tool?

    Twenty requests, each labelled with the tool that should answer it first (or none). One
    model call per request, per style, repeated; the first call is graded, then run. Read
    the ranges: with a few runs, overlapping ranges are not a difference.
    """)
    return


@app.cell
def _(mo, persona, selection):
    mo.ui.table([{"ask": i["ask"], "expect": ", ".join(i["expect"]) or "(no call)",
                  "args": i["args"]} for i in selection.items_for(persona.value)],
                selection=None, page_size=10)
    return


@app.cell
def _(llm, mo, styles):
    sel_styles = mo.ui.multiselect(options=list(styles.STYLES), value=["terse", "lean"],
                                   label="styles")
    sel_repeats = mo.ui.slider(1, 5, value=2, label="runs")
    run_sel = mo.ui.run_button(label="Run the selection eval", disabled=llm() is None)
    mo.vstack([mo.hstack([sel_styles, sel_repeats, run_sel], justify="start"),
               mo.md("_No model endpoint configured: set OPENAI_BASE_URL and OPENAI_API_KEY "
                     "to run it._") if llm() is None else mo.md("")])
    return run_sel, sel_repeats, sel_styles


@app.cell
async def _(mo, persona, run_sel, sel_repeats, sel_styles, selection):
    mo.stop(not run_sel.value, mo.md("_Press **Run the selection eval**._"))
    _results = await selection.run(persona.value, sel_styles.value, sel_repeats.value)
    mo.vstack([mo.ui.table(selection.summarise(_results), selection=None),
               mo.ui.table(_results, selection=None, page_size=10)])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 7. What is an error message worth?

    Six tasks phrased the way people ask, so the first call usually fails. Each runs as a
    whole conversation and is scored on its final answer. Compare the shipped messages with
    "invalid request", and with this unit's descriptions.
    """)
    return


@app.cell
def _(llm, mo, recovery):
    rec_conditions = mo.ui.multiselect(options=list(recovery.CONDITIONS),
                                       value=list(recovery.CONDITIONS), label="conditions")
    rec_repeats = mo.ui.slider(1, 5, value=2, label="runs")
    run_rec = mo.ui.run_button(label="Run the recovery eval", disabled=llm() is None)
    mo.hstack([rec_conditions, rec_repeats, run_rec], justify="start")
    return rec_conditions, rec_repeats, run_rec


@app.cell
async def _(mo, persona, rec_conditions, rec_repeats, recovery, run_rec):
    mo.stop(not run_rec.value, mo.md("_Press **Run the recovery eval**._"))
    _results = await recovery.run(persona.value, rec_conditions.value, rec_repeats.value)
    mo.vstack([mo.ui.table(recovery.summarise(_results), selection=None),
               mo.ui.table(_results, selection=None, page_size=10)])
    return


if __name__ == "__main__":
    app.run()
