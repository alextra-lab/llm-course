---
title: "The Measured Default (Capstone)"
linkTitle: "12. The Measured Default (Capstone)"
weight: 12
---

**Goal:** put everything the course built behind one endpoint, use it to work a real incident
from alert to containment proposal, and decide, with evidence, whether it's ready for
production. You'll:
- compose the SOC / IT-ops gateway from the CMDB and SIEM tools, the policy, the envelope and
  the record, without changing any of them;
- add one tool, `propose_containment`, that proposes a step for a person to approve and
  executes nothing;
- triage the planted incident end to end, the measured way, and follow it through the audit
  log and the telemetry;
- give the same gateway to the model and compare its path with the scripted one;
- run a production-readiness review that reports what passes, what's a caveat, what fails and
  what nobody has proven yet.

**Where this fits:** every earlier unit left one layer and one check behind. Units 3 and 4
made the tools; Unit 5 placed them on the permission dial; Unit 7 gave them a caller and a
policy; Units 8 and 9 an envelope that holds under load; Unit 10 the threat model; Unit 11 the
record and the tests. This unit is where they meet, and the review is where each check earns
its keep.

The model is needed for one part of the lab; everything else runs without it.

## The measured default

The course's thesis, in one line: **an agent's access to your systems should be measured by
default**. Measured in both senses:

- **Restrained.** Read before acting. Count before reading. Propose; don't execute. Ask a
  person when the step touches a person or a production system.
- **Quantified.** Every call has a caller, a verdict, a cost and a trace. Every claim about the
  server ("only L3 can propose", "every call is audited", "it answers in 50 ms") is a check
  that can fail.

The capstone gateway is that default, assembled. Nothing in it is new except one tool and one
prompt; what's new is that it's reviewed as a whole.

## One gateway

`examples/12/soc_gateway.py` builds one MCP server from the parts the course already has:

```text
                         the SOC gateway (urn:creating-mcps:soc)
[SDK: OpenTelemetry, request state] -> joinable_log -> audit -> identity -> envelope
      -> tool, with the policy inside:
         CMDB   get_asset, find_assets, count_assets, service_dependencies,
                hosts_with_vulnerability, get_contact                       (Units 3, 5)
         SIEM   list_incidents, get_incident, event_histogram, top_values,
                search_events                                               (Unit 4)
         new    propose_containment                                         (this unit)
      prompt: triage_alert(incident_id)
```

It doesn't fork Unit 9's scaled server or Unit 11's observed one. It teaches the gateway one
more kind:

```python
def install(gateway) -> None:
    """Teach gateway.build the "soc" kind, so Units 9 and 11 can serve it unchanged."""
    original = gateway.build
    if getattr(original, "soc", False):
        return

    def soc_build(kind, resource):
        return build(gateway, resource) if kind == "soc" else original(kind, resource)

    soc_build.soc = True
    gateway.build = soc_build
```

So `MCP_KIND=soc` gets the replicas, the shared limits and the cache from Unit 9 and the
telemetry and audit from Unit 11, with the same settings:

```bash
python examples/12/soc_gateway.py --port 8740                 # HTTP, a token on every request
MCP_WORKERS=2 python examples/12/soc_gateway.py --port 8740   # two replicas, pools checked
MCP_ACCESS_TOKEN=... python examples/12/soc_gateway.py --stdio   # one caller, for a host
```

The policy needs no change either. `propose_containment` is not in the L1 grant, so an L1
analyst doesn't see it, and a contractor, with no grant, sees nothing at all.

## Propose, don't execute

Unit 5's dial ran from *read* to *act*. This gateway stops one notch past reading: it
**proposes**. `propose_containment(incident_id, action, target, reason, evidence)` checks the
proposal is sound, then returns what would be done, what it would affect, who must approve it
and how to undo it:

- the incident exists and is open or in progress;
- the target is the right kind of thing: a hostname the caller can see for `isolate_host`, an
  account for `disable_account`, an address *outside your own network* for `block_ip`
  (blocking an internal one would cut off your own systems; the tool says so and suggests
  isolating the host instead);
- the proposal cites one to ten findings as evidence;
- every lookup runs **as the caller**, through the same CMDB tools, so row-level security
  decides what Marc can propose for, just as it decides what he can read.

The approval requirements come from the CMDB, not from the model: the owning team always, a
change window for a production host, the data owner's sign-off for a restricted one. The
proposal id is a hash of incident, action and target, so asking twice gives the same id
instead of two proposals.

```json
{
  "proposal_id": "PRP-21d20d3e",
  "status": "proposed: not executed",
  "action": "isolate_host",
  "target": "jump-prod-02",
  "evidence": ["auth: svc_backup logged in on jump-prod-02 from 203.0.113.77 at 02:31:12Z ...",
               "edr: 6 events on jump-prod-02 as svc_backup after that login",
               "auth: svc_backup then logged in on db-prod-01"],
  "host": {"environment": "prod", "classification": "confidential",
           "service": "remote-access", "owner_team": "Remote Access"},
  "blast_radius": ["remote-access on jump-prod-02"],
  "approver": "Remote Access (Camille Michel)",
  "requires": ["approval by the host's owning team", "a ticket: INC-0042",
               "a change window, or an emergency change (production host)"],
  "rollback": "release jump-prod-02 from isolation in the EDR console"
}
```

The tool is still annotated read-only, and it is: it reads the SIEM and the CMDB and writes
nothing. Carrying the proposal out, behind an approval, a change window and an idempotency
key, is the enterprise track's E1.

> **Security:** a proposal is the model's words with the server's facts around them. The
> `reason` and `evidence` are the model's; the host, the approver, the requirements and the
> rollback are the server's. A person approving it should be able to tell which is which, and
> the response keeps them in separate fields for that reason (MCP06, *Intent Flow
> Subversion*).

## Triage INC-0042

`examples/12/triage.py` works the planted incident as Marc, over HTTP, with the whole stack on.
The `triage_alert` prompt describes the same path for a model.

| Step | Tool | Why |
|---|---|---|
| 1 | `list_incidents` | what's open and high |
| 2 | `get_incident` | the host, account, address and a suggested window |
| 3 | `event_histogram` | **count**: when did the failures happen? |
| 4 | `top_values` | **count**: from where? |
| 5 | `search_events` | **read**, the narrow window: did that address get in? |
| 6 | `search_events` | where else did the account log in? *(asks Marc for a ticket)* |
| 7 | `get_asset` | whose host is it, what runs on it |
| 8 | `service_dependencies` | what else breaks if it's isolated |
| 9 | `search_events` | EDR: what did the account do on the host? *(asks)* |
| 10 | `propose_containment` | isolate `jump-prod-02` |
| 11 | `propose_containment` | disable `svc_backup` |

```bash
python examples/12/triage.py
```

```text
 3 event_histogram     47.4 ms   1924 B  1500 failures, peak 300 at ...T02:05:00Z
 4 top_values          28.5 ms    298 B  203.0.113.77 (1500)
 5 search_events       34.9 ms    496 B  ...T02:31:12Z svc_backup on jump-prod-02
 6 search_events       44.8 ms    776 B  (asked)  02:31 jump-prod-02; 02:46 db-prod-01
 ...
  [PASS] found the attacker address: 203.0.113.77 vs 203.0.113.77
  [PASS] found the next host: db-prod-01 vs db-prod-01
  [PASS] every step is in the audit log: 14 rows for 11 steps; the extra rounds:
         6: approve/input_required, 9: approve/input_required, 11: busy/tool_error
```

The checks compare the findings with facts computed from the estate (Unit 4's `facts`), not
with strings typed into the script.

> **Observe:** 14 audit rows for 11 steps, and every extra row is explained. A person lookup
> is two rounds of one call: the first returns `input_required` and asks for a ticket, the
> second carries it. And step 11 was turned away once: the script ran faster than Marc's rate
> limit, got `busy: ... Retry the same call after 1 s`, waited, and retried with the same step
> number. The audit log shows both rounds of each. That's what "every call" means.

The last finding matters. The attacker moved from `jump-prod-02` to `db-prod-01`, so isolating
the jump host alone doesn't contain them, which is why the second proposal disables the
account. A proposal to isolate `db-prod-01` would need the data owner's sign-off as well: it's
restricted.

## The model's path

The scripted path is the gold. The model gets the same gateway over stdio, as Marc, with the
`triage_alert` prompt, and each run records its path, whether it **counted before it read**,
whether it **proposed** containment, how many facts its answer got right, and what it cost in
calls, bytes and seconds:

```bash
python examples/12/triage.py --model --repeats 3
```

Read the runs the way Unit 4 taught: not "did it get the answer once", but how often, by what
path, at what cost. A model that jumps straight to `search_events` over the whole range may
still find the address; it pays for it in bytes and in what it reads that it didn't need to.

## The production-readiness review

`examples/12/readiness.py` runs every unit's check against this gateway and prints one
scorecard:

```bash
python examples/12/readiness.py --markdown review.md
```

```text
  [PASS] catalog   catalog 125c23bacc83: agrees with the estate for l1_analyst, l3_responder, auditor
  [PASS] policy    14 cases decided as written
  [PASS] contract  matches the reviewed snapshots: soc-l1 10 tools, soc-l3 12 tools, contractors 0 tools
  [PASS] dial      12 tools, all read-only; 2 proposals, none executed; L1 and contractors can't propose
  [PASS] identity  no token: 401; a token for another server: 401; a forged token: 401; sam: no tools ...
  [PASS] envelope  2 replicas x 10 connections fit the gateway login's limit
  [PASS] envelope  25 calls at once from one caller: 15 told to wait (busy: ...)
  [PASS] triage    INC-0042 in 11 steps: 203.0.113.77 as svc_backup at 02:31 UTC, then db-prod-01
  [PASS] audit     14 rows for 11 steps; the extra rounds: ...
  [PASS] latency   p95 31 ms per call (budget 500), 415 ms for the whole triage (budget 3000)
  [WARN] eval      golden set consistent; tool selection NOT measured (no model ...)
  [TODO] owasp     0 of 10 cases written and blocked; still placeholders: MCP01, ..., MCP10
verdict: not signed off: the todo lines are unproven
```

| Area | The claim | The evidence | Unit |
|---|---|---|---|
| catalog | it promises nothing the backends refuse | `catalog.py --check` for every persona | 3 |
| policy | the rules decide as written | the table of cases | 7 |
| contract | each group's tool list is the reviewed one | snapshots in `examples/12/contracts/` | 11 |
| dial | it reads and proposes, nothing more | annotations, grants, the proposals' status | 5 |
| identity | strangers stay out | no token, wrong audience, forged, no grant | 7 |
| envelope | it fits, and it pushes back | the pool budget; a burst past the rate limit | 8, 9 |
| triage | it does the job | the planted incident, against the estate's facts | 4 |
| audit | every call is recorded | the triage's trace in `audit.tool_calls` | 11 |
| latency | it's fast enough | the server's own time, from telemetry | 11 |
| eval | the model still picks the right tools | the eval gate, with floors | 6, 11 |
| owasp | the Top 10 are answered | Unit 10's cases | 10 |

Four results, and the difference between them is the point:

- **pass**: the evidence was collected and it holds.
- **warn**: it works, with a caveat someone has to read and accept, like an eval that couldn't
  run without a model.
- **fail**: the evidence contradicts the claim. The review exits 1, so CI can gate on it.
- **todo**: nobody has collected the evidence. Unit 10's negative cases are placeholders, so
  the OWASP line says *todo*, never *pass*. A review that turned "not tested" into green would
  be worse than no review.

The verdict follows from the worst line: any fail is "not ready", any todo is "not signed
off", warnings alone are "ready, once the warnings are read and accepted".

> **Limits:** latency is the **server's** time, from its telemetry, not the client's. The
> client waited a second at step 11 because of the rate limit; that's the caller's time, and
> counting it would make the gateway look slow for doing its job. The budgets (500 ms a call at
> p95, 3 s a triage) are this course's; set yours from what your users wait for.

The contract snapshots are reviewed like code. When a change to a tool is intended:

```bash
UPDATE_CONTRACTS=1 python examples/12/readiness.py
git diff examples/12/contracts/
```

## Lab: the measured default

Open the notebook ([`labs/12_measured_default.py`](../labs/12_measured_default.py)) with
marimo:

```bash
marimo edit labs/12_measured_default.py
```

It has five parts:

1. One gateway, three offers: the tools each group sees.
2. Triage INC-0042: the steps, the checks, and the two proposals as a person would see them.
3. The model's triage: its path, its facts and its cost, next to the scripted one.
4. The production-readiness review: the scorecard, the verdict and the review as Markdown.
5. The dashboard: Unit 11's panels over what you just did.

## Challenges

1. **Contain the pivot.** Add a third proposal to the scripted triage: isolate `db-prod-01`.
   *Success:* its `requires` includes the data owner's sign-off, and its blast radius lists
   what depends on `customer-db`.
2. **A stricter dial.** Add a policy rule so `propose_containment` needs a ticket, like a person
   lookup. *Success:* the triage asks four times, the audit check still passes with the new
   rounds explained, and `policy.py --test` has a case for it.
3. **Write a case.** Replace one OWASP placeholder in `examples/10/cases.py` with a real case,
   following Unit 10's "Writing a case". *Success:* the review's OWASP line counts it as
   written and blocked, and still says *todo* for the rest.

## Recap

- **The measured default.** Read before acting, count before reading, propose rather than
  execute, and measure every call.
- **Compose, don't fork.** The capstone is the course's layers in their order, plus one tool;
  teaching the gateway a new kind let Units 9 and 11 serve it unchanged.
- **A proposal is not an action.** It's checked as the caller, carries the server's facts
  apart from the model's words, and names its approver and its undo.
- **The record explains itself.** Approval rounds and retries are extra audit rows, each with
  its reason.
- **A review is a set of claims with evidence.** Pass, warn, fail, and an honest todo for what
  nobody has proven yet.

## Next

**Unit 13: References** *(coming next)*: the spec and SDK docs, the OWASP MCP Top 10, Postgres
and OpenSearch, an Elasticsearch porting note, and the established MCP courses as preparation
and complements.
