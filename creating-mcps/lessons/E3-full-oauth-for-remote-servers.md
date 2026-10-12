---
title: "E3. Full OAuth for Remote Servers"
linkTitle: "E3. Full OAuth for Remote Servers"
weight: 16
---

**Goal:** let a host that has never seen your server connect to it the way a real deployment
does: find the issuer, register, send a person to consent, and get a token that works at that
server and nowhere else. You'll:
- discover everything from one URL and a 401, with no configuration in the host;
- run an authorization server with client registration, PKCE, consent bounded by the
  person's groups, and rotating refresh tokens;
- ask for more scope only when a tool needs it (step-up), and see who can approve it;
- call a downstream API *for* the person with token exchange, never by passing their token
  on;
- walk the flow by hand and hit every guard: a hostile redirect URI, no resource, a wrong PKCE
  verifier, a code used twice, a refresh token replayed.

**Where this fits:** the third unit of the optional enterprise track, after Unit 7. Unit 7's
issuer handed tokens out directly and said the full flow was for later; this is later. The
gateway keeps Unit 7's verification rules (signature, issuer, audience, expiry) and gains
everything around them. No estate and no model are needed: three small servers start on
loopback ports.

## What Unit 7 skipped

Unit 7 ran `issuer.py issue priya http://127.0.0.1:8707/mcp` and pasted the result into the
host. That stood in for five things a shared server can't skip:

| question | Unit 7 | here |
|---|---|---|
| Which issuer does this server trust? | you knew | the server says so, in metadata |
| Who is this host? | nobody asked | it registers and gets a client id |
| Did the *person* agree? | the token just appeared | a consent page, with their groups as the bound |
| How long does it last? | an hour, then paste another | five minutes, refreshed without asking again |
| What does the server send downstream? | nothing downstream | a token of its own, exchanged, naming them both |

## The cast

[`examples/E3/`](../examples/E3/) has three servers and a drill:

| server | file | role |
|---|---|---|
| authorization server | [`auth_server.py`](../examples/E3/auth_server.py) | registers hosts, shows consent, issues and exchanges tokens |
| the gateway | [`gateway.py`](../examples/E3/gateway.py) | an MCP server and OAuth protected resource: `whoami`, `ticket_status`, `isolate_host` |
| ticketing API | [`ticket_api.py`](../examples/E3/ticket_api.py) | a plain HTTP API behind the gateway, the kind MCP servers sit in front of |

The people are Unit 7's: priya (`soc-l1`), marc (`soc-l3`), zoe (`audit`) and sam
(`contractors`). What each group may approve is one table in the authorization server:

```python
SCOPES = {
    "mcp:tools":    ("use the gateway's read-only tools", {"soc-l1", "soc-l3", "audit"}),
    "mcp:actions":  ("ask the gateway to change hosts (isolate, release)", {"soc-l3"}),
    "tickets:read": ("read tickets, through a server acting for you", {"soc-l1", "soc-l3",
                                                                       "audit"}),
}
```

The person approves; their groups bound what they *can* approve. Unit 7's policy file still
decides what each tool does for each person; this table decides which tokens exist at all.

## Discovery, from one URL

The host is given `http://127.0.0.1:<port>/mcp` and nothing else. Its first request has no
token, and the SDK answers it (from the `AuthSettings` Unit 7 already wrote):

```
POST /mcp                                     401
  WWW-Authenticate: Bearer error="invalid_token", error_description="Authentication required",
                    resource_metadata="http://127.0.0.1:<port>/.well-known/oauth-protected-resource/mcp"
GET  /.well-known/oauth-protected-resource/mcp            200   (RFC 9728)
  {"resource": ".../mcp", "authorization_servers": ["http://127.0.0.1:<as>"], "scopes_supported": ["mcp:tools"]}
GET  <as>/.well-known/oauth-authorization-server          200   (RFC 8414)
  {"issuer": ..., "authorization_endpoint": ..., "token_endpoint": ..., "registration_endpoint": ...,
   "jwks_uri": ..., "code_challenge_methods_supported": ["S256"],
   "authorization_response_iss_parameter_supported": true, ...}
```

Three things the host checks along the way, and the SDK's client does all three:

- **The resource is the URL it was given.** Metadata naming another resource is refused, so
  a server can't send the host off to collect a token for something else.
- **The issuer is the one the resource named,** exactly, as a string. Metadata whose
  `issuer` differs is refused (mix-up defence).
- **Scopes start small.** The host asks for what the resource advertises, `mcp:tools`, not
  for everything the issuer knows.

Note that `scopes_supported` lists only the scope every request needs. `mcp:actions` exists,
but nobody asks for it until a tool needs it.

## Registration

The host has no client id yet, so it registers (RFC 7591) at the advertised endpoint:

```json
{"client_name": "E3 lab host", "redirect_uris": ["http://127.0.0.1:33418/callback"],
 "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"]}
```

Two decisions here:

- **A public client.** A host on a laptop can't keep a secret: anything it's given, the
  laptop's user can read. So it has none (`"none"`), and PKCE protects the code instead.
- **Redirect URIs are https or loopback,** checked at registration. The code goes wherever
  the redirect URI says; an attacker who registers `https://evil.example/cb` would get it.
  The consent page shows where the person will be sent, too.

> **Security:** open registration means anyone can register a client with any *name*.
> "Claude Desktop" on the consent page proves nothing. Production MCP deployments increasingly
> use **Client ID Metadata Documents** instead: the client id is an https URL the client
> controls, and the authorization server fetches its metadata from there, so the name is
> bound to a domain. The SDK supports it (`client_metadata_url`) when the server advertises
> `client_id_metadata_document_supported`; it needs https, so this lab uses registration.
> In an enterprise, the third option is pre-registration: only the hosts IT approved exist.

## Authorization and consent

The host sends the person's browser to `/authorize`:

```
GET /authorize?response_type=code&client_id=...&redirect_uri=http://127.0.0.1:33418/callback
   &state=<random>&code_challenge=<S256 of a secret>&code_challenge_method=S256
   &resource=http://127.0.0.1:<port>/mcp&scope=mcp:tools
```

| parameter | stops |
|---|---|
| `code_challenge` (PKCE, S256) | a stolen code: only the host that holds the verifier can redeem it |
| `state` | a forged redirect: the host only accepts the one it started |
| `resource` (RFC 8707) | a token valid everywhere: it names the one server the token is for, and the authorization server refuses a request without it, or for a resource that isn't its to grant |
| `iss` on the way back (RFC 9207) | mix-up: the host checks the code came from the issuer it asked |

The authorization server keeps the request and shows the consent page. The page says, in
words, *which host* (as it registered itself), *which server*, *asking for what*, and
*where you'll be sent back*. The person signs in (the lab picks a name; your identity
provider does this for real) and approves or denies. If their groups don't allow a scope,
approving it fails: `access_denied`, and nothing is issued.

The code that comes back lives 60 seconds and works once. The host redeems it at `/token`
with the PKCE verifier and gets:

```json
{"access_token": "eyJ...", "token_type": "Bearer", "expires_in": 300,
 "scope": "mcp:tools", "refresh_token": "..."}
```

The access token is an Ed25519 JWT, like Unit 7's: `iss`, `sub`, `aud` (the gateway's URL),
`client_id` (the host), `scope`, `groups`, `exp`, `jti`. It lasts five minutes.

## Checking it, with published keys

Unit 7's verifier read the issuer's public key from a file. Here the gateway fetches it from
the `jwks_uri`, picks the key by the token's `kid`, and fetches again when it meets a `kid`
it doesn't know, which is how a key rotation reaches every resource without a redeploy
([`tokens.py`](../examples/E3/tokens.py)). Everything else is Unit 7's rule:

```python
jwt.decode(token, self._key(kid), algorithms=["EdDSA"], audience=self.audience,
           issuer=self.issuer, leeway=5,
           options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]})
```

The algorithm is pinned, the audience is this server's URL, and the signing key never
leaves the authorization server: it is made at start-up and never written down.

## Step-up: asking for more when a tool needs it

priya's token says `mcp:tools`. `isolate_host` needs `mcp:actions` as well. The gateway
could just fail the call, but a tool error tells the host nothing it can act on. OAuth's
answer is an HTTP one: 403, `error="insufficient_scope"`, and the scopes the request needs.

The request has to be stopped *before* the MCP layer, and the gateway knows which tool is
being called without parsing the body: the 2026-07-28 transport puts it in the `Mcp-Name`
header, and the SDK refuses a request whose header and body disagree.
[`gateway.py`](../examples/E3/gateway.py)'s `StepUp` wraps the app:

```python
needs = STEP_UP.get(headers.get("mcp-name", "")) if (
    scope["type"] == "http" and headers.get("mcp-method") == "tools/call") else None
...
if have is not None and needs not in have:
    want = " ".join(dict.fromkeys([*have, needs]))
    header = (f'Bearer error="insufficient_scope", scope="{want}", '
              f'resource_metadata="{self.metadata_url}", ...')
```

The SDK's client sees the 403, sends the person back to consent for `mcp:tools
mcp:actions` (the union: stepping up mustn't drop what it already had), and retries the
call with the new token. For priya, the consent fails: her group can't approve
`mcp:actions`, so the call fails and nothing is isolated. For marc it goes through, and
`whoami` afterwards shows both scopes.

The tool checks the scope again itself. The door is the control the host can see; the
check inside is the one that holds if the door is ever misconfigured, or bypassed: a
2025-era client sends no `Mcp-Name` header, so the door can't tell which tool it calls, and
the tool's own check is what refuses it (as a plain tool error, with no step-up).

> **Try it:** priya's step-up fails at consent. What would happen if the authorization
> server granted the scopes she *may* approve and silently dropped the rest? (The host would
> get a token without `mcp:actions`, retry, and get the same 403. Refusing is clearer, and
> the drill checks for it.)

## On the person's behalf: token exchange

`ticket_status` reads from the ticketing API. The gateway holds priya's token; the obvious
move, forwarding it, is the one the MCP spec forbids (**token passthrough**). Her token's
audience is the gateway: the ticketing API refuses it, as it should, and an API that
accepted it would also accept it from anyone else who had it.

Instead the gateway exchanges it (RFC 8693):

```
POST /token
  grant_type=urn:ietf:params:oauth:grant-type:token-exchange
  client_id=<the gateway>&client_secret=<its secret>
  subject_token=<priya's gateway token>&subject_token_type=urn:ietf:params:oauth:token-type:access_token
  resource=http://127.0.0.1:<tickets>&scope=tickets:read
```

The authorization server checks four things before it issues anything:

- **The caller is a registered resource server,** with a secret. A host can't exchange
  (`unauthorized_client`).
- **The subject token was issued to that caller.** A gateway can only exchange tokens it
  was the audience of.
- **The target is one this gateway may call,** at most these scopes (`invalid_target`
  otherwise).
- **The person may have the scope,** through their groups, the same bound as consent.

The token that comes back is for the ticketing API only, lasts two minutes, and carries
`act: {"sub": "<the gateway's client id>"}`. The API refuses a token without `act`, and its
log, like every answer it gives, names both: *read for* priya, *read by* the gateway. It
also applies its own rule: a restricted ticket isn't there for priya, and looks exactly
like one that doesn't exist.

The gateway describes the exchanged token in its result (audience, actor, scope, lifetime,
`jti`) and never returns it. A token is a credential, not data.

## Refresh, rotation and replay

Five-minute tokens mean refreshing. The host does it without bothering the person: it posts
its refresh token and gets a new access token *and a new refresh token*. The old refresh
token is now spent.

If it's ever presented again, either the host raced itself or someone copied it. OAuth 2.1
says to assume the worst: the authorization server revokes the whole family, every refresh
token descended from that consent, and the person signs in again. The same goes for an
authorization code redeemed twice. A refresh also re-checks the person's groups: someone
moved out of `soc-l3` loses `mcp:actions` at the next refresh, not at the next login.

Access tokens already issued live out their five minutes; a JWT can't be recalled. That's
the case for keeping them short, and for introspection (RFC 7662) where minutes are too long.

## The drill

[`examples/E3/drill.py`](../examples/E3/drill.py) starts the three servers and checks each
step:

| scene | expected |
|---|---|
| discovery | 401 naming the metadata; the metadata naming the resource and issuer; the issuer advertising S256, registration, token exchange and `iss` |
| priya | one consent for `mcp:tools`; a token for the gateway only; the host found it all from one URL |
| on her behalf | an exchanged token: audience the ticketing API, `act` the gateway, `tickets:read`, two minutes; a restricted ticket not there |
| step-up | 403 `insufficient_scope` naming `mcp:actions`; priya can't approve it, nothing isolated |
| marc | the same step-up approved; isolated as marc; his token now has both scopes |
| refresh | a new token without a consent page; the refresh token rotated; the old one replayed refused and the family revoked |
| audiences | the gateway token at the ticketing API, an exchanged token at the gateway, a host exchanging, the gateway acting elsewhere: all refused |
| by hand | a hostile redirect URI, no resource, the ticketing API as resource, sam, a wrong verifier, a code twice, its refresh token after: all refused |
| the log | a few dozen events, and no token in any of them, only `jti`s |

```bash
python examples/E3/drill.py
```

## Lab: full OAuth for remote servers

Open the notebook ([`labs/E3_full_oauth.py`](../labs/E3_full_oauth.py)) with

```bash
marimo edit labs/E3_full_oauth.py
```

It has six parts:

1. Discovery, from nothing: the 401, the resource metadata and the issuer's metadata.
2. Sign in through a host: pick the person and their answer; see the calls, the consent
   pages and every HTTP exchange the host made.
3. The token on the wrong door: four tokens tried where they weren't issued for.
4. Refresh, rotate, replay.
5. By hand: the flow as plain HTTP, each request aimed at a guard.
6. The audit log: the authorization server's decisions and the ticketing API's requests.

## In production

The lab's authorization server is a teaching one: in memory, one process, a name picker for
a login. In production:

- **Use your identity provider.** Entra ID, Okta, Keycloak and the rest do registration,
  consent, refresh rotation and (most of them) token exchange. Your MCP server is a resource
  server: `AuthSettings`, a verifier with the IdP's JWKS, and step-up. That is most of this
  unit's code that you keep.
- **Enterprise-managed authorization.** Where the IdP should decide which hosts may reach
  which MCP servers, without a consent page per server, the MCP extension for identity
  assertion (SEP-990, an ID-JAG presented with the JWT bearer grant) lets the host trade
  the user's sign-in for a token the IdP approved. The SDK supports it
  (`identity_assertion_enabled`).
- **Everything over https,** with the loopback exception for native hosts only.
- **Short access tokens, rotated refresh tokens, a revocation path** you have tested.

## Challenges

1. **Time-bound step-up.** `mcp:actions` is powerful; make it short-lived. Issue tokens
   that carry it for 60 seconds, then refresh down to `mcp:tools`. *Success:* marc isolates
   a host, and a call two minutes later needs a new consent.
2. **Introspection.** Add RFC 7662 introspection to the authorization server and have the
   ticketing API use it instead of the JWKS. *Success:* revoking a family makes its access
   tokens fail at the API at once, and the drill says how many requests that adds.
3. **Least-privilege exchange.** The gateway always asks for `tickets:read`. Add
   `tickets:write`, allowed only to `soc-l3`, and an `update_ticket` tool that exchanges for
   it only when called. *Success:* priya can read but not write; marc's read tokens never
   carry write.

## Recap

- **One URL is enough.** A 401 names the metadata, the metadata names the issuer, the issuer
  names the rest; the host checks each hop names the last.
- **The person consents; their groups bound it.** The consent page says which host, which
  server, what, and where they go back to.
- **PKCE, state, iss, one-time codes:** each closes one way to steal or swap a code.
- **Tokens name one audience** and live minutes. The server checks signature, issuer,
  audience and expiry, with keys it fetches.
- **Step-up is HTTP:** 403 `insufficient_scope` with the scopes needed, before the tool
  runs, and the tool checks again.
- **Never pass a token on.** Exchange it for one to the next service, naming who acts.
- **Rotate refresh tokens;** a replay revokes the family.

## Next

That's the enterprise track. Go back to [E2](E2-the-local-systems-server.md), to
[E1](E1-backend-apis-with-approvals.md), or to the [references](13-references.md).
