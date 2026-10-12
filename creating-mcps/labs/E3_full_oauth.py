import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="E3 lab: full OAuth for remote servers",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # E3 lab: full OAuth for remote servers

    Unit 7 handed out tokens directly. Here a host that has never seen the gateway is given
    one URL and gets everything else the way a real deployment does. In this lab you:

    1. discover the gateway's issuer from a 401, with no token at all,
    2. sign in as different people through a host, consent, and watch the wire,
    3. try a token where it wasn't issued for,
    4. let a token run out, refresh it, and replay the old refresh token,
    5. walk the flow by hand and hit every guard, and
    6. read the authorization server's audit log.

    No estate and no model are needed. Three servers start on free loopback ports, in this
    notebook's process.
    """)
    return


@app.cell
def _():
    import importlib.util
    import sys

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "E3")]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    drill = _load("e3_drill", EXAMPLES / "E3" / "drill.py")
    return drill, mo


@app.cell
def _(drill, mo):
    servers = drill.Servers().__enter__()        # daemon threads: they stop with the kernel
    mo.md(f"""
    | server | URL |
    |---|---|
    | authorization server | `{servers.issuer}` |
    | the gateway (MCP, protected resource) | `{servers.resource}` |
    | the ticketing API (not MCP; behind the gateway) | `{servers.tickets_url}` |
    """)
    return (servers,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. Discovery, from nothing

    A request without a token gets a 401 whose `WWW-Authenticate` header names the gateway's
    protected-resource metadata. That names the authorization server; the server's own
    metadata names its endpoints, what it supports and where its public keys are. A host
    needs nothing else configured.
    """)
    return


@app.cell
async def _(drill, mo, servers):
    found = await drill.discovery(servers)
    mo.vstack([
        mo.md(f"**{found['status']}** `WWW-Authenticate: {found['www_authenticate']}`"),
        mo.hstack([
            mo.vstack([mo.md(f"`{found['prm_url']}`"), mo.json(found["prm"])]),
            mo.vstack([mo.md(f"`{found['asm_url']}`"), mo.json(found["asm"])]),
        ], widths=[1, 1]),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Sign in through a host

    Pick who is at the browser and what they answer. The host connects knowing only the
    gateway's URL, then calls `whoami`, reads two tickets through the gateway, and tries
    `isolate_host`, which needs `mcp:actions`. Try **priya** (tier 1), **marc** (tier 3),
    **zoe** (audit) and **sam** (contractors), and try **deny**.
    """)
    return


@app.cell
def _(mo):
    who = mo.ui.dropdown(["priya", "marc", "zoe", "sam"], value="priya", label="Person")
    decision = mo.ui.radio(["approve", "deny"], value="approve", label="At the consent page")
    run_person = mo.ui.run_button(label="Connect and call")
    mo.hstack([who, decision, run_person], justify="start")
    return decision, run_person, who


@app.cell
async def _(decision, drill, mo, run_person, servers, who):
    mo.stop(not run_person.value, mo.md("_Press **Connect and call**._"))
    with mo.status.spinner(title=f"{who.value} is signing in..."):
        scene = await drill.person(servers, who.value, decision.value)

    def _short(c):
        if c["error"]:
            return c["text"].split(": ", 1)[-1][:120]
        return str(c["data"])[:160]

    mo.vstack([
        mo.md("### The calls"),
        mo.ui.table([{"tool": c["tool"], "args": str(c["args"]), "ok": not c["error"],
                      "came back": _short(c)} for c in scene["calls"]], selection=None),
        mo.md("### What the consent page asked"),
        mo.ui.table([{"scope": p["scope"], "answer": p["decision"], "page": p["page"][:220]}
                     for p in scene["consent"]], selection=None),
        mo.accordion({f"The wire: {len(scene['wire'])} HTTP exchanges the host made": mo.ui.table(
            [{"#": i, "request": f"{w['method']} {w['url']}", "mcp": w["mcp"],
              "status": w["status"], "WWW-Authenticate": w["www_authenticate"][:120]}
             for i, w in enumerate(scene["wire"])], selection=None)}),
    ])
    return (scene,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. The token on the wrong door

    The last person's gateway token, sent straight to the ticketing API; a token the gateway
    got by exchange, sent to the gateway; the host itself asking to exchange a token; and
    the gateway asking to act somewhere it isn't allowed to.
    """)
    return


@app.cell
def _(mo):
    run_doors = mo.ui.run_button(label="Try the doors")
    run_doors
    return (run_doors,)


@app.cell
async def _(drill, mo, run_doors, scene, servers):
    mo.stop(not run_doors.value, mo.md("_Press **Try the doors**._"))
    mo.stop(not scene["first_token"], mo.md("_That person got no token: sign in as someone "
                                            "who can._"))
    _doors = await drill.audiences(servers, scene["first_token"], scene["client_id"])
    mo.vstack([
        mo.ui.table([{"tried": k.replace("_", " "), "status": v.get("status"),
                      "answer": str(v.get("body") or v.get("www_authenticate"))[:160]}
                     for k, v in _doors.items() if k != "exchanged_claims"], selection=None),
        mo.accordion({"An exchanged token's claims (decoded, not verified)":
                      mo.json(_doors["exchanged_claims"])}),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Refresh, rotate, replay

    marc signs in, and his access token is made to run out. The host refreshes it without
    asking him again, and the refresh token is replaced. Then the OLD refresh token is
    replayed, as a thief who copied it would: refused, and the new one dies with it.
    """)
    return


@app.cell
def _(mo):
    run_refresh = mo.ui.run_button(label="Refresh and replay")
    run_refresh
    return (run_refresh,)


@app.cell
async def _(drill, mo, run_refresh, servers):
    mo.stop(not run_refresh.value, mo.md("_Press **Refresh and replay**._"))
    _f = await drill.refresh(servers)
    mo.md(f"""
    | | |
    |---|---|
    | access token before | `jti {_f['before']['data']['jti']}` |
    | after the refresh | `jti {_f['after']['data']['jti']}`, consent pages shown: {_f['pages']} |
    | refresh token rotated | {_f['rotated']} |
    | the old refresh token, replayed | `{_f['replay']}` |
    | the current one, after the replay | `{_f['current_after_replay']}` |
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. By hand

    The same flow without the SDK's client: plain HTTP requests, each one aimed at a guard.
    """)
    return


@app.cell
def _(mo):
    run_hand = mo.ui.run_button(label="Walk the flow")
    run_hand
    return (run_hand,)


@app.cell
async def _(drill, mo, run_hand, servers):
    mo.stop(not run_hand.value, mo.md("_Press **Walk the flow**._"))
    _steps = await drill.by_hand(servers)
    mo.ui.table([{"step": s["step"], "status": s["status"], "expected": s["expect"],
                  "got": str(s["got"].get("error_description") or s["got"].get("error")
                             or sorted(s["got"]))[:140]} for s in _steps], selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. The audit log

    Everything the authorization server decided, and every request the ticketing API saw.
    Tokens appear by `jti` only. Press the button again after the other parts to see theirs.
    """)
    return


@app.cell
def _(mo):
    read_log = mo.ui.run_button(label="Read the log")
    read_log
    return (read_log,)


@app.cell
def _(mo, read_log, servers):
    read_log.value      # pressing it reads the log again
    mo.vstack([
        mo.ui.table([{k: (str(v) if isinstance(v, (list, dict)) else v) for k, v in e.items()}
                     for e in servers.auth.events], selection=None),
        mo.md("**The ticketing API**"),
        mo.ui.table(servers.tickets.log or [{"outcome": "no requests yet"}], selection=None),
    ])
    return


if __name__ == "__main__":
    app.run()
