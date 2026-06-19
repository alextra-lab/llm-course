---
title: 'Observability & Privacy'
linkTitle: '10. Observability & Privacy'
weight: 10
---

**Goal:** treat memory as what it is — durable, personal data — and build the two operational
habits that follow. **Observability:** every memory read and write emits a structured log line
that is *joinable* to the request that caused it, so you can answer "what did the agent remember,
and recall, for this conversation?" **Privacy:** memory is scoped to an owner, recall filters by
who is asking, personal data is redacted before it reaches a log, and every query binds its values
so an attacker cannot rewrite it.

**Where this fits:** Unit 9 measured *how well* memory recalls. This unit is about *what it did and
for whom* — the difference between a demo and something you can run for real users. It reuses the
foundations observability and cost work (§10–§11) and the audit-log pattern (§17), now applied to
the graph.

> **Half of this needs no database.** The telemetry and redaction helpers are pure Python and
> always run. The scoped access-control half uses Neo4j: set `NEO4J_URI` (see Unit 5) or it skips.

---

## Joinable telemetry

When a user asks "why did the assistant think I was vegetarian?", you need to find the moment that
fact entered memory and every time it was recalled. That is only possible if each memory operation
is logged with the **same identifier as the request that triggered it** — a `trace_id` your web
layer already generates. "Joinable" means: given a conversation, you can pull every memory event it
caused, and given a suspicious memory, you can find the request that wrote it.

```python
def log_event(actor, operation, **fields):
    line = {"trace_id": TRACE_ID, "actor": actor, "operation": operation,
            **{k: redact(str(v)) for k, v in fields.items()}}
    print(json.dumps(line, sort_keys=True))
```

One structured line per operation — `write`, `recall`, `read_node` — each carrying `trace_id`
(which request), `actor` (who), and `operation` (what). In production you ship these to your log
store; the join key is `trace_id`. This is the §10–§11 logging discipline, now covering the memory
layer specifically, because memory is the part of the system that *remembers* across requests and
therefore is the hardest to debug after the fact without a trail.

## Redact personal data before it is logged

Logs persist, get copied, and are shipped to other systems. A raw email or phone number in a log
line is a data leak that outlives the conversation. Redact at the boundary — *before* the value is
written — not afterwards:

```python
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d ()-]{7,}\d")


def redact(text):
    text = _EMAIL.sub("<email>", text)
    return _PHONE.sub("<phone>", text)
```

```
{"actor": "alex", "detail": "reach me at <email> or <phone>", "operation": "write", ...}
```

This is deliberately simple — regexes catch the obvious cases, not every name or address. The
lesson is the *placement*: redaction belongs at the point data crosses into a log or an external
system, and `log_event` applies it to every field automatically so no call site can forget. Memory
content itself may need the same treatment before storage, depending on what you are allowed to
keep.

## Scope memory to an owner, and filter on read

Memory about one user must not surface for another. The smallest real model: every node has an
**owner** and a **visibility** (`private` or `shared`), and recall returns only what the asking
actor is allowed to see — their own memory, plus anything shared.

```python
def scoped_recall(driver, actor, query):
    log_event(actor, "recall", query=query)
    records, _, _ = driver.execute_query(
        "MATCH (e:Entity) WHERE e.owner = $actor OR e.visibility = 'shared' "
        "RETURN e.name AS name, e.owner AS owner, e.visibility AS visibility ORDER BY name",
        actor=actor,
    )
    ...
```

Run it as two different actors and the boundary holds:

```
  alex can see: ['Acme office in Portland', 'Alex shellfish allergy']
  sam can see:  ['Acme office in Portland', 'Sam prefers email contact']
```

Alex sees his own private allergy plus the shared office; Sam sees only the shared office and his
own memory — **never** Alex's private allergy. The crucial detail: the scope is enforced **inside
the query**, with a `WHERE` the database applies, not by fetching everything and filtering in
Python. Filtering after the fetch means the private data already left the database and could leak
through a log, an error message, or a bug in the filter. Let the store enforce the boundary.

## Bind the actor, or the boundary is fake

Access control is only as strong as the query that enforces it — and that query now contains a
value (`actor`) supplied by the caller. This is the **Cypher injection** problem from Units 5–6,
and here it is guarding *privacy*. Bind the value; never format it into the string:

```
injection attempt as actor -> sees: ['Acme office in Portland'] (bound param = data, not Cypher)
```

A hostile actor value like `sam' OR e.owner='alex` is passed as a **bound parameter** (`$actor`),
so the database treats it as a literal string to match — it equals no real owner, so it widens
nothing. Had the value been formatted into the query text, that same string would have rewritten
the `WHERE` clause and handed the attacker everyone's memory. **Bind what you can; allow-list what
you cannot** — the rule from Unit 6, now the difference between an access control that works and one
that only looks like it does.

```bash
python work/observe.py
```

*(Reference: [`examples/10/observe.py`](../examples/10/observe.py).)*

---

> **Security:** This unit *is* the security unit, so the summary is the warning. Memory is durable
> personal data plus an automated writer (Unit 8) and an automated reader (Unit 7); every one of
> those is an attack surface. Three controls, all shown above, carry the weight: **scope** enforced
> in the query (not after), **bound parameters** so input cannot rewrite that query, and **redaction
> + joinable logs** so you can both limit and reconstruct what was exposed. Add the obvious
> operational ones too — retention limits, a delete path for a user's memory (the right to be
> forgotten is also a feature), and access to the log store itself.

> **Observe:** This unit is where the through-line you have emitted since Unit 2 gets its name
> and its rules. The joinable `session_id`/`trace_id`/`step` line becomes `log_event` with an
> `actor`, an `operation`, and PII redaction at the boundary — the same record, now safe to keep.
> The loop it closes is the privacy one: *can you reconstruct exactly what one request read and
> wrote, without the log itself leaking what it was protecting?*

## Challenges

1. **Reconstruct a session.** Capture the JSON lines from a run and, using only `trace_id`, list
   every memory operation one request performed. *Success:* you can answer "what did this request
   read and write?" from the logs alone.
2. **Add a visibility level.** Introduce a `team` scope between `private` and `shared`, and extend
   `scoped_recall` so a teammate sees `team` nodes but an outsider does not. *Success:* three actors
   (owner, teammate, stranger) each see a correctly different set.
3. **Break it, then fix it.** Rewrite `scoped_recall` to format the actor into the query string,
   and show the `sam' OR e.owner='alex` value now leaks Alex's private node. Restore the bound
   parameter and show the leak closes. *Success:* you can demonstrate the exact line that is the
   privacy boundary.

## Recap

- Emit **joinable telemetry**: one structured line per memory operation, carrying the request's
  `trace_id`, so memory access can be tied back to the conversation that caused it (§10–§11, §17).
- **Redact PII at the boundary** — before a value reaches a log or external system — and apply it
  uniformly so no call site can forget.
- **Scope memory to an owner** and enforce visibility **inside the query**, not by filtering after
  the fetch (which means the data already left the store).
- **Bind the actor and every value** — the Unit 5–6 injection rule is now your access-control
  boundary. A formatted-in string is a privacy hole.

## Next

**Unit 11 — The Opinionated Default:** the last unit assembles everything — ingestion, the graph,
hybrid retrieval, lifecycle, measurement, and these controls — into a single memory-backed agent,
and delivers the course's decision tree: when this whole machine is worth building, and when you
should *not* build it at all.
