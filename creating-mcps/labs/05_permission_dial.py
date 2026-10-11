import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 5 lab: turn the permission dial",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 5 lab: turn the permission dial

    The same persona, the same estate, four ways to hand its power to a model:

    | variant | CMDB (Postgres) | SIEM (OpenSearch) |
    |---|---|---|
    | **permissive** | `run_sql`: any SQL | OpenSearch's own MCP server: raw Query DSL, any API |
    | **guarded** | `query_sql`: one parse-checked SELECT | `ppl_query`: checked, bounded PPL |
    | **restrictive** | task tools (`get_asset`, `service_dependencies`, ...) | Unit 4's tools |
    | **hybrid** | restrictive, plus `escalate_query` behind the person's approval | |

    You'll try the two guards by hand, fire the same risky probes at every variant, see what
    each variant's tool list costs, open the hatch yourself, and (with a model endpoint) run
    the same tasks through all four. It needs the estate (`python env/env.py up` and `seed`).
    The permissive variant also needs `opensearch-mcp-server-py` in its own environment (see
    `examples/05/variants.py`); without it, that variant is skipped.
    """)
    return


@app.cell
def _():
    import importlib.util
    import sys

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "05"), str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    sql_guard = _load("sql_guard", EXAMPLES / "05" / "sql_guard.py")
    ppl_guard = _load("ppl_guard", EXAMPLES / "05" / "ppl_guard.py")
    variants = _load("variants", EXAMPLES / "05" / "variants.py")
    blast = _load("unit5_blast", EXAMPLES / "05" / "blast.py")
    grid = _load("unit5_grid", EXAMPLES / "05" / "grid.py")
    from common_mcp import llm, load_env, os_client, pg_connect

    load_env()
    return (blast, grid, llm, mo, os_client, pg_connect, ppl_guard, sql_guard,
            variants)


@app.cell
def _(mo, variants):
    persona = mo.ui.dropdown(options=["l1_analyst", "l3_responder"], value="l1_analyst",
                             label="act as")
    _missing = [] if "permissive" in variants.available() else [mo.callout(mo.md(
        "`opensearch-mcp-server-py` isn't installed, so the **permissive** variant is "
        "skipped. To include it: `python3.13 -m venv .venv-osmcp && .venv-osmcp/bin/pip "
        f"install opensearch-mcp-server-py=={variants.OSMCP_VERSION}`"), kind="warn")]
    mo.vstack([persona, *_missing])
    return (persona,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. The SQL guard: check the tree, not the text

    `sql_guard.check` parses the query with sqlglot and walks the tree: one statement, a
    read, tables in `cmdb` only and schema-qualified, functions it recognises. What passes is
    wrapped with a row cap and run read-only, with a 2 s timeout, **as the persona**. Try:

    - `SELECT hostname FROM assets` (unqualified)
    - `SET statement_timeout = 0; SELECT pg_sleep(6)` (two statements)
    - `WITH x AS (DELETE FROM cmdb.assets RETURNING *) SELECT * FROM x` (a write in a CTE)
    - `SELECT pg_read_file('/etc/passwd')`
    - `SELECT email FROM cmdb.people` (passes the guard; then the grants decide)
    """)
    return


@app.cell
def _(mo):
    sql_text = mo.ui.text_area(
        value="SELECT s.tier, count(*) FROM cmdb.assets a JOIN cmdb.services s "
              "ON s.id = a.service_id WHERE a.environment = 'prod' GROUP BY s.tier",
        label="SQL", rows=3, full_width=True)
    sql_text
    return (sql_text,)


@app.cell
def _(mo, persona, pg_connect, sql_guard, sql_text):
    try:
        _tree = sql_guard.check(sql_text.value)
    except sql_guard.SqlRefused as _e:
        _out = mo.callout(mo.md(f"**Refused by the guard:** {_e}"), kind="warn")
    else:
        try:
            with pg_connect(persona.value) as _conn:
                _r = sql_guard.run(_conn, sql_text.value)
            _out = mo.vstack([
                mo.md(f"**Ran** as `{persona.value}`: {_r['returned']} rows"
                      + (" (truncated)" if _r["truncated"] else "")),
                mo.ui.table([dict(zip(_r["columns"], row)) for row in _r["rows"]],
                            selection=None, page_size=8)])
        except Exception as _e:  # noqa: BLE001 -- the backend's own refusal
            _out = mo.callout(mo.md(f"**Passed the guard, stopped by Postgres:** "
                                    f"`{type(_e).__name__}: {str(_e).splitlines()[0]}`"),
                              kind="danger")
        _out = mo.vstack([mo.md(f"What actually runs:\n\n```sql\n{sql_guard.capped(_tree)}\n```"),
                          _out])
    _out
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. The PPL guard: bound it by construction, then read the plan

    The window is an argument, not part of the query. The guard rewrites `source` to the
    daily indexes the window touches, adds the exact time filter and `head 200`, then asks
    OpenSearch for the compiled plan (`_explain`) and refuses leading wildcards, regexps and
    plans that fetch too many documents. Try `where like(message, '%svc_backup%')`,
    `source = edr-events-*` as L1, a window over 7 days, or no window at all.
    """)
    return


@app.cell
def _(mo):
    ppl_text = mo.ui.text_area(
        value="source = logs-auth-* | where `event.outcome` = 'failure' "
              "| stats count() as failures by `source.ip` | sort - failures | head 5",
        label="PPL", rows=3, full_width=True)
    ppl_start = mo.ui.text(value="now-3d", label="start")
    ppl_end = mo.ui.text(value="now-2d", label="end")
    mo.vstack([ppl_text, mo.hstack([ppl_start, ppl_end], justify="start")])
    return ppl_end, ppl_start, ppl_text


@app.cell
def _(mo, os_client, persona, ppl_end, ppl_guard, ppl_start, ppl_text):
    try:
        _r = ppl_guard.run(os_client(persona.value), ppl_text.value, persona.value,
                           ppl_start.value or None, ppl_end.value or None)
    except ppl_guard.PplRefused as _e:
        _kind = "danger" if str(_e).startswith("the query failed") else "warn"
        _out = mo.callout(mo.md(f"**{'Failed' if _kind == 'danger' else 'Refused'}:** {_e}"),
                          kind=_kind)
    else:
        _out = mo.vstack([
            mo.md(f"What actually ran:\n\n```\n{_r['ran']}\n```\n\n**{_r['returned']} rows**"),
            mo.ui.table([dict(zip(_r["columns"], row)) for row in _r["rows"]],
                        selection=None, page_size=8)])
    _out
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Blast radius: the same probes, every variant

    Each probe is something a confused or manipulated model might send. Every variant gets
    the closest call its tools can express, as the same persona. The hybrid's hatch is
    approved automatically here, so its column is the worst case: what a person could
    approve by mistake. Read each column for **ran**: that is what the variant lets through.
    """)
    return


@app.cell
def _(mo):
    run_blast = mo.ui.run_button(label="Fire the probes")
    run_blast
    return (run_blast,)


@app.cell
async def _(blast, mo, persona, run_blast):
    mo.stop(not run_blast.value, mo.md("_Press **Fire the probes** (about 30 s)._"))
    _rows = await blast.run(persona.value)
    mo.ui.table(_rows, selection=None, page_size=len(_rows))
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. What each tool list costs

    The model reads every tool's name, description and schema on every call. Restrictive
    is the safest variant and the most expensive to describe: many small tools cost more
    than two general ones. That's the price of the menu, paid on every turn.
    """)
    return


@app.cell
async def _(grid, mo, persona, variants):
    mo.ui.table([await grid.tool_cost(v, persona.value) for v in variants.available()],
                selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. Open the hatch yourself

    In the hybrid, `escalate_query` asks *you* before anything runs. Below, you are the
    person. The question the server would show you is printed; tick **approve** and run it.
    Approval opens the *guarded* tier, not raw access: try approving an UPDATE.
    """)
    return


@app.cell
def _(mo):
    hatch_sql = mo.ui.text_area(
        value="SELECT t.name, count(*) FROM cmdb.assets a JOIN cmdb.services s "
              "ON s.id = a.service_id JOIN cmdb.teams t ON t.id = s.owner_team_id "
              "WHERE a.environment = 'prod' GROUP BY t.name ORDER BY 2 DESC LIMIT 3",
        label="the model's query", rows=3, full_width=True)
    hatch_reason = mo.ui.text(value="no task tool groups hosts by owning team",
                              label="the model's reason", full_width=True)
    approve = mo.ui.checkbox(label="approve")
    run_hatch = mo.ui.run_button(label="Send escalate_query")
    mo.vstack([hatch_sql, hatch_reason, mo.hstack([approve, run_hatch], justify="start")])
    return approve, hatch_reason, hatch_sql, run_hatch


@app.cell
async def _(approve, blast, hatch_reason, hatch_sql, mo, persona, run_hatch, variants):
    mo.stop(not run_hatch.value, mo.md("_Press **Send escalate_query**._"))
    _asked = []

    async def _person(_server, params):
        _asked.append(params.message)
        return blast.unit2.ElicitResult(action="accept" if approve.value else "decline",
                                        content={})

    async with blast.unit2.Host({"hatch": variants.HERE / "hatch_server.py"}, _person,
                                env={"MCP_PERSONA": persona.value}) as _host:
        _text, _ev = await _host.call("hatch__escalate_query",
                                      {"language": "sql", "query": hatch_sql.value,
                                       "reason": hatch_reason.value}, "lab", "0" * 32, 1)
    mo.vstack([
        mo.md("**What you were asked:**"),
        mo.md("```\n" + (_asked[0] if _asked else "(nothing)") + "\n```"),
        mo.md(f"**You {'approved' if approve.value else 'declined'}. Outcome:** "
              f"`{_ev.outcome}`"),
        mo.md(f"```\n{_text[:1500]}\n```"),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. The grid: the same tasks through every variant

    Ten tasks: eight a task tool answers directly, two "off the menu". Each is asked in a
    fresh conversation, through each variant, several times, and scored against gold
    answers computed live as this persona. Read success by **on** and **off** the menu,
    and the range, not just the mean. Needs the model endpoint.
    """)
    return


@app.cell
def _(grid, mo, persona):
    mo.ui.table([{"task": t["id"], "menu": t["menu"], "ask": t["ask"],
                  "gold": ", ".join(sorted(t["gold"])) if isinstance(t["gold"], set)
                  else t["gold"]} for t in grid.tasks(persona.value)], selection=None)
    return


@app.cell
def _(llm, mo, variants):
    grid_variants = mo.ui.multiselect(options=variants.available(),
                                      value=variants.available(), label="variants")
    grid_repeats = mo.ui.slider(1, 5, value=2, label="runs")
    run_grid = mo.ui.run_button(label="Run the grid", disabled=llm() is None)
    mo.vstack([mo.hstack([grid_variants, grid_repeats, run_grid], justify="start"),
               mo.md("_No model endpoint configured: set OPENAI_BASE_URL and OPENAI_API_KEY "
                     "to run it._") if llm() is None else mo.md("")])
    return grid_repeats, grid_variants, run_grid


@app.cell
async def _(grid, grid_repeats, grid_variants, mo, persona, run_grid):
    mo.stop(not run_grid.value, mo.md("_Press **Run the grid**._"))
    _results = await grid.run(persona.value, grid_variants.value, grid_repeats.value)
    mo.vstack([
        mo.md("**Summary** (per variant; `all`, then on and off the menu):"),
        mo.ui.table(grid.summarise(_results), selection=None),
        mo.md("**Every task:**"),
        mo.ui.table(_results, selection=None, page_size=10),
    ])
    return


if __name__ == "__main__":
    app.run()
