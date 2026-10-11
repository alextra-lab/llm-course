import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="Unit 7 lab: four layers of refusal",
)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Unit 7 lab: four layers of refusal

    One CMDB server and one SIEM server, now shared: many people call them over HTTP, each
    with their own token (`examples/07/gateway_server.py`). The servers act *as the caller* at
    the backends, through a gateway login that holds nothing itself. Four people:

    | person | directory group | acts as |
    |---|---|---|
    | **priya** | `soc-l1` | `l1_analyst` |
    | **marc** | `soc-l3` | `l3_responder` |
    | **zoe** | `audit` | `auditor` |
    | **sam** | `contractors` | nobody: no grants |

    You'll read a token, check the policy, then ask the same questions as different people
    and see which layer stops what: the tool list, the rows and indexes, the fields, the
    policy. No model is needed. It needs the estate (`python env/env.py up` and `seed`); if
    you set the estate up before Unit 7, run `python env/env.py up` and
    `python env/env.py security` once (`check` says if you need to).
    """)
    return


@app.cell
def _():
    import base64
    import importlib.util
    import json
    import sys
    import tempfile
    from pathlib import Path

    import marimo as mo

    COURSE_DIR = mo.notebook_dir().parent        # labs/ or work/ -> creating-mcps/
    EXAMPLES = COURSE_DIR / "examples"
    sys.path[:0] = [str(EXAMPLES / "07"), str(EXAMPLES / "05"), str(EXAMPLES)]

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    issuer = _load("issuer", EXAMPLES / "07" / "issuer.py")
    policy = _load("policy", EXAMPLES / "07" / "policy.py")
    identity = _load("identity", EXAMPLES / "07" / "identity.py")
    refusals = _load("unit7_refusals", EXAMPLES / "07" / "refusals.py")
    from common_mcp import load_env

    load_env()
    issuer.keys()
    LOG_DIR = Path(tempfile.mkdtemp(prefix="unit7-lab-"))
    return LOG_DIR, base64, identity, issuer, json, mo, policy, refusals


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. Read a token

    The issuer signs; servers verify with its public key, so they can check a token but
    never mint one. Look at what the token says, and what it *doesn't*: no permissions, no
    password. Who you are is the issuer's business; what that allows is the server's policy.
    """)
    return


@app.cell
def _(issuer, mo):
    who = mo.ui.dropdown(options=sorted(issuer.DIRECTORY), value="priya", label="person")
    aud = mo.ui.dropdown(options={"the CMDB server": "http://127.0.0.1:8707/mcp",
                                  "the SIEM server": "http://127.0.0.1:8708/mcp"},
                         value="the CMDB server", label="for")
    mo.hstack([who, aud], justify="start")
    return aud, who


@app.cell
def _(aud, issuer, json, mo, who):
    token = issuer.issue(who.value, aud.value)
    _seen = issuer.inspect(token)
    mo.vstack([
        mo.md(f"`{token[:48]}…` ({len(token)} characters, three base64 parts: header, "
              "claims, signature)"),
        mo.md(f"```json\n{json.dumps(_seen, indent=1)}\n```"),
    ])
    return (token,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    Now try to cheat. Each line is what the verifier says (`identity.JwtVerifier.check`) about
    a token: the real one, the same token at the other server, and the same token with the
    claims edited to say `soc-l3` but the signature left as it was.
    """)
    return


@app.cell
def _(aud, base64, identity, json, mo, token):
    def _verdict(tok, audience):
        try:
            identity.JwtVerifier(audience=audience).check(tok)
            return "accepted"
        except Exception as e:  # noqa: BLE001
            return f"refused: {type(e).__name__}"

    _head, _body, _sig = token.split(".")
    _claims = json.loads(base64.urlsafe_b64decode(_body + "=" * (-len(_body) % 4)))
    _claims["groups"] = ["soc-l3"]
    _edited = base64.urlsafe_b64encode(json.dumps(_claims).encode()).rstrip(b"=").decode()
    _other = ("http://127.0.0.1:8708/mcp" if aud.value.endswith("8707/mcp")
              else "http://127.0.0.1:8707/mcp")
    mo.ui.table([
        {"token": "as issued, at the server it's for", "verifier": _verdict(token, aud.value)},
        {"token": "as issued, at the other server", "verifier": _verdict(token, _other)},
        {"token": "groups edited to soc-l3", "verifier": _verdict(
            f"{_head}.{_edited}.{_sig}", aud.value)},
    ], selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. The rules, as data

    `examples/07/policy.toml`: **grants** say which tools each group may use at all (and so
    what its tools/list shows); **rules** decide what a granted call may do. Edit it below:
    the table of cases re-decides as you type, with no server involved. Then section 3 can
    run the servers with your version.
    """)
    return


@app.cell
def _(mo, policy):
    policy_text = mo.ui.code_editor(value=policy.POLICY_FILE.read_text(), language="toml",
                                    min_height=300)
    policy_text
    return (policy_text,)


@app.cell
def _(mo, policy, policy_text):
    try:
        mine = policy.Policy.loads(policy_text.value)
        _rows = policy.run_cases(mine)
        _changed = sum(not r["ok"] for r in _rows)
        _out = mo.vstack([
            mo.md(f"**{len(mine.grants)} grants, {len(mine.rules)} rules.** "
                  + (f"{_changed} case(s) now decided differently from the shipped policy."
                     if _changed else "Every case decided as the shipped policy decides it.")),
            mo.ui.table([{k: r[k] for k in ("ok", "who", "tool", "args", "verdict", "rule",
                                            "expected")} for r in _rows],
                        selection=None, page_size=14),
        ])
    except Exception as e:  # noqa: BLE001
        mine = None
        _out = mo.callout(mo.md(f"The policy doesn't load: `{e}`. The servers would refuse "
                                "to start with it."), kind="danger")
    _out
    return (mine,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Ask as different people

    This starts both servers on free ports, gives each person a token per server, and asks
    the same questions. Every probe says what it expected, what came back, and which layer
    decided. Probes that ask for a ticket are answered with INC-0042 (or declined, where the
    probe says so).
    """)
    return


@app.cell
def _(mo):
    use_mine = mo.ui.checkbox(label="run the servers with my edited policy", value=False)
    run_probes = mo.ui.run_button(label="Start the servers and run the probes")
    mo.hstack([run_probes, use_mine], justify="start")
    return run_probes, use_mine


@app.cell
async def _(LOG_DIR, mine, mo, policy_text, refusals, run_probes, use_mine):
    mo.stop(not run_probes.value, mo.md("_Press **Start the servers and run the probes**._"))
    _policy = None
    if use_mine.value and mine is not None:
        _policy = LOG_DIR / "my_policy.toml"
        _policy.write_text(policy_text.value)
    result = await refusals.run_all(LOG_DIR, _policy)
    _surprises = sum(not r["ok"] for r in result["door"] + result["probes"])
    mo.vstack([
        mo.md(f"**{len(result['door']) + len(result['probes']) - _surprises} of "
              f"{len(result['door']) + len(result['probes'])}** probes behaved as expected."),
        mo.md("**The door** (layer 0): a request without a good token never reaches MCP."),
        mo.ui.table(result["door"], selection=None),
        mo.md("**Layers 1 to 4**, as priya (L1) and marc (L3)."),
        mo.ui.table(result["probes"], selection=None, page_size=15),
    ])
    return (result,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Take a layer away

    Straight to the backends, as the same person, through the same gateway login: what was
    each server check standing in front of? Where the next layer holds, the server's check
    was defence in depth. Where it doesn't, that check was the only thing there.
    """)
    return


@app.cell
def _(mo, result):
    mo.ui.table(result["bypass"], selection=None)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. The audit trail

    Every call both servers just answered, from their own logs: who, which tool, the
    policy's verdict and rule, how the call ended, and the ticket where one was given. Allow
    and deny are both recorded. The token appears only as its id.
    """)
    return


@app.cell
def _(mo, result):
    mo.vstack([
        mo.ui.table(result["audit"], selection=None, page_size=12),
        mo.md(f"Tokens found anywhere in the logs: **{result['tokens_in_logs']}**."),
    ])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. Your own call

    Pick a person, a server and a tool, and write the arguments. The call it starts with is a
    real leak: the top 50 hosts in the syslog, as priya. Some of them are restricted hosts she
    can't see in the CMDB (`get_asset` says they don't exist), because the policy's host rule
    only looks at a host the call *names*. Find one, then stop it: a rule in section 2, or a
    better fix in the server (Challenge 2), and run it again.
    """)
    return


@app.cell
def _(issuer, mo):
    me = mo.ui.dropdown(options=sorted(issuer.DIRECTORY), value="priya", label="as")
    server = mo.ui.dropdown(options=["cmdb", "siem"], value="siem", label="server")
    tool = mo.ui.text(value="top_values", label="tool")
    args = mo.ui.text_area(value='{"dataset": "syslog", "field": "host.name", "start": '
                                 '"now-21d", "size": 50}',
                           label="arguments (JSON)", full_width=True)
    answer = mo.ui.radio(options=["accept", "decline"], value="accept", inline=True,
                         label="if asked for a ticket")
    go = mo.ui.run_button(label="Call it")
    mo.vstack([mo.hstack([me, server, tool, answer], justify="start"), args, go])
    return answer, args, go, me, server, tool


@app.cell
async def _(LOG_DIR, answer, args, go, json, me, mine, mo, policy_text, refusals, server,
            tool, use_mine):
    mo.stop(not go.value, mo.md("_Press **Call it**._"))
    _policy = None
    if use_mine.value and mine is not None:
        _policy = LOG_DIR / "my_policy.toml"
        _policy.write_text(policy_text.value)
    async with refusals.servers(LOG_DIR / "mine", _policy) as _urls:
        _got, _says = await refusals.call_as(_urls[server.value], me.value, tool.value,
                                             json.loads(args.value), answer.value)
    mo.vstack([mo.md(f"**{_got}**"), mo.md(f"```\n{_says[:3000]}\n```")])
    return


if __name__ == "__main__":
    app.run()
