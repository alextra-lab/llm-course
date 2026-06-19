"""
Unit 10 - Observability & privacy: see what memory did, and for whom (OPT-IN: Neo4j).

A memory store is durable state about people. Two operational duties follow, and neither is
optional in production:

  - OBSERVABILITY: every memory read/write emits a structured, JOINABLE log line carrying the
    same trace_id as the request that caused it -- so you can reconstruct exactly what an agent
    remembered or recalled for any conversation (foundations Sections 10-11, 17 audit log).
  - PRIVACY: memory is SCOPED to an owner with a visibility, recall filters by the asking actor
    (so one user can't read another's private memory), PII is redacted before it's logged, and
    every query BINDS its values so a hostile scope can't rewrite the Cypher (Units 5-6).

The telemetry + redaction half is pure Python and always runs. The scoped access-control half
needs Neo4j: set NEO4J_URI (see Unit 5) or it skips cleanly.

    python agent-memory/examples/10/observe.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # agent-memory/examples
from common_graph import get_graph

# A fixed trace id stands in for the per-request id your web layer already generates. Everything
# this request touches is logged under it, so memory access JOINS to the conversation that drove it.
TRACE_ID = "req-7f3a"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d ()-]{7,}\d")


def redact(text):
    """Mask obvious PII before it reaches a log. Logs persist and get shipped to other systems;
    raw emails/phones in them are a leak. Redact at the boundary, not after."""
    text = _EMAIL.sub("<email>", text)
    return _PHONE.sub("<phone>", text)


def log_event(actor, operation, **fields):
    """Emit one joinable, structured telemetry line (you'd ship this to your log store).
    trace_id ties it to the request; actor says who; operation says what. Values are redacted."""
    line = {"trace_id": TRACE_ID, "actor": actor, "operation": operation,
            **{k: redact(str(v)) for k, v in fields.items()}}
    print(json.dumps(line, sort_keys=True))


# --- OPT-IN: scoped memory + access control over the graph ----------------------------------
# Each memory node has an OWNER and a VISIBILITY ('private' or 'shared'). This is the smallest
# real access model: you can see your own memory, plus anything explicitly shared.
SEED = """
MERGE (a:Entity {name:'Alex shellfish allergy'})  ON CREATE SET a.owner='alex', a.visibility='private'
MERGE (b:Entity {name:'Acme office in Portland'})  ON CREATE SET b.owner='alex', b.visibility='shared'
MERGE (c:Entity {name:'Sam prefers email contact'}) ON CREATE SET c.owner='sam',  c.visibility='private'
"""


def scoped_recall(driver, actor, query):
    """Return only memory the actor may see: their own, or anything shared. The actor and query
    are BOUND ($actor) -- never formatted into the string -- so a hostile actor value like
    "x' OR 1=1 //" is data, not Cypher (the Unit 5-6 rule, now guarding access control)."""
    log_event(actor, "recall", query=query)
    records, _, _ = driver.execute_query(
        "MATCH (e:Entity) WHERE e.owner = $actor OR e.visibility = 'shared' "
        "RETURN e.name AS name, e.owner AS owner, e.visibility AS visibility ORDER BY name",
        actor=actor,
    )
    for r in records:
        log_event(actor, "read_node", node=r["name"], owner=r["owner"])
    return [r["name"] for r in records]


def main():
    # The pure half always runs: structured, redacted, joinable telemetry.
    print("telemetry (one joinable line per memory operation):")
    log_event("alex", "write", node="contact", detail="reach me at alex@example.com or 555-123-4567")
    print("  ^ note the email and phone are redacted before the line is emitted.\n")

    driver = get_graph()
    if driver is None:
        return   # the telemetry/redaction demo above stands on its own; the graph half is optional

    with driver:
        driver.execute_query(SEED)

        print("access control -- same query, two different actors:")
        alex_sees = scoped_recall(driver, "alex", "what do you know?")
        print("  alex can see:", alex_sees)
        sam_sees = scoped_recall(driver, "sam", "what do you know?")
        print("  sam can see: ", sam_sees)
        print("\nAlex sees his own private allergy + the shared office; Sam sees only the shared "
              "office, never Alex's private memory. Scope is enforced in the query, not after.")

        # A hostile actor value can't widen the scope: it's bound, so it matches nothing.
        sneaky = scoped_recall(driver, "sam' OR e.owner='alex", "injection attempt")
        print("\ninjection attempt as actor -> sees:", sneaky, "(bound param = data, not Cypher)")


if __name__ == "__main__":
    main()
