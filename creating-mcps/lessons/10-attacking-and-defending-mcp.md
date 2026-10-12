---
title: "Attacking and Defending MCP"
linkTitle: "10. Attacking and Defending MCP"
weight: 10
---

> **Status: in progress.** This unit's defences, servers and lab are complete. The negative
> cases, one per OWASP category, are placeholders that the course team is writing: each is
> marked *Placeholder* below and reported as `todo` by `examples/10/cases.py`.

**Goal:** know which defence answers each item of the OWASP MCP Top 10, and where in the
stack it lives. You'll:
- map the ten categories onto the gateway you've built since Unit 1;
- add the defences that belong in the **host**: an allowlist with namespaced tools,
  description screening with pinning, an egress rule, redaction, fencing and an audit record;
- add the ones that belong in the **server**: least privilege, parameterised queries, no
  secrets in results, and approvals for anything that changes something;
- see why fencing is a hint to the model and not a control;
- find the false positives a heuristic defence produces on your own honest tools.

**Where this fits:** Units 5, 7 and 8 already built several of these defences without naming
them as such: the blast radius on the permission dial, verified identity and the policy layer,
and the operating envelope. The estate has carried hostile data since Unit 0: an instruction
planted in a syslog line on `jump-prod-02`, and another in the CMDB description of
`build-legacy-01`. Unit 11 records and tests everything this unit decides.

No model is needed for this unit.

## The OWASP MCP Top 10

The [OWASP MCP Top 10](https://owasp.org/www-project-mcp-top-10/) (2025 edition, in beta) names
ten risks specific to MCP. Each maps to a place in the stack this course has built:

| Id | Category | Where it's answered | Defence |
|---|---|---|---|
| MCP01 | Token Mismanagement & Secret Exposure | server and host | no secrets in results; redaction; Unit 7's `hold_only_my_secret` |
| MCP02 | Privilege Escalation via Scope Creep | server and backend | least privilege: act as the caller (Units 5, 7) |
| MCP03 | Tool Poisoning | host | vetting: screen descriptions when they're approved |
| MCP04 | Software Supply Chain Attacks & Dependency Tampering | host and install | pinning: approved definitions, pinned versions (Unit 5's `osmcp` pin) |
| MCP05 | Command Injection & Execution | server | parameters, never string-built queries; Unit 5's guards |
| MCP06 | Intent Flow Subversion | server and host | approvals for every change; fencing as a hint |
| MCP07 | Insufficient Authentication & Authorization | server | verified identity and policy (Unit 7) |
| MCP08 | Lack of Audit and Telemetry | host and server | a record of every decision (this unit, Unit 11) |
| MCP09 | Shadow MCP Servers | host | an allowlist of servers and tools, namespaced |
| MCP10 | Context Injection & Over-Sharing | host and server | egress rule; least privilege |

Two patterns stand out. First, most items have a **control** (something that refuses, whatever
the model does) and sometimes a **hint** (something that makes a good outcome likelier). Build
the controls; treat the hints as extras. Second, the items split between the server and the
host. A server can't see what other servers a host runs, and a host can't see what a backend
role allows, so each must do its own part.

## The servers for this unit

`examples/10/` holds three servers, each with a switch:

- **`cmdb_naive_server.py`**: Unit 5's task tools plus three that real servers grow: a
  contact lookup with no ticket, a lookup by owner name with the name pasted into the SQL,
  and a health check that returns its connection string. `MCP_HARDENED=1` is the same server
  after a review: a query parameter, and a health check that says nothing about how it logs in.
- **`actions_server.py`**: SOC actions (change an incident's status, post to a channel),
  recorded to a file instead of applied. Only a responder may change a status, and
  `MCP_APPROVALS=1` makes every action ask the person first.
- **`third_party_server.py`**: an IP-reputation service, the kind a team installs from a
  registry. It has an honest mode and three hostile modes the defences are measured against;
  its docstring says what each one does.

## The guarded host

`examples/10/guarded_host.py` starts servers the way Unit 2's host does, then applies the
defences you switch on. The host-side ones:

- **Allowlist.** Only approved servers start, and only their approved tools reach the model,
  each namespaced as `server__tool`. Without namespacing, two servers offering the same tool
  name leave the choice to whichever loaded last.
- **Vetting and pinning.** When a person approves the servers, the host screens every tool
  description for phrases that tell a model what to do (`screen()`), refuses the ones that
  match, and pins a fingerprint of the rest: name, description and input schema. Each time it
  re-reads a tool list, a definition that no longer matches its pin is switched off until
  someone approves it again.
- **Egress.** Every value a trusted server returns is remembered. A call to an untrusted
  server whose arguments contain one of those values is refused, unless the person typed the
  value themselves (an IP they asked about may go to a reputation service; an owner's phone
  number may not).
- **Redaction.** Results are scrubbed of credential-shaped text (URLs with passwords,
  `password=...`, API keys, JWTs, private keys) before the model reads them.
- **Fencing.** Each result is wrapped as `<untrusted-data source="server.tool">`, and the
  system prompt says never to follow instructions inside it.
- **Audit.** Every decision, allowed or refused, is a record: which server, which tool, which
  defence, why.

Try it on everyday requests:

```bash
python examples/10/tour.py --defences ""
python examples/10/tour.py
```

The first run shows the tool table as Unit 2's host would merge it, with bare names. The
second shows namespaced tools, fenced results, a confirmation the person answered, and the
audit records.

> **Security:** the description screen is a heuristic, and heuristics misfire. Its first
> version in this course refused two of Unit 4's own SIEM tools, because "use it first" and
> "call again with the same arguments" read like instructions. They are: honest instructions
> about the tool itself. The screen now looks only for phrases no honest description needs:
> instructions about *other* tools, about earlier instructions, or about what not to tell the
> user. Run any screen over your own servers before trusting it on someone else's.

## Controls and hints

Fencing is the defence most often proposed against instructions hidden in results, and the
weakest. It tells the model which text is data; whether the model acts on that is up to the
model, on every call. The controls in this unit don't depend on the model:

- **Approvals** make a change happen only when the person confirms the exact change. If an
  agent is talked into closing an incident, the person sees "Set INC-0042 to false_positive?"
  and says no.
- **Least privilege** limits what any call can do to what the person may do. An analyst's
  agent can't read what the analyst can't.
- **Egress** limits where data can go, whoever asks for it to move.

Keep fencing: it lowers how often a model is misled, and costs nothing. Don't count it.

## Server-side defences

These are the server changes, with where each first appeared:

- **Act as the caller.** A server that runs as one shared account gives every caller that
  account's reach (Unit 5 showed the blast radius; Unit 7 made identity verified).
- **Parameters.** A value the model sends is data, never part of the statement. Compare
  `find_assets_by_owner` with `MCP_HARDENED` off and on.
- **No secrets in results.** A health check doesn't need a connection string. Unit 7's
  `hold_only_my_secret` keeps a server from even holding the other personas' passwords.
- **Approvals for changes.** Unit 2's `open_ticket` and Unit 7's ticket for personal data are
  the same pattern as `actions_server.py`.

## The negative cases

`examples/10/cases.py` registers one case per OWASP category, with the defence expected to
answer it. Each case runs the unit's servers with a set of defences and reports whether the
defence held.

*Placeholder: the course team is writing the ten cases. Until then `cases.py` reports each as
`todo`, and the lab and CI pass without them.*

### Writing a case

A case is an async function `run(defences) -> {"blocked": bool, "detail": str}` assigned to
its entry in `CASES`. It should:

1. start only the servers it needs, through `GuardedHost`, with the given defences;
2. make the call or calls that exercise its category;
3. decide from evidence outside the model (the actions server's outbox, the third-party
   server's log, the audit records, a result's content) whether the defence held;
4. leave nothing behind: use a temporary directory for the outbox and logs.

A case should hold with its own defence on and fail with every defence off. That pair is what
CI checks once the case exists.

| Id | Case | Status |
|---|---|---|
| MCP01 | secrets in results | *Placeholder* |
| MCP02 | a shared account's reach | *Placeholder* |
| MCP03 | instructions in a tool description | *Placeholder* |
| MCP04 | a definition that changes after approval | *Placeholder* |
| MCP05 | a value pasted into a query | *Placeholder* |
| MCP06 | a change requested by text in a result | *Placeholder* |
| MCP07 | a persona that isn't verified | *Placeholder* |
| MCP08 | a decision with no record | *Placeholder* |
| MCP09 | two servers offering one tool name | *Placeholder* |
| MCP10 | internal data sent to a third party | *Placeholder* |

> **Observe:** the host's audit records are the start of Unit 11's audit log. Each refusal
> names the defence that made it, so "how often does vetting refuse, and on which servers?"
> is a query, not a guess.

> **Limits:** the egress rule matches exact values of six characters or more. Data that's
> been reworded, summarised or re-encoded gets past it. It's a control for accidents and
> obvious misuse, not a data-loss-prevention system.

## Lab: the guarded host

Open the notebook ([`labs/10_guarded_host.py`](../labs/10_guarded_host.py)) with marimo:

```bash
marimo edit labs/10_guarded_host.py
```

It has three parts:

1. The OWASP map: each category, where it's answered, and the state of its case.
2. The guarded host with a switch per defence: the tool table the model sees, everyday
   calls, and the audit records.
3. The description screen, run over every tool in the estate.

## Challenges

1. **Approve again.** Add `GuardedHost.reapprove(server, tool)`, which re-pins a definition
   that changed, after showing the person the old and new descriptions. *Success:* a tool
   switched off by pinning is back in the table after `reapprove`, and the audit records
   who approved it.
2. **Egress by field.** Replace "any value of six characters or more" with a list of fields
   that may never leave (e-mail, phone, `api_token`). *Success:* an owner's name may go to the
   third-party server, their phone number may not, and the tour still passes.
3. **Write a case.** Implement one placeholder in `cases.py`, following "Writing a case".
   *Success:* it reports `blocked` with its defence on and `got through` with none.

## Recap

- **Ten categories, two places.** Each OWASP MCP item is answered in the server, the host, or
  both, and neither can do the other's part.
- **Controls before hints.** Approvals, least privilege and egress hold whatever the model
  does. Fencing helps the model; it doesn't stop anything.
- **Approve what you saw.** Screen descriptions when they're approved, pin them, and switch
  off anything that changes.
- **Namespaced tools from approved servers only.** No server should be able to take over
  another's tool name.
- **Fix it at the source, scrub it at the boundary.** Parameterised queries and no secrets in
  results, then redaction for what slips through.
- **Every decision is a record.** That's MCP08, and Unit 11's starting point.

## Next

**[Unit 11: Observability, Audit and Testing](11-observability-audit-and-testing.md)**: one
trace across the boundary, telemetry in OpenSearch, an audit log that fails closed,
per-caller metrics, and contract tests and evals that run in CI.
