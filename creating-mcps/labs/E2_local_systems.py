import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="E2 lab: the local systems server",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # E2 lab: the local systems server

    Unit 12's triage ended on the pivot: svc_backup logged in to **db-prod-01** at 02:46:15,
    and the SIEM has nothing after that. The rest is on the host. In this lab you:

    1. plant db-prod-01 as it was found the next morning,
    2. triage it through the local systems server, running in the sandbox, and check every
       finding against what was planted,
    3. try paths of your own against the server's path rules, and
    4. probe the sandbox's walls from inside.

    No estate and no model are needed. Docker and the `creating-mcps-local` image make it a
    sandbox (`docker build -f examples/E2/Dockerfile -t creating-mcps-local .`); without
    them, the same server runs as a plain process, and the lab says so.
    """)
    return


@app.cell
def _():
    import importlib.util
    import sys

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "E2")]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    drill = _load("e2_drill", EXAMPLES / "E2" / "drill.py")
    sandbox = _load("sandbox", EXAMPLES / "E2" / "sandbox.py")
    server = _load("local_server", EXAMPLES / "E2" / "local_server.py")
    return drill, mo, sandbox, server


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. The host, as found

    `suspect_host.py` writes db-prod-01's files into a directory: what a live-response
    collector or a mounted disk image gives an investigator. The server reads it as its
    `--root`; on a live host, the root is `/`. Nothing in it is harmful: the "payload" is a
    shell script that exits, and the keys and hashes are made up.
    """)
    return


@app.cell
def _(drill, mo, sandbox):
    root, evidence, facts = drill.plant()
    _files = sorted("/" + p.relative_to(root).as_posix() for p in root.rglob("*")
                    if (p.is_file() or p.is_symlink()) and "/proc/" not in "/" + p.relative_to(
                        root).as_posix())
    mo.vstack([
        mo.md(f"Planted in `{root}`. Sandbox image: "
              + ("**found**, so the server runs inside it." if sandbox.available() else
                 "**not found**, so the server runs as a plain process (no sandbox).")),
        mo.hstack([mo.ui.table([{"file": f} for f in _files], selection=None),
                   mo.json(facts)], widths=[1, 1]),
    ])
    return evidence, facts, root


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Triage db-prod-01

    The drill connects to the server over stdio (through `docker run -i` when it can), makes
    the calls a responder would, and checks each finding against the planted facts. Watch the
    `asked` column: only collecting evidence asks the person, and it asks before anything is
    copied. The declined collection copies nothing; the approved one copies three files with
    a manifest of their hashes.
    """)
    return


@app.cell
def _(mo):
    run_triage = mo.ui.run_button(label="Run the triage")
    run_triage
    return (run_triage,)


@app.cell
async def _(drill, evidence, mo, root, run_triage):
    mo.stop(not run_triage.value, mo.md("_Press **Run the triage**._"))
    with mo.status.spinner(title="Starting the server and triaging db-prod-01..."):
        run = await drill.triage(root, evidence)
    _checks = drill.check(run)

    def _short(c):
        if c["error"]:
            return c["text"].split(": ", 1)[-1][:110]
        d = c["data"]
        if isinstance(d, list):
            return f"{len(d)} rows"
        return ", ".join(f"{k}={str(v)[:40]}" for k, v in list((d or {}).items())[:3])

    mo.vstack([
        mo.md(f"**Sandboxed:** {run['sandboxed']}"),
        mo.ui.table([{"tool": c["tool"], "args": str(c["args"])[:70], "asked": c["asked"],
                      "ms": c["ms"], "came back": _short(c)} for c in run["calls"]],
                    selection=None),
        mo.md("\n".join(f"- {'✅' if ok else '❌'} **{what}**: {detail}"
                        for what, ok, detail in _checks)),
    ])
    return (run,)


@app.cell
def _(mo, run):
    _hist = next(c for c in run["calls"] if c["tool"] == "read_file")
    mo.accordion({
        "The shell history, as the model sees it (redacted)":
            mo.md(f"```\n{_hist['data']['content']}\n```"),
        "The evidence manifest": mo.json(run["manifest"]),
    })
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Paths, your way

    The server takes the host's own paths (`/etc/passwd`) and resolves them under its root.
    Try to get something it shouldn't give: `/etc/shadow`, `/home/svc_backup/.pgpass`,
    `/home/svc_backup/etc-link/hostname` (a symlink that points out of the image),
    `/../../etc/hostname`, `/home/camille/.ssh/id_ed25519.pub` (public: fine), or a file of
    your own. This calls the server's functions directly, in this process.
    """)
    return


@app.cell
def _(mo):
    path = mo.ui.text(value="/etc/shadow", label="Path on the host", full_width=True)
    path
    return (path,)


@app.cell
def _(mo, path, root, server):
    from pathlib import Path as _Path

    server.ROOT = _Path(root)
    _rows = []
    for _tool in (server.read_file, server.file_info):
        try:
            _got = _tool(path.value)
            _rows.append({"tool": _tool.__name__, "outcome": "returned",
                          "detail": str(_got)[:400]})
        except Exception as _e:  # noqa: BLE001 -- a ToolError is the point
            _rows.append({"tool": _tool.__name__, "outcome": "refused", "detail": str(_e)})
    mo.ui.table(_rows, selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. The walls, from inside

    The server parses files an attacker wrote. If a hostile file ever took the parser over,
    what could it do? These probes run in the same sandbox, with the same flags, and try:
    write to the host, write anywhere, reach the C2 address, resolve a name, be root, hold a
    capability, escalate, and exceed the memory and process limits.
    """)
    return


@app.cell
def _(mo):
    run_walls = mo.ui.run_button(label="Probe the walls")
    run_walls
    return (run_walls,)


@app.cell
def _(mo, root, run_walls, sandbox):
    mo.stop(not run_walls.value, mo.md("_Press **Probe the walls**._"))
    mo.stop(not sandbox.available(), mo.md(
        "_No `creating-mcps-local` image: build it to see the walls._"))
    _rows = sandbox.walls(root)
    mo.vstack([
        mo.ui.table([{"": "🧱" if r["held"] else "❌", **r} for r in _rows], selection=None),
        mo.accordion({"The docker command": mo.md(
            "```\n" + " \\\n  ".join(sandbox.argv(root)) + "\n```")}),
    ])
    return


if __name__ == "__main__":
    app.run()
