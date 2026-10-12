---
title: "Identity, Least Privilege and Enterprise Rules"
linkTitle: "7. Identity, Least Privilege and Enterprise Rules"
weight: 7
---

**Goal:** turn the single-persona servers of Units 3-6 into shared servers that know who
is calling and act *as* that person all the way down. You'll:
- issue signed access tokens from a small local issuer, and verify them on every request;
- refuse tokens minted for another server, so no server can replay yours;
- give each caller their own tool list, and their own choices inside it;
- carry the caller's identity to Postgres and OpenSearch through a gateway login that holds
  nothing itself;
- add a policy layer: enterprise rules as data, evaluated on every call, every verdict
  logged, approvals through elicitation;
- watch the same questions refused at four different layers, and take each layer away to
  see what was behind it.

**Where this fits:** every server so far has acted as one persona from an environment
variable (`MCP_PERSONA`). That's fine on a laptop for one person. It doesn't work for a
server the whole SOC shares. Unit 6 made the tools easy to use; this unit decides who may
use which, and what they get back. Unit 8 limits how much each caller may use; Unit 11
writes this unit's verdicts to the audit table; enterprise track E3 replaces the local
issuer with the full OAuth flow.

No model is needed for this unit.

---

## Who is calling

The two transports answer this question differently.

- **stdio.** The host starts the server as a child process. There is one caller, the
  process's owner, and the spec's guidance is that a stdio server takes its credentials
  from its environment. Units 3-6 did that with `MCP_PERSONA`.
- **Streamable HTTP.** One server, many callers, each request on its own. The caller is
  whoever the request says it is, and the request has to prove it: an
  `Authorization: Bearer <token>` header, checked before anything else happens.

This unit's server, [`examples/07/gateway_server.py`](../examples/07/gateway_server.py),
does both. Over HTTP it checks a token on every request. Over stdio it checks
`MCP_ACCESS_TOKEN` once at start and refuses to run without a valid one. Either way, every
request ends up with the same thing: a verified **caller**.

```python
@dataclass(frozen=True)
class Caller:
    """Who is calling, from a verified token. Never the token itself."""
    sub: str
    groups: tuple[str, ...]
    persona: str | None          # the backend identity the gateway acts as (None: no access)
    client_id: str = ""
    jti: str = ""                # the token's id: logs name the token without containing it
```

## A token, and what's in it

[`examples/07/issuer.py`](../examples/07/issuer.py) stands in for your identity provider. It
signs short-lived JWTs with an Ed25519 key pair, which it creates in `env/keys/`
(gitignored):

```bash
python examples/07/issuer.py keys
python examples/07/issuer.py issue priya http://127.0.0.1:8707/mcp
python examples/07/issuer.py inspect <token>
```

Four people are in its directory: **priya** (`soc-l1`), **marc** (`soc-l3`), **zoe**
(`audit`) and **sam** (`contractors`). A decoded token looks like this:

```json
{
  "iss": "https://idp.creating-mcps.local",
  "sub": "priya",
  "groups": ["soc-l1"],
  "aud": "http://127.0.0.1:8707/mcp",
  "client_id": "unit7-host",
  "scope": "mcp:tools",
  "iat": 1791734400, "nbf": 1791734400, "exp": 1791738000,
  "jti": "0dc1aa41052f879c"
}
```

Three things about it matter:

- **It says who, not what.** There is a group, not a list of permissions. What `soc-l1` may
  do is the server's policy, so changing it doesn't mean re-issuing every token.
- **It's for one server.** `aud` is the CMDB server's URL. The SIEM server will refuse it.
- **The servers can't mint one.** They hold the issuer's *public* key. A server that gets
  compromised leaks a key that can only check signatures.

## Checking it on every request

The SDK's HTTP transport does the plumbing. Give `MCPServer` a `TokenVerifier` and
`AuthSettings`, and every request's bearer token goes through your verifier before the MCP
request is read. No token, or a bad one, gets a `401` with a `WWW-Authenticate` header
pointing at the server's protected-resource metadata (`/.well-known/oauth-protected-resource/mcp`),
which tells a client which issuer to go to. That discovery is what E3 builds on.

```python
mcp = MCPServer(
    "cmdb", version="0.7.0", instructions=instructions,
    token_verifier=identity.JwtVerifier(audience=resource),
    auth=AuthSettings(issuer_url=idp.ISSUER, resource_server_url=resource,
                      required_scopes=["mcp:tools"], validate_token_resource=True),
    cache_hints={"tools/list": CacheHint(ttl_ms=60_000, scope="private")},
)
```

The verifier, in [`identity.py`](../examples/07/identity.py), is one `jwt.decode` call
with nothing left to chance:

```python
def check(self, token: str) -> dict:
    """The verified claims, or jwt.InvalidTokenError saying why not."""
    return jwt.decode(token, self.key, algorithms=[idp.ALGORITHM], audience=self.audience,
                      issuer=self.issuer, leeway=self.leeway,
                      options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]})
```

- **The algorithm is pinned.** `algorithms=["EdDSA"]` refuses an unsigned token
  (`alg: none`) and the old trick of signing an HMAC with the public key.
- **Issuer and audience are both checked.** `validate_token_resource=True` makes the SDK
  check the audience as well, so it's checked twice.
- **Why it failed goes to the log, not the caller.** The caller gets a plain `401`. The
  server logs `auth.refused` with the exception's name, never the token.

`refusals.py` knocks on the door seven ways:

| Attempt | Result |
|---|---|
| no token | `401` |
| priya's token for the SIEM server, replayed at the CMDB server | `401` (wrong audience) |
| priya's token, expired | `401` |
| a token with the right claims, signed with another key | `401` |
| an unsigned token (`alg: none`) | `401` |
| sam's valid token (`contractors`: no grants) | in, but no tools |
| priya's valid token | in, 5 tools |

## No token passthrough

The tempting design is for the server to forward the caller's token to whatever it calls
next. The MCP authorization spec forbids it, and the audience check above is why it can't
work here:

- **A token is for one audience.** If a server accepted tokens meant for anyone and passed
  them on, a malicious or compromised server could replay your token against every other
  service that trusts the same issuer. Checking `aud` makes a stolen token worthless
  anywhere else.
- **The backend can't tell who it's really serving.** A forwarded token says "priya", but
  the request came from the server. Rate limits, audit and policy at the backend all end up
  attached to the wrong party.

So the token stops at the server. The server reaches the backends with **its own**
credential and says, in the backend's own terms, which caller it is acting for.

## Carrying identity to the backends

The estate was built for this from Unit 0. `mcp_service`, the gateway's login, holds
nothing in Postgres: it's `NOINHERIT` and has been granted the persona roles, so it can
`SET ROLE` to one and do nothing else. This unit gives it the same shape in OpenSearch. Its
role reads no logs at all, and `compose.yaml` lets it impersonate the three personas and
nobody else:

```yaml
plugins.security.authcz.rest_impersonation_user.mcp_service: "l1_analyst,l3_responder,auditor"
```

`common_mcp.acting_for(persona)` switches a request into that mode, and the two connection
helpers do the rest:

```python
with acting_for("l1_analyst"):
    with pg_connect(persona()) as conn:      # logs in as mcp_service, then SET ROLE l1_analyst
        conn.execute("SELECT count(*) FROM cmdb.assets").fetchone()     # (431,)
    os_client(persona()).search(index="logs-web-*", body=query)
    # sends opendistro_security_impersonate_as: l1_analyst on every request
```

Inside it, Postgres applies `l1_analyst`'s row-level security and column privileges, and
OpenSearch applies `soc_l1`'s index permissions and field-level security. The server
doesn't filter anything itself; it asks *as priya*, and gets priya's answer.

Three details make it fail closed:

- **The gateway holds nothing itself.** If a code path forgets `acting_for`, the query runs
  as `mcp_service` and is refused. `env.py check` tests both halves: "the gateway login
  reads nothing as itself" and "the gateway acts as a caller".
- **`RESET ROLE` gets you nothing.** If anything resets the role on the connection, it
  drops back to `mcp_service`, which can't read the CMDB.
- **The persona must match.** Inside `acting_for("l1_analyst")`, asking for
  `pg_connect("l3_responder")` raises `PermissionError`.

The tools didn't change for this. Units 3, 4 and 5's tool code now asks `persona()` who
it's acting for rather than reading `MCP_PERSONA` at import. Outside `acting_for`,
`persona()` still returns `MCP_PERSONA`, so every earlier unit runs as before.

> **Porting to Elasticsearch:** Elasticsearch impersonates with the `run_as` privilege and
> the `es-security-runas-user` header. Document- and field-level security, which this
> course's L1/L3 split relies on, need a paid Elastic subscription. The free alternative is
> a separate index per audience, which is the main reason this course uses OpenSearch.

## Per-caller tool lists

A tool you may not use shouldn't be in your tool list. That isn't a security control:
the server still refuses the call. It's what Unit 6 was about. The model shouldn't spend
tokens reading a tool, or a turn calling it, when the answer is always no.

The identity layer is MCP middleware. It runs on every request, sets `acting_for` from the
caller, and post-processes `tools/list`:

```python
if ctx.method == "tools/list" and isinstance(result, dict):
    mine = datasets_for(caller.persona) if caller.persona else []
    tools = []
    for tool in result.get("tools", []):
        if not POLICY.granted(caller, tool["name"]):
            continue
        prop = tool.get("inputSchema", {}).get("properties", {}).get("dataset")
        if prop and "enum" in prop:    # only the logs this caller may search
            tool = {**tool, "inputSchema": {**tool["inputSchema"], "properties": {
                **tool["inputSchema"]["properties"],
                "dataset": {**prop, "enum": [d for d in prop["enum"] if d in mine]}}}}
        tools.append(tool)
    result = {**result, "tools": tools}
```

| Caller | CMDB tools | SIEM `dataset` choices |
|---|---|---|
| priya (`soc-l1`) | 5: no `get_contact` | auth, web, syslog |
| marc (`soc-l3`) | 6 | auth, web, syslog, edr |
| zoe (`audit`) | 2: `count_assets`, `hosts_with_vulnerability` | (no SIEM tools) |
| sam (`contractors`) | none | (none) |

Two rules go with it:

- **A hidden tool doesn't exist.** Calling `get_contact` as priya returns
  `Unknown tool: get_contact`, the SDK's exact words for a tool that really doesn't exist.
  "You're not allowed" would confirm the tool is there.
- **Never cache it as public.** Units 3 and 5 marked their tool lists `cacheScope: public`,
  which was right when everyone saw the same list. A per-caller list must be `private`, or
  a shared cache could hand marc's list to priya. The spec's `cacheScope` exists for
  exactly this.

## The policy layer

The backends enforce rows, indexes and fields. Some enterprise rules are about things no
backend knows. The rules live in
[`examples/07/policy.toml`](../examples/07/policy.toml), as data. The server evaluates
every call against them in two steps:

1. **Grants.** Which tools each group may use at all. No grant, no tool: the default is
   deny. The grants also drive the tool list above.
2. **Rules.** Conditions on a granted call, top to bottom. The first rule that matches
   decides, with `deny`, or `approve` meaning the user must confirm with a ticket. If no
   rule matches, the call goes ahead.

```toml
[personas]
soc-l1 = "l1_analyst"
soc-l3 = "l3_responder"
audit = "auditor"

[grants]
soc-l1 = ["get_asset", "find_assets", "service_dependencies", "hosts_with_vulnerability",
          "count_assets", "list_incidents", "get_incident", "event_histogram", "top_values",
          "search_events"]
soc-l3 = ["*"]
audit = ["count_assets", "hosts_with_vulnerability"]

[[rule]]
id = "restricted-hosts-stay-restricted"
tools = ["list_incidents", "event_histogram", "top_values", "search_events"]
when = { host_hidden_from_caller = true }
effect = "deny"
```

The five rules, and why each one is there:

| Rule | Who | Effect | Why the backends can't do it |
|---|---|---|---|
| `restricted-hosts-stay-restricted` | everyone | deny a log query naming a host the caller can't see in the CMDB | log documents carry no classification, so OpenSearch would happily show priya `db-prod-01`'s logs |
| `no-person-lookups-at-l1` | `soc-l1` | deny filtering logs on `user.name` | FLS hides e-mail, not names; following one person's activity is an HR rule, not a data rule |
| `person-lookups-need-a-ticket` | `soc-l3` | approve, with a ticket | the same lookup is legitimate for L3, *with a reason on record* |
| `contacts-need-a-ticket` | whoever is granted `get_contact` | approve, with a ticket | marc can read e-mail and phone, but each release should be justified |
| `writes-need-approval` | everyone | approve anything not annotated read-only | annotations only *describe* a tool; this makes the server *enforce* one |

Two of these need more than the call's arguments:

- **`host_hidden_from_caller` needs a fact from the CMDB.** The server looks it up as the
  caller (`SELECT 1 FROM cmdb.assets WHERE hostname = %s`, inside `acting_for`), so
  Postgres's row-level security answers the question. The SIEM gets the CMDB's rule
  without anyone copying classifications into the logs.
- **`approve` is an elicitation.** The tool returns `input_required` asking for a ticket
  (`INC-0042`). The form carries the pattern, and the server checks it again, since a
  client can send anything. The SDK seals the round's state and binds it to
  the arguments, as in Unit 5's hatch. A decline is a refusal; an accept runs the call and
  logs the ticket. The person confirming is the same person asking, so this is
  *accountability*, not four-eyes approval: a second approver needs a workflow outside the
  request, such as URL-mode elicitation.

Every verdict is logged, allow and deny alike: `verdict`, `rule`, and `approved_with` join
`caller`, `groups`, `persona` and `token_id` in the joinable log record. A refusal tells the
model which rule refused it and what to do instead. The model gets the rule's name, never
its conditions.

```bash
python examples/07/policy.py --test       # the rules against a table of 14 cases
python examples/07/policy.py --explain    # what each group may see, and what can stop it
```

`--test` is how you change a rule safely. It's the policy's unit test, and CI runs it. The
server also refuses to start on a policy that doesn't validate: an unknown condition, a
duplicate rule id, or a granted group with no persona.

## Four layers of refusal

[`examples/07/refusals.py`](../examples/07/refusals.py) starts both servers on free ports,
gives priya and marc a token each per server, and asks the same questions:

```bash
python examples/07/refusals.py
```

| Layer | As | Call | Result |
|---|---|---|---|
| 1 tool list | priya | `tools/list` (CMDB) | no `get_contact` |
| 1 tool list | priya | `get_contact(jump-prod-02)` | `Unknown tool` |
| 1 tool list | priya | `search_events` dataset choices | auth, web, syslog |
| 2 rows/indexes | priya | `get_asset(db-prod-01)` | "no asset named" (RLS) |
| 2 rows/indexes | marc | `get_asset(db-prod-01)` | the asset, `restricted` |
| 2 rows/indexes | priya | `count_assets(classification=restricted)` | 0 |
| 2 rows/indexes | priya | `search_events(dataset=edr)` | "edr logs aren't available to your role" |
| 3 fields | priya | `search_events(web, fields=[user.email])` | "can't return 'user.email'" |
| 3 fields | marc | the same, filtered on one user | asked for a ticket; then `user.email` as a hash |
| 4 policy | priya | `event_histogram(syslog, host.name=db-prod-01)` | refused: `restricted-hosts-stay-restricted` |
| 4 policy | marc | the same | the histogram |
| 4 policy | priya | `top_values(auth, user.name=svc_backup)` | refused: `no-person-lookups-at-l1` |
| 4 policy | marc | the same | asked for a ticket; then 203.0.113.77 |
| 4 policy | marc | `get_contact(jump-prod-02)`, declined | refused: "the user didn't confirm" |
| 4 policy | marc | `get_contact(jump-prod-02)`, INC-0042 | e-mail and phone |

Then it takes each layer away. It goes straight to the backend, as the same person,
through the same gateway login, to see what the server's own check was standing in front
of:

| Without | Tried, as priya | Backend says | Next layer holds? |
|---|---|---|---|
| the tool grant | `get_contact`'s SQL | `permission denied for table people` | yes: column privileges |
| the server's dataset check | count `edr-events-*` | `403` | yes: index permissions |
| the server's field check | read a web log's `user.*` | `{"name": ...}`, no e-mail | yes: field-level security |
| the policy | count `db-prod-01`'s logs | dozens of documents | **no** |

The first three are defence in depth: the server's check gives the model a better message,
and the backend would have refused anyway. The last row is why the policy layer exists.
OpenSearch has no idea which hosts are restricted, so for that rule the policy is the only
thing there.

## A leak the rules don't cover

Ask the SIEM server, as priya, for `top_values(dataset=syslog, field=host.name, size=50)`.
Among the answers are restricted hosts, `db-prod-01` among them, that `get_asset` tells her
don't exist. The rule only checks a host the call *names*. An aggregation that *returns*
host names goes around it.

This is the general shape of policy bugs. A rule written against the arguments misses an
answer that carries the same information. There are two fixes, and the lab's last section
lets you try them:

- **Deny the shape.** Add a rule: L1 may not aggregate on `host.name`. It's crude, and it
  breaks a legitimate question ("which hosts have the most failures?").
- **Filter the answer.** Have the server drop values the caller can't see in the CMDB
  before returning them (one `SELECT hostname ... WHERE hostname = ANY(%s)`, as the
  caller), and say how many were withheld. It's more work, and it's what the backend
  would do if it knew.

When you write a rule, ask what it's protecting (here, *the existence* of restricted
hosts), and check every tool that can reveal that, not just the ones that take it as an
argument.

## Secrets

The server holds **one** secret: its own backend login. At start it drops every other
credential that `env/.env` would have put in its environment (the persona passwords and
the admin passwords), so no code path can use them:

```python
def hold_only_my_secret() -> None:
    """The server needs exactly one secret: its own backend login. Drop the rest."""
    for persona_name, var in PERSONAS.items():
        if persona_name != "mcp_service":
            os.environ.pop(var, None)
    for var in ("PG_ADMIN_PASSWORD", "OS_ADMIN_PASSWORD"):
        os.environ.pop(var, None)
```

- **The issuer's private key never reaches a server.** Servers read `issuer.pub.pem`. On a
  real deployment they'd fetch the issuer's JWKS URL and cache it.
- **Tokens never reach a log.** Records carry the token's `jti`. `refusals.py` searches
  both servers' logs for anything shaped like a JWT and fails if it finds one.
- **stdio is no excuse.** In stdio mode, the server reads `MCP_ACCESS_TOKEN` and then
  removes it from its environment, so a child process or a crash dump can't pick it up.

> **Security:** the four layers are only as good as the weakest *answer*, as the
> aggregation leak shows. Hiding a tool is for the model; refusing the call is the control.
> Group-to-persona mapping is the most sensitive line in the policy file, because whoever
> can edit it can make anyone L3: review it like code, and keep `policy.py --test` in CI.
> Token lifetime is a trade-off. An hour is fine for a lab; production uses minutes, plus
> refresh, which is E3's ground. Impersonation is powerful too: `mcp_service` may act as
> exactly three users, never as `admin`. Check yours with `/_plugins/_security/authinfo`.

> **Observe:** every request's log record now says who: `caller`, `groups`, `persona`,
> `token_id`. Every call adds `verdict` and `rule`, and `approved_with` when a ticket was
> given. Refused tokens log `auth.refused` with the reason. Counting `verdict=deny` by
> `rule` shows which rules are actually firing. A rule that never fires is either
> protecting something nobody tries, or broken; `--test` tells you which. Unit 11 writes
> these records to `audit.tool_calls`.

> **Limits:**
> - **Connections.** One per call, as before, now through one gateway login, so every
>   caller shares `mcp_service`'s 50 Postgres connections. Unit 8 pools them and shares
>   them fairly.
> - **Facts.** The host rule costs one CMDB lookup per log query that names a host. Unit 9
>   caches facts like that, per caller, briefly.

## Lab: four layers of refusal

Open the notebook ([`labs/07_identity_and_policy.py`](../labs/07_identity_and_policy.py))
with marimo:

```bash
marimo edit labs/07_identity_and_policy.py
```

If your estate predates this unit, run `python env/env.py up` (to pick up the
impersonation setting) and `python env/env.py security` (to strip the gateway's own log
access) once; `python env/env.py check` tells you if you need to.

It walks through the unit in six steps:

1. Issue a token as anyone, for either server, and read it. Then try to cheat: replay it at
   the other server, or edit its groups without re-signing.
2. Edit the policy, and watch the table of cases re-decide as you type.
3. Start both servers and run every probe: the door, then layers 1-4, as priya and marc,
   with your policy or the shipped one.
4. Take each layer away and see what the backend says.
5. Read the audit trail the servers just wrote.
6. Make your own call as anyone. It starts with the aggregation leak.

## Challenges

1. **Close the leak with a rule.** Add a rule to `policy.toml` so that L1 can't aggregate
   on `host.name`. *Success:* `policy.py --test` still passes with a new case of your own for
   it; the lab's section 6 call is refused as priya and allowed as marc.
2. **Close it properly.** Instead of the rule, make the SIEM server drop `top_values`
   values the caller can't see in the CMDB, and add `withheld: N` to the answer. *Success:*
   priya's top 50 contains no restricted host and says how many were withheld, marc's is
   unchanged, and `refusals.py` still passes.
3. **Check the ticket.** `approve` accepts any ticket that matches the pattern. Make it
   refuse a ticket that isn't an open incident in the SIEM (`get_incident`, as the caller).
   *Success:* INC-0042 is accepted, INC-9999 is refused with a message saying why, and both
   are in the audit log.
4. **A fifth person.** Add `oncall` (group `soc-oncall`) who acts as `l1_analyst` but may
   also use `get_contact`, with a ticket. *Success:* no code changes, only `issuer.py`'s
   directory and `policy.toml`; `refusals.py` passes, and a probe you add shows oncall
   getting a contact after giving a ticket.

## Recap

- **Who is calling** is per process over stdio and per request over HTTP. Over HTTP, a
  bearer token is verified before the MCP request is read: signature, pinned algorithm,
  issuer, audience, expiry.
- **A token says who, for one server.** Groups, not permissions; `aud` is one server, so a
  token can't be replayed elsewhere. **No token passthrough:** the token stops at the
  server.
- **Identity reaches the backends** through a gateway login that holds nothing itself:
  `SET ROLE` in Postgres, impersonation in OpenSearch. RLS, FLS and masking apply to the
  person. Forget to say who, and the query is refused.
- **Tool lists are per caller:** grants filter the list and narrow the choices; a hidden
  tool answers "Unknown tool"; the list is cached `private`.
- **The policy layer** holds the rules no backend can express, as data: grants, then rules,
  first match wins, default deny, with `approve` going through elicitation with a ticket.
  Every verdict is logged.
- **Four layers** refused the same questions. Three of them sit on top of a backend that
  would refuse anyway. One rule, keeping restricted hosts restricted in the logs, has
  nothing behind it. An aggregation went around it.
- **One secret per server**, and no token in any log.

## Next

**[Unit 8: Resource Management: Many Callers, Finite Backends](08-resource-management-many-callers-finite-backends.md)**: the gateway
now serves many callers through one login and one set of backends. Unit 8 bounds what each
caller may use: concurrency and pools, per-caller rate limits and quotas, backpressure the
model respects, and timeouts all the way down.
