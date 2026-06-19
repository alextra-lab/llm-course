"""
Unit 8 - Curation & lifecycle: decide what memory KEEPS, FADES, and gets WRITTEN (OPT-IN).

Units 5-7 wrote and read a graph. But memory that only grows becomes noise. This unit adds
the lifecycle:

  - DECAY: each memory has a retention that falls over time (Ebbinghaus: R = e^(-t/S)), but
    ACCESS reinforces it -- recalling a memory raises its strength S, so reused facts fade
    slowly (MemoryBank, Zhong et al., AAAI 2024; the recency-since-access idea is also in
    Generative Agents, Park et al., UIST 2023).
  - FORGETTING: a pass that drops memories that are BOTH faded AND unimportant. Importance
    protects a fact even when it is old -- the counter-pressure against over-forgetting
    (EMem, Zhou & Han, 2025).
  - A PROMOTION GATE: not every extracted fact deserves durable memory. An LLM judges each
    candidate and NARRATES its decision before writing (the "narrate-and-confirm" pattern).

OPT-IN: the decay/forget spine is pure graph -- set NEO4J_URI (see Unit 5) or it skips. The
promotion gate needs the chat endpoint; if OPENAI_BASE_URL is unset, that part skips and the
rest still runs.

    python agent-memory/examples/08/lifecycle.py
"""

import json
import math
import os
import sys
import uuid
from pathlib import Path

from pydantic import BaseModel

sys.path.append(str(Path(__file__).resolve().parents[1]))               # agent-memory/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common import get_client, MODEL
from common_graph import get_graph


def log_event(session_id, trace_id, step, operation, **fields):
    """One joinable telemetry line per memory op (foundations Section 10 shape). The
    session_id/trace_id/step tuple ties this run together; Unit 10 adds redaction + scope."""
    print(json.dumps({"session_id": session_id, "trace_id": trace_id, "step": step,
                      "operation": operation, **fields}, sort_keys=True), file=sys.stderr)

# A graph with two ranking signals per memory: `importance` (1-10, set once when the memory
# is created) and `strength` (S in the decay curve -- grows each time the memory is accessed).
# `last_access_days` is time since we last saw it. Fixed numbers, not a live clock, so the
# demo is deterministic. Note the spread: the allergy is OLD but IMPORTANT; the small talk is
# recent-ish but trivial; the running club is faded and minor -- unless it gets accessed.
SEED = """
MERGE (a:Entity {name:'Alex'})         ON CREATE SET a.importance=8, a.strength=20, a.last_access_days=1
MERGE (c:Entity {name:'Acme Corp'})    ON CREATE SET c.importance=7, c.strength=10, c.last_access_days=2
MERGE (s:Entity {name:'shellfish'})    ON CREATE SET s.importance=9, s.strength=5,  s.last_access_days=40
MERGE (p:Entity {name:'Portland'})     ON CREATE SET p.importance=4, p.strength=6,  p.last_access_days=30
MERGE (q:Entity {name:'Q3 deadline'})  ON CREATE SET q.importance=6, q.strength=4,  q.last_access_days=3
MERGE (r:Entity {name:'running club'}) ON CREATE SET r.importance=2, r.strength=3,  r.last_access_days=20
MERGE (w:Entity {name:'weather'})      ON CREATE SET w.importance=1, w.strength=2,  w.last_access_days=5
MERGE (a)-[:WORKS_AT]->(c)
MERGE (a)-[:ALLERGIC_TO]->(s)
MERGE (c)-[:LOCATED_IN]->(p)
MERGE (a)-[:HAS_DEADLINE]->(q)
MERGE (a)-[:MEMBER_OF]->(r)
"""

R_MIN, I_MIN = 0.1, 3   # forget only if retention < R_MIN AND importance < I_MIN (both)


def retention(age_days, strength):
    """Ebbinghaus forgetting curve: R = e^(-t/S). Higher strength S -> slower forgetting."""
    return math.exp(-age_days / strength)


def memories(driver):
    records, _, _ = driver.execute_query(
        "MATCH (e:Entity) RETURN e.name AS name, e.importance AS importance, "
        "e.strength AS strength, e.last_access_days AS age ORDER BY name"
    )
    return [dict(r) for r in records]


def record_access(driver, name):
    """Accessing a memory REINFORCES it: strength +1, and the clock resets (last_access = 0).
    This is why a frequently recalled fact does not fade -- access, not just time, drives decay."""
    driver.execute_query(
        "MATCH (e:Entity {name:$name}) "
        "SET e.strength = e.strength + 1, e.last_access_days = 0",
        name=name,
    )


def forget_pass(driver):
    """Drop memories that are BOTH faded (low retention) AND unimportant. Importance alone
    keeps an old fact alive -- the guard against forgetting something that still matters."""
    doomed = [m["name"] for m in memories(driver)
              if retention(m["age"], m["strength"]) < R_MIN and m["importance"] < I_MIN]
    for name in doomed:
        driver.execute_query("MATCH (e:Entity {name:$name}) DETACH DELETE e", name=name)
    return doomed


# --- the promotion gate (needs the chat endpoint) ---------------------------------------
class Verdict(BaseModel):
    keep: bool
    importance: int
    reason: str


GATE_PROMPT = """You curate an agent's long-term memory. Decide if this fact is worth storing
durably for future conversations. Return JSON: "keep" (true/false), "importance" (1-10), and a
short "reason". Keep durable facts (preferences, constraints, identity, commitments); drop
small talk and transient chatter.

Fact: {fact}"""


def promotion_gate(client, fact):
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": GATE_PROMPT.format(fact=fact)}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return Verdict.model_validate_json(r.choices[0].message.content)


def main():
    driver = get_graph()
    if driver is None:
        return   # skip notice already printed -- this unit writes/reads the graph
    session_id, trace_id = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]   # one run; see Section 10

    with driver:
        driver.execute_query(SEED)

        print("retention now (R = e^(-age/strength)):")
        for m in memories(driver):
            r = retention(m["age"], m["strength"])
            faded = "faded" if r < R_MIN else "     "
            print(f"  {m['name']:<14} imp={m['importance']}  age={m['age']:>2}d  R={r:.4f}  {faded}")

        # ACCESS reinforces. The running club is faded (R below the floor) and minor -- it would
        # be forgotten. But the user just mentioned it again, so we record an access; its clock
        # resets and it survives. Access, not importance, saves this one.
        print("\n-> the user mentions the running club again; record_access('running club')")
        record_access(driver, "running club")

        # FORGET pass. Only memories that are BOTH faded AND unimportant go. The allergy is the
        # oldest and most faded fact, but importance=9 protects it (don't over-forget). The
        # running club survives because we just accessed it. Only trivial 'weather' is dropped.
        forgotten = forget_pass(driver)
        survivors = [m["name"] for m in memories(driver)]
        # Telemetry: an automated editor must log what it removed (foundations Section 10).
        log_event(session_id, trace_id, 0, "forget", dropped=forgotten, kept=survivors)
        print(f"\nforget_pass dropped: {forgotten}")
        print("surviving memories:", survivors)
        print("  (shellfish kept by IMPORTANCE despite being the most faded; "
              "running club kept by ACCESS; weather dropped -- faded AND trivial.)")

        # PROMOTION GATE: judge new candidate facts before writing them. Needs the chat endpoint.
        if not os.environ.get("OPENAI_BASE_URL"):
            print("\n(OPENAI_BASE_URL not set -- skipping the promotion gate demo.)")
            return
        client = get_client()
        print("\npromotion gate (narrate-and-confirm before writing):")
        for i, fact in enumerate(["I'm also allergic to peanuts.", "The weather is nice today."]):
            v = promotion_gate(client, fact)
            action = "WRITE" if v.keep else "drop "
            # Telemetry: every gate decision is logged, so what got in is auditable later.
            log_event(session_id, trace_id, 1 + i, "gate", keep=v.keep, importance=v.importance)
            print(f"  [{action}] {fact!r}  importance={v.importance} -- {v.reason}")


if __name__ == "__main__":
    main()
