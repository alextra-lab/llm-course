"""
Unit 11 - The opinionated default: a memory-backed agent wiring every prior unit together.

This is the whole course in one loop. For each user turn the agent:
  1. REMEMBERS  -- extract (entity, relation) triples + an importance score (Unit 6), gate out
                   trivia (Unit 8), and MERGE into the graph with an embedding (Units 5-6).
  2. RECALLS    -- hybrid rank of entities by relevance/importance, traverse, assemble context
                   (Unit 7).
  3. RESPONDS   -- answer with the recalled memory in the prompt.

It needs the chat endpoint AND Neo4j (it is a graph-backed agent). Set NEO4J_URI (see Unit 5)
or it skips; if OPENAI_BASE_URL is unset it also skips. EMBED_MODEL is optional -- without it,
recall ranks by importance instead of relevance.

    python agent-memory/examples/11/agent.py
"""

import re
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.append(str(Path(__file__).resolve().parents[1]))               # agent-memory/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common import get_client, get_embed_client, MODEL, EMBED_MODEL
from common_graph import get_graph

IMPORTANCE_GATE = 4   # entities the model rates below this are trivia -- not stored (Unit 8)


class Entity(BaseModel):
    name: str
    type: str
    importance: int


class Relation(BaseModel):
    subject: str
    predicate: str
    object: str


class Extraction(BaseModel):
    entities: list[Entity]
    relations: list[Relation]


EXTRACT_PROMPT = """Extract durable facts about the user from the message as JSON.
- "entities": each has "name" (canonical), "type", and "importance" (1-10: how much it matters
  for future conversations -- an allergy is 9, a passing comment about the weather is 1).
- "relations": each has "subject", "predicate" (UPPER_SNAKE_CASE), "object", using entity names.
Return ONLY JSON with keys "entities" and "relations".

Message: {turn}"""


def safe_rel(predicate):
    rel = re.sub(r"[^A-Z_]", "", predicate.upper().replace(" ", "_")).strip("_")
    if not rel:
        raise ValueError(f"unusable relation type: {predicate!r}")
    return rel


def remember(driver, client, embed, turn):
    """Extract -> gate -> write. The whole write side of memory, in one function."""
    r = client.chat.completions.create(
        model=MODEL, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "user", "content": EXTRACT_PROMPT.format(turn=turn)}])
    extraction = Extraction.model_validate_json(r.choices[0].message.content)

    kept = [e for e in extraction.entities if e.importance >= IMPORTANCE_GATE]
    dropped = [e.name for e in extraction.entities if e.importance < IMPORTANCE_GATE]
    if dropped:
        print(f"   gate dropped (low importance): {dropped}")
    keep_names = {e.name for e in kept}
    for e in kept:
        driver.execute_query(
            "MERGE (e:Entity {name:$name}) ON CREATE SET e.type=$type SET e.importance=$imp",
            name=e.name, type=e.type, imp=e.importance)
        if embed is not None:
            driver.execute_query("MATCH (e:Entity {name:$name}) SET e.embedding=$v",
                                 name=e.name, v=embed(e.name))
    for rel in extraction.relations:
        if rel.subject in keep_names and rel.object in keep_names:
            driver.execute_query(
                f"MATCH (a:Entity {{name:$s}}), (b:Entity {{name:$o}}) MERGE (a)-[:{safe_rel(rel.predicate)}]->(b)",
                s=rel.subject, o=rel.object)
    print(f"   remembered: {sorted(keep_names)}")


def cosine(a, b):
    import numpy as np
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def recall(driver, embed, query, k=3):
    """Hybrid rank (relevance if we have embeddings, else importance), traverse, assemble."""
    rows = [dict(r) for r in driver.execute_query(
        "MATCH (e:Entity) RETURN e.name AS name, e.importance AS importance, e.embedding AS embedding"
    )[0]]
    if embed is not None and all(r["embedding"] for r in rows):
        qv = embed(query)
        rows.sort(key=lambda r: cosine(qv, r["embedding"]), reverse=True)
    else:
        rows.sort(key=lambda r: r["importance"], reverse=True)
    facts = []
    for r in rows[:k]:
        recs, _, _ = driver.execute_query(
            "MATCH (e:Entity {name:$n})-[rel]->(m) RETURN e.name AS s, type(rel) AS p, m.name AS o "
            "UNION MATCH (e:Entity {name:$n})<-[rel]-(m) RETURN m.name AS s, type(rel) AS p, e.name AS o",
            n=r["name"])
        facts.extend(f"{x['s']} {x['p']} {x['o']}" for x in recs)
    return "\n".join(f"- {f}" for f in dict.fromkeys(facts))


def respond(client, context, question):
    r = client.chat.completions.create(
        model=MODEL, temperature=0,
        messages=[{"role": "system", "content": "You are a helpful assistant. Use the memory "
                   f"about the user below; respect it.\n<memory>\n{context}\n</memory>"},
                  {"role": "user", "content": question}])
    return r.choices[0].message.content


def main():
    driver = get_graph()
    if driver is None:
        return   # graph-backed agent -- needs Neo4j
    import os
    if not os.environ.get("OPENAI_BASE_URL"):
        print("OPENAI_BASE_URL not set -- skipping (the agent needs the chat endpoint).")
        return

    client = get_client()
    embed = None
    if EMBED_MODEL:
        ec = get_embed_client()
        def embed(t):
            return list(ec.embeddings.create(model=EMBED_MODEL, input=[t]).data[0].embedding)
    else:
        print("(EMBED_MODEL not set -- recall ranks by importance, not relevance.)")

    with driver:
        # Turn 1: the user introduces themselves. The agent remembers.
        intro = ("Hi! I'm Alex. I just started as a data engineer at Acme Corp here in Portland, "
                 "and I'm allergic to shellfish. The weather's lovely today.")
        print(f"USER: {intro}")
        remember(driver, client, embed, intro)

        # Turn 2: a question that needs the memory from turn 1. It is about food, so the allergy
        # is the most RELEVANT memory -- hybrid recall surfaces it without the user re-stating it.
        question = "I'm booking a seafood restaurant for dinner. Anything I should keep in mind?"
        print(f"\nUSER: {question}")
        context = recall(driver, embed, question)
        print(f"   recalled memory:\n{context}")
        print(f"\nASSISTANT: {respond(client, context, question)}")
        print("\n(The shellfish allergy came from turn 1; relevance recall surfaced it for a "
              "seafood question that never mentioned it -- cross-session memory, end to end.)")


if __name__ == "__main__":
    main()
