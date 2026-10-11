import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 3 lab: catalogs, raw vs curated",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 3 lab: catalogs, raw vs curated

    How does a model learn what data exists? This lab puts the two answers side by side, as
    one persona at a time:

    - **raw discovery**: what Postgres (`information_schema`) and OpenSearch (`_mapping`)
      say about themselves;
    - **the curated catalog** (`examples/03/catalog.py`): what the data means, how to ask
      for it, and what this persona may see.

    You'll profile what's actually in the log indexes, read the catalog as different
    personas, measure what each approach costs in the model's context, check that the
    catalog agrees with what the backends enforce, and (with a model endpoint) measure query
    accuracy over repeated runs. It needs the estate: `python env/env.py up` and `seed`.
    """)
    return


@app.cell
def _():
    import importlib.util
    import sys

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    UNIT3 = COURSE_DIR / "examples" / "03"
    sys.path[:0] = [str(UNIT3), str(COURSE_DIR / "examples")]

    def _load(name):
        spec = importlib.util.spec_from_file_location(f"unit3_{name}", UNIT3 / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    catalog, measure, profile, evals = (_load(n) for n in
                                        ("catalog", "measure", "profile", "eval_discovery"))
    from common_mcp import llm, load_env, os_client

    load_env()
    return catalog, evals, llm, measure, mo, os_client, profile


@app.cell
def _(catalog, mo):
    persona = mo.ui.dropdown(options=list(catalog.ANALYSTS), value="l1_analyst",
                             label="act as")
    persona
    return (persona,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. What's actually in a log index?

    A mapping lists every field an index *could* hold. The profiler asks OpenSearch, as the
    persona above, how many documents really hold each one. Try `logs-web-*` as
    `l1_analyst` and then as `l3_responder`, and look for `user.email`.
    """)
    return


@app.cell
def _(catalog, mo, persona):
    _patterns = [n for n in catalog.datasets_for(persona.value)
                 if catalog.DATASETS[n]["backend"] == "opensearch"]
    pattern = mo.ui.dropdown(options=_patterns or ["(none for this persona)"],
                             value=(_patterns or ["(none for this persona)"])[0],
                             label="index pattern")
    pattern
    return (pattern,)


@app.cell
def _(mo, os_client, pattern, persona, profile):
    mo.stop(pattern.value.startswith("("), mo.md("_This persona can't read any log index._"))
    _p = profile.profile(os_client(persona.value), pattern.value)
    _filled = [r for r in _p["fields"] if r["docs"]]
    mo.vstack([
        mo.hstack([
            mo.stat(f"{_p['indices']}", label="daily indexes"),
            mo.stat(f"{_p['populated']} / {_p['mapped']}", label="fields with any data"),
            mo.stat(f"{_p['mapping_bytes']:,} B", label="raw _mapping for the pattern"),
        ], justify="start"),
        mo.ui.table([{"field": r["field"], "type": r["type"], "share": f"{r['share']:.1%}",
                      "top values": ", ".join(map(str, r["top"]))} for r in _filled],
                    selection=None, page_size=12),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. The catalog, as this persona sees it

    Pick a dataset. Compare what `l1_analyst`, `l3_responder` and `auditor` are told: whole
    datasets disappear, PII fields disappear, and a `withheld` note says that something is
    there without saying what.
    """)
    return


@app.cell
def _(catalog, mo, persona):
    dataset = mo.ui.dropdown(options=catalog.datasets_for(persona.value),
                             value=catalog.datasets_for(persona.value)[0], label="dataset")
    _rows = [{"dataset": n, **{p: (len(catalog.describe(n, p)["fields"])
                                   if catalog.describe(n, p) else "-")
                               for p in catalog.ANALYSTS}}
             for n in catalog.DATASETS]
    mo.vstack([mo.md(f"Catalog version **{catalog.VERSION}**. Fields visible per persona:"),
               mo.ui.table(_rows, selection=None, page_size=12), dataset])
    return (dataset,)


@app.cell
def _(catalog, dataset, mo, persona):
    _d = catalog.describe(dataset.value, persona.value)
    _meta = {k: v for k, v in _d.items() if k not in ("fields", "name")}
    mo.vstack([
        mo.md("\n".join(f"- **{k}**: {v}" for k, v in _meta.items())),
        mo.ui.table([{"field": f, "type": s["type"], "use": s["use"],
                      "sensitivity": s["sensitivity"], "description": s["desc"],
                      "examples": ", ".join(map(str, s.get("examples", []))),
                      "note": s.get("note", "")}
                     for f, s in _d["fields"].items()], selection=None, page_size=12),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. What does discovery cost?

    This runs both discovery servers through the Unit 2 host, as the persona above, and
    discovers *everything* once each way. Bytes are exact; tokens are estimated at 4 bytes
    per token. The last row is what a model gets if it asks for the mapping of `logs-*`.
    """)
    return


@app.cell
def _(mo):
    run_measure = mo.ui.run_button(label="Measure")
    run_measure
    return (run_measure,)


@app.cell
async def _(measure, mo, persona, run_measure):
    mo.stop(not run_measure.value, mo.md("_Press **Measure**._"))
    _rows = await measure.measure(persona.value)
    _t = measure.totals(_rows)
    mo.vstack([
        mo.hstack([mo.stat(f"~{_t['raw']['tokens']:,}", label="raw: discover everything"),
                   mo.stat(f"~{_t['curated']['tokens']:,}",
                           label="curated: discover everything")], justify="start"),
        mo.ui.table(_rows, selection=None, page_size=12),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Does the catalog agree with the backends?

    The catalog isn't the security boundary; Postgres and OpenSearch are. It must never
    *promise* a persona something the backend refuses. Tick the box to plant a mistake
    (offer `edr-events-*` and `people.email` to an L1 analyst) and run the check again.
    """)
    return


@app.cell
def _(mo):
    plant = mo.ui.checkbox(label="plant a mistake in the catalog")
    run_check = mo.ui.run_button(label="Check against the estate")
    mo.hstack([plant, run_check], justify="start")
    return plant, run_check


@app.cell
def _(catalog, mo, plant, run_check):
    mo.stop(not run_check.value, mo.md("_Press **Check against the estate**._"))
    _edr = catalog.DATASETS["edr-events-*"]
    _email = catalog.DATASETS["cmdb.people"]["fields"]["email"]
    _saved = (_edr["allowed"], _email["allowed"])
    try:
        if plant.value:
            _edr["allowed"] = (*_edr["allowed"], "l1_analyst")
            _email["allowed"] = (*_email["allowed"], "l1_analyst")
        _problems = [p for who in catalog.ANALYSTS for p in catalog.check(who)]
    finally:
        _edr["allowed"], _email["allowed"] = _saved
    (mo.callout(mo.md("\n".join(f"- {p}" for p in _problems)), kind="danger") if _problems
     else mo.callout(mo.md(f"Catalog {catalog.VERSION} agrees with the estate for every "
                           "persona."), kind="success"))
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. Does it change the answers?

    Nine questions, each in a fresh conversation, under three conditions: no catalog, raw
    discovery, the curated catalog. Every condition runs several times, because one run of
    a model is an anecdote: read the **range** as well as the mean. Gold answers are
    computed live as the same persona. This needs the model endpoint, and takes a while
    (questions × conditions × repeats conversations).
    """)
    return


@app.cell
def _(evals, llm, mo):
    repeats = mo.ui.slider(1, 5, value=3, label="repeats")
    conditions = mo.ui.multiselect(options=list(evals.CONDITIONS), value=list(evals.CONDITIONS),
                                   label="conditions")
    run_eval = mo.ui.run_button(label="Run the eval", disabled=llm() is None)
    mo.vstack([mo.hstack([repeats, conditions, run_eval], justify="start"),
               mo.md("_No model endpoint configured: set OPENAI_BASE_URL and OPENAI_API_KEY "
                     "to run it._") if llm() is None else mo.md("")])
    return conditions, repeats, run_eval


@app.cell
async def _(conditions, evals, mo, persona, repeats, run_eval):
    mo.stop(not run_eval.value, mo.md("_Press **Run the eval**._"))
    _results = await evals.run(persona.value, list(conditions.value), repeats.value)
    mo.vstack([
        mo.md("**Per condition** (accuracy is the mean over repeats; min and max show the "
              "spread):"),
        mo.ui.table(evals.summarise(_results), selection=None),
        mo.md("**Every answer:**"),
        mo.ui.table(_results, selection=None, page_size=10),
    ])
    return


if __name__ == "__main__":
    app.run()
