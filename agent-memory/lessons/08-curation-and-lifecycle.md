---
title: 'Curation & Lifecycle'
linkTitle: '8. Curation & Lifecycle'
weight: 8
---

**Goal:** keep the memory graph *healthy* over time. So far it only grows: every turn adds
nodes and edges (Unit 6), and retrieval assumes the right facts are in there (Unit 7). But a
memory that only grows becomes noise — old trivia crowds out what matters, and recall gets
worse, not better. This unit adds the **lifecycle**: a **decay** score that fades memories
over time but is reinforced by **access**, a **forgetting** pass that drops what is both faded
and unimportant, and a **promotion gate** that decides what is even worth storing — narrating
its decision before it writes.

**Where this fits:** Unit 7 met decay as a single `DECAY` constant inside the ranking step.
Here it becomes a real policy with a stored, evolving strength. This is a **decide**-heavy
unit: the build is small, but each piece encodes a tradeoff you must choose deliberately,
because over-forgetting and under-forgetting are both real failures.

> **Optional (opt-in).** The decay and forgetting parts are pure graph work — set `NEO4J_URI`
> (see Unit 5) or the script skips. The promotion gate needs the chat endpoint; if
> `OPENAI_BASE_URL` is unset, that part skips and the rest still runs.

---

## Decay: time, reinforced by access

A memory should not weigh the same forever. The classic model of human forgetting is the
**Ebbinghaus curve**: retention falls exponentially with time. **MemoryBank** (Zhong et al.,
*AAAI* 2024) applies exactly this to an LLM agent, with retention

```
R = e^(-t / S)
```

where `t` is time since the memory was last seen and `S` is its **strength**. The important
detail is what `S` does: every time the memory is **accessed** — recalled, mentioned again —
its strength grows, so it decays more slowly afterwards. Forgetting is driven by *time and
access together*, not time alone. (The same idea — recency measured since last *access*, not
since creation — appears in Generative Agents, which you used for ranking in Unit 7.)

```python
def retention(age_days, strength):
    return math.exp(-age_days / strength)


def record_access(driver, name):
    """Accessing a memory REINFORCES it: strength +1, and the clock resets."""
    driver.execute_query(
        "MATCH (e:Entity {name:$name}) "
        "SET e.strength = e.strength + 1, e.last_access_days = 0",
        name=name,
    )
```

This is the answer to a question the plan left open: **decay by time, or by access?** They are
not rivals — the strength term *is* the access signal folded into the time curve. A fact you
keep using stays fresh; a fact nobody touches fades. You still choose the shape (how fast the
base curve falls, how much each access adds), and that choice is a product decision, not a fact
the literature settles for you. We use a simple `+1` per access here so you can see the
mechanism; tune it to your data.

## Forgetting: faded *and* unimportant

Low retention alone is **not** a reason to delete. The oldest, most faded fact in our demo is a
shellfish allergy — and forgetting it could be dangerous. This is the warning from **EMem**
(Zhou & Han, 2025), a counterpoint to aggressive forgetting: compressing or dropping memory to
save space can throw away exactly what you needed later. The fix is to forget only when *two*
things are true — the memory has faded **and** it was never important:

```python
R_MIN, I_MIN = 0.1, 3   # forget only if retention < R_MIN AND importance < I_MIN (both)


def forget_pass(driver):
    doomed = [m["name"] for m in memories(driver)
              if retention(m["age"], m["strength"]) < R_MIN and m["importance"] < I_MIN]
    for name in doomed:
        driver.execute_query("MATCH (e:Entity {name:$name}) DETACH DELETE e", name=name)
    return doomed
```

Run the example and watch three different outcomes from one pass:

```
retention now (R = e^(-age/strength)):
  shellfish      imp=9  age=40d  R=0.0003  faded
  Portland       imp=4  age=30d  R=0.0067  faded
  running club   imp=2  age=20d  R=0.0013  faded
  weather        imp=1  age= 5d  R=0.0821  faded
  ...
-> the user mentions the running club again; record_access('running club')

forget_pass dropped: ['weather']
```

- **shellfish** is the most faded memory of all, but its importance is 9 — **kept**. Importance
  protects it. (EMem's point, in one line.)
- **running club** was faded and trivial, heading for deletion — but the user mentioned it
  again, so `record_access` reset its clock and it **survived**. Access saved it, not
  importance.
- **weather** is faded *and* trivial, and nobody brought it up again — **dropped**. This is the
  only thing that should go.

One pass, three fates, and each follows from a rule you can defend. That is the whole point of
making the lifecycle explicit instead of either keeping everything (noise) or forgetting on a
timer (data loss).

> **Demotion, not just deletion.** Deleting is the harshest option. A gentler lifecycle
> *demotes* a faded memory — moves it to a cheaper, slower tier instead of removing it — which
> is the tiered-memory idea from **MemGPT** (Unit 2). The same `retention < R_MIN` test that
> deletes here could instead set a `tier` property you exclude from the hot retrieval path. We
> delete for clarity; demotion is the production-friendly variant (a challenge below).

## The promotion gate: not everything deserves memory

Decay decides what *leaves*. The **promotion gate** decides what *enters*. Unit 6 extracted a
triple from every turn, but most turns are not worth remembering forever — "the weather is
nice" is not a durable fact about the user. Writing everything is how a memory fills with noise
in the first place. So put a judge in front of the write:

```python
class Verdict(BaseModel):
    keep: bool
    importance: int
    reason: str


def promotion_gate(client, fact):
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": GATE_PROMPT.format(fact=fact)}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return Verdict.model_validate_json(r.choices[0].message.content)
```

The model returns a structured verdict (validated with Pydantic, foundations §6), and — this is
the part that matters in practice — the agent **narrates** the decision before acting on it:

```
  [WRITE] "I'm also allergic to peanuts."  importance=8 -- significant health-related fact...
  [drop ] 'The weather is nice today.'     importance=2 -- temporary, specific to a single day...
```

This is the **narrate-and-confirm** pattern. The gate does not silently rewrite the user's
memory; it states what it is about to remember and why. In a real assistant that line is shown
to the user, who can correct it ("no, don't store that") — which gives you a memory the user
trusts and an audit trail of every change. Memory that changes behind the user's back is a
memory they cannot trust. The `importance` the gate assigns is the same score the decay and
forgetting rules above depend on, so the gate is also where a memory's whole lifecycle is set.

```bash
python work/lifecycle.py
```

*(Reference: [`examples/08/lifecycle.py`](../examples/08/lifecycle.py).)*

## Consolidation: merging what belongs together

A third force keeps memory healthy: **consolidation** — periodically merging related or
duplicate memories so the graph stays connected instead of fragmenting. This is the entity
resolution problem from Unit 6 ("Acme Corp" vs. "ACME Inc."), now running as a background pass
rather than at write time. **A-MEM** (Xu et al., *NeurIPS* 2025) builds a whole memory system
around this idea: each new note is linked to existing related notes, and adding a memory can
update the ones it connects to — the memory network reorganizes itself as it grows, in the
spirit of a Zettelkasten. You already have the pieces (embedding similarity from Unit 6, graph
edges from Unit 5); consolidation is the scheduled job that applies them to keep the graph
coherent over months.

---

> **Security & trust:** Curation is a powerful, mostly-automated editor of durable state, which
> makes it a target. A hostile turn could try to inflate a planted fact's importance so it
> survives every forget pass, or to demote a real safety fact so it fades. Two defenses carry
> over: the promotion gate's **narrate-and-confirm** keeps a human in the loop for what gets
> written, and treating an old memory's *content* as untrusted (Unit 7) limits the damage a
> surviving bad fact can do. Never let extracted text set its own importance without a gate.

> **Observe:** Curation is an automated editor, so log what it changed: a joinable line
> (foundations §9) per gate decision (`kept`/`dropped` with importance) and per forget or
> consolidate pass (which nodes decayed, faded, or merged). That answers the question that makes
> an automated editor safe to run: *what did the gate let in, and what did the lifecycle quietly
> remove?* — so a forgotten safety fact is visible, not noticed missing later.

## Challenges

1. **Tune the decay to your data.** Change the per-access strength bump and the base curve, then
   find inputs where the same memory is forgotten under one setting and kept under another.
   *Success:* you can state, in one sentence, the forgetting policy your choice implements.
2. **Demote instead of delete.** Replace the `DETACH DELETE` in `forget_pass` with setting a
   `tier='cold'` property, and exclude cold nodes from Unit 7's candidate set. *Success:* a faded
   memory stops appearing in recall but is still in the graph, and you can promote it back by
   accessing it.
3. **Gate then write.** Combine this unit with Unit 6: extract a triple, run each new entity
   through `promotion_gate`, and write only the ones it keeps — storing the returned `importance`
   on the node. *Success:* "the weather is nice" never reaches the graph, while "I'm allergic to
   peanuts" does, with importance set by the gate.

## Recap

- Memory needs a **lifecycle**, not just writes. Decay fades memories (`R = e^(-t/S)`,
  MemoryBank); **access reinforces** them by raising strength — time and access together, not
  time alone.
- **Forget only when faded AND unimportant.** Importance protects an old fact from deletion —
  the guard against over-forgetting (EMem). Prefer **demotion** (a tier, MemGPT) over deletion.
- A **promotion gate** judges what is worth storing so the graph does not fill with noise; it
  **narrates and confirms** so memory changes stay visible to the user and auditable.
- **Consolidation** merges duplicates and links related memories over time (A-MEM), reusing the
  Unit 6 resolution tools as a scheduled pass.

## Next

**Unit 9 — Measure Before You Optimize:** every knob in this unit (decay rate, thresholds, gate
strictness) changes recall — for better or worse. You cannot tune what you cannot measure. Next
you build an evaluation harness — recall@k, precision@k, MRR, nDCG — over your memory, so the
choices in this unit become measured, not guessed.
