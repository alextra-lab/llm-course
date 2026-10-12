---
title: "E1. Backend APIs with Approvals"
linkTitle: "E1. Backend APIs with Approvals"
weight: 14
---

**Goal:** let the capstone gateway carry out the step it proposed, through backend APIs that
fail the way real ones do, without ever doing it twice, doing it unapproved, or doing it at the
wrong time. You'll:
- call an EDR and a ticketing API through a client with timeouts, retries, backoff and a
  circuit breaker, and make the retries safe with idempotency keys;
- put a destructive tool behind three gates in the right order: prechecks, then the person's
  approval, then the call;
- refuse production changes outside the change window, with an emergency route that is
  recorded as one;
- turn a slow action into a task with the Tasks extension, and serve clients that don't
  understand tasks just as well;
- prove every claim against a ledger of what the backends really did.

**Where this fits:** this is the first unit of the optional enterprise track, and it plugs
into Unit 12's gateway. It leans on Unit 7 (the policy and its ticket approval), Unit 8
(backpressure, timeouts and errors a model can act on) and Unit 12 (`propose_containment`).
No model is needed.

## Acting is different

Everything until now read. A read that fails can simply be tried again; the worst outcome is a
slow answer. An action that fails is a question: *was it done?* The EDR may have isolated the
host and lost the reply on the way back; the gateway can't tell that from a request that never
arrived. Try again blindly and a host is isolated twice, a change ticket opened twice, a
comment posted twice. Don't try again and an analyst believes a compromised host is still on
the network, or that it isn't.

Three rules follow, and the rest of this unit is how to keep them:

1. **Every write can be repeated safely.** A retry, a model that calls twice, a person who
   clicks twice: none of them may act twice.
2. **A failure says what is known.** "Nothing was done" and "whether it was done is not
   known; check first" are different answers, and the model has to be told which.
3. **A failing backend is left alone.** Retrying a backend that is down makes it worse, and
   makes every caller wait for timeouts.

## The backends

[`examples/E1/backends.py`](../examples/E1/backends.py) stands in for both APIs: a small
Starlette app, so you can break it on purpose.

| | |
|---|---|
| `POST /edr/hosts/{host}/isolate` | 202 `{action_id, status: "pending"}`; the EDR finishes a few seconds later |
| `POST /edr/hosts/{host}/release` | the undo, same shape |
| `GET /edr/actions/{id}`, `GET /edr/hosts/{host}` | pending or done; isolated or not, and the last finished action |
| `POST /tickets`, `POST /tickets/{id}/comments` | a change record, and its outcome |
| `POST /_faults` | `fail_rate`, `lose_reply`, `lose_first_reply`, `latency_ms`, `down` |
| `GET /_ledger` | every side effect that really happened, in order |

Two properties of real EDR and ticketing APIs are kept on purpose. Isolation is
*asynchronous*: the API accepts the request at once and does the work afterwards, so the
caller has to wait for it. And every POST requires an **`Idempotency-Key`** header: the same
key with the same body returns the first answer again (with `Idempotent-Replay: true`) and does
nothing; the same key with a different body is a 409. Most payment, ticketing and cloud APIs
work this way; where yours doesn't, the gateway has to check state before it acts instead.

The **ledger** is the drill's ground truth. The gateway says what it did; the ledger says what
happened. Every check in this unit compares the two.

## A client that fails well

[`examples/E1/resilience.py`](../examples/E1/resilience.py) is one `Backend` per API, shared
by every request so that its breaker sees every failure:

```python
EDR = Backend("EDR", BACKENDS, breaker=CircuitBreaker(threshold=3, cooldown=COOLDOWN))

started = EDR.request("POST", f"/edr/hosts/{host}/isolate",
                      key=idempotency_key(incident_id, "isolate", host, ticket, last_action, "edr"),
                      json={"ticket": ticket, "change": change_id, "requested_by": who})
```

- **A timeout on every attempt** (two seconds here). Unit 8's rule: no layer waits forever.
- **Retries** on 502, 503, 504, timeouts and connection errors, with exponential backoff,
  jitter, and at least the server's `Retry-After`. Never on a 4xx: that's the backend's
  answer, and asking again gets the same one.
- **A circuit breaker.** Three failures in a row open it; while it's open, requests are refused
  at once without being sent; after the cooldown, one trial request goes through
  (*half-open*): success closes it, failure opens it again. A caller of a dead EDR waits
  milliseconds, not four timeouts, and the EDR gets room to come back.
- **`maybe_done`.** Every failure records whether an attempt may have reached the backend. A
  GET never changes anything; a POST that timed out or got a 503 might have. The error the
  model sees says which: *"Nothing was done"* or *"whether it was done is not known; check
  before trying again"*.

The retry loop is only safe because of the key. Without it, `lose_reply` turns every retry
into a second isolation; with it, a retry is a replay. Lab part 1 shows both.

### What the key names

The key is the *intent*, not the request: the same logical action always gets the same key,
whoever sends it and however often. The gateway derives it from the incident, the action, the
host, the approval ticket and the **host's last finished action**:

```python
base = (incident_id, kind, host, ticket or "-", state["last_action"] or "none")
```

The last part matters. Isolate a host, release it, and isolate it again under the same
incident and ticket: without the host's state in the key, the third request would replay the
first and the host would stay on the network. With it, the key says "isolate this host *from
the state I saw*", like an ETag. A retry of the same call sees the same state (a pending action
hasn't finished) and is a replay; an isolation after a release is a new request.

## Three gates, in order

[`examples/E1/actions_gateway.py`](../examples/E1/actions_gateway.py) is Unit 12's gateway
with three more tools:

| tool | annotations | what it does |
|---|---|---|
| `isolate_host(incident_id, host, proposal_id, emergency=False)` | destructive, idempotent | carries out an `isolate_host` proposal |
| `release_host(incident_id, host)` | destructive, idempotent | the undo |
| `action_status(host)` | read-only | what the EDR reports, for when an outcome is unclear |

Between a model and the EDR there are three gates, and their order is the design:

1. **Grants.** Unit 7's policy, unchanged: `soc-l1` isn't granted the action tools, so they
   aren't in Priya's list and calling one gets "Unknown tool". `soc-l3` has `*`.
2. **Prechecks**, before anyone is asked: the incident is active; the `proposal_id` is the one
   `propose_containment` gives this incident, action and host (so a step is proposed before
   it's done, and a model can't skip the proposal); the caller can see the host in the CMDB;
   and the change window (below). A host that's already isolated is answered from the EDR's
   state, *"already isolated: nothing to do"*, with nobody asked.
3. **Approval.** Unit 7's `writes-need-approval` rule fires because the tools aren't read-only:
   the person confirms with a ticket, through elicitation. Declining runs nothing.

Prechecks come before the approval for a reason: a person asked to approve something the
gateway would then refuse learns to approve without reading. Ask only for what will run.

`with_prechecks` wraps the policy-guarded tool, so neither Unit 7 nor Unit 12 changes; the
ticket the policy checked is kept in a context variable so the action can cite it on the EDR
request and the change record.

### Then the action

1. Open a **change record** in the ticketing system, citing the incident and the approval.
2. Ask the EDR to isolate, with the change id.
3. **Wait** for the EDR to finish, polling `GET /edr/actions/{id}`.
4. **Comment** the outcome on the change record.

Each POST has its own key, so a retry of the whole call repeats none of them. Each failure is
said the way rule 2 asks: the EDR call failing *after* the change record was opened says so,
names the record, and tells the model to call `action_status` before anything else. If only
the last comment fails, the host *was* isolated: the result says so and asks a person to
update the record by hand, rather than reporting a failure that would invite a retry.

## The change window

Production changes happen in a window: `MCP_CHANGE_WINDOW`, 22:00 to 05:00 UTC by default. A
`prod` host outside it is refused at the precheck, with the time, the window and the way out:

> jump-prod-02 is a production host and it's 14:00 UTC, outside the change window (22:00-05:00
> UTC). Wait for the window, or, if this can't wait, call again with emergency=true: it's
> recorded as an emergency change. Nothing was done and nobody was asked.

An intrusion can't wait for the evening, so a **high-severity** incident allows an emergency
change. The model asks for it explicitly, the person still approves it, and the change record's
title says *(emergency)*, so the change board sees every one. A medium-severity incident
can't use the route at all. `MCP_CLOCK` pins "now", so the drill runs the same at any hour.

## Tasks: when the answer takes a while

Isolation takes the EDR seconds here and can take minutes on a real estate. Holding a
`tools/call` open that long ties up the connection, fights proxies' idle timeouts, and gives
the client nothing to show. The **Tasks extension** (`io.modelcontextprotocol/tasks`, an
official extension of the 2026-07-28 spec) lets the server answer at once with a task and let
the client poll it.

The server decides. A client only says, in each request's capabilities, that it understands
tasks; if the call is still running after `MCP_TASK_AFTER` (a second by default), the server
answers:

```json
{"resultType": "task", "taskId": "786512e2-...", "status": "working",
 "statusMessage": "The operation is in progress.", "createdAt": "...", "lastUpdatedAt": "...",
 "ttlMs": 600000, "pollIntervalMs": 500}
```

The client calls `tasks/get` every `pollIntervalMs` until the status is `completed` (the
tool's result is in `result`), `failed` (a JSON-RPC error, in `error`) or `cancelled`. A tool
error (`isError: true`) is still *completed*: the task worked, the tool said no. `tasks/cancel`
asks the server to stop; cancellation is cooperative. There is no `tasks/list`: a task id is
the capability to read it.

The Python SDK has the extension *framework* (`MCPServer(extensions=[...])`,
`Client(extensions=[...])`) but not this extension yet, so
[`examples/E1/tasks_ext.py`](../examples/E1/tasks_ext.py) implements the part the course needs
on it: `Tasks(after_s=...)` intercepts `tools/call` and serves `tasks/get`, `tasks/update` and
`tasks/cancel`; `TasksClient()` claims `resultType: "task"` results and polls them, so
`call_tool` returns the final result as if the call had been synchronous.

Three details that matter:

- **Approvals come first.** The ticket round is answered on the original call, before the
  task exists; the spec advises this, and it means a task never stops halfway to ask a person
  something. A quick answer (a refusal, a request for a ticket) comes back directly.
- **A client without the extension still works.** It never gets a task: the server simply
  waits and answers. Lab step 7 calls `release_host` that way.
- **Tasks live in this process's memory.** One replica is fine; several need the task table in
  shared storage (Unit 9's Valkey) and a task id that any replica can serve.

## The drill

[`examples/E1/drill.py`](../examples/E1/drill.py) starts the backends and the gateway with the
clock at 14:00 UTC, and walks Priya and Marc through what happens to a real containment step:

| # | step | expected |
|---|---|---|
| 1 | Priya lists tools | no action tools |
| 2 | isolate, no emergency | refused at the precheck, nobody asked, nothing in the ledger |
| 3 | a proposal id that isn't this step's | refused |
| 4 | emergency, Marc declines | asked once, nothing in the ledger |
| 5 | emergency, Marc approves with INC-0042 | a task, polled to completed; one change, one isolation, one comment |
| 6 | the same call again | "already isolated", nobody asked, nothing new |
| 7 | release from a client without the extension | waits about two seconds, no task, released |
| 8 | isolate with every first reply lost | retried and replayed; still one new isolation |
| 9 | release with the EDR down; again; again after the cooldown | fails saying so; fails at once (breaker open); works |

and checks the record: every EDR action cites the approval ticket, and every change record has
its outcome.

```bash
python examples/E1/drill.py
```

## Lab: backend APIs with approvals

Open the notebook ([`labs/E1_backend_actions.py`](../labs/E1_backend_actions.py)) with

```bash
marimo edit labs/E1_backend_actions.py
```

It has four parts:

1. A backend that fails: sliders for refused requests and lost replies, ten isolations, and
   the requests, replays, ledger entries and breaker transitions that came of them.
2. When a production host may change: the window over a day.
3. The change drill: every step and every check, and the change records it left.
4. A task on the wire: what the client saw while it waited, and the result it handed back.

## Challenges

1. **Without the key.** Remove the `Idempotency-Key` from `Backend.request` (and the 400 from
   `backends.py`), then run part 1 with lost replies at 0.5. *Success:* the ledger shows hosts
   isolated twice; put the key back and explain in one sentence why the retry loop needed it.
2. **Revoke the account.** Add `disable_account` to the EDR stand-in and a tool for it,
   carrying out Unit 12's `disable_account` proposal. *Success:* it goes through the same three
   gates, a service account needs `emergency=true` outside the window, and the drill has a step
   for it.
3. **Cancel.** Give `TasksClient` a timeout shorter than the EDR's delay. *Success:* the client
   sends `tasks/cancel`, the task ends `cancelled`, and you can say from the ledger whether the
   host was isolated anyway (cancellation is cooperative).

## Recap

- **A failed write is a question.** Say "nothing was done" or "not known; check first", never
  just "failed".
- **Idempotency keys make retries safe.** Name the intent, including the state it was decided
  on; a retry is then a replay.
- **Breakers protect both sides.** Callers fail in milliseconds; the backend gets room to
  recover.
- **Order the gates.** Grants, then prechecks, then the person's approval: ask only for what
  will run.
- **Windows have an emergency route, and it's recorded.** The route is narrow (high severity),
  explicit (`emergency=true`) and visible on the change.
- **Tasks keep slow actions honest.** The server decides; approvals come before the task; a
  client that doesn't understand tasks still gets an answer.
- **Check against the ledger.** What the gateway says happened is a claim; what the backend
  did is the evidence.

## Next

**E2. The Local Systems Server** *(coming next)*: host triage on the permission dial, behind a
sandbox. Or go back to the [references](13-references.md).
