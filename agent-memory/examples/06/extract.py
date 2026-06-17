"""
Unit 6 - Ingestion: let an LLM extract graph structure from a raw turn (OPT-IN: Neo4j).

In Unit 5 you wrote the nodes and edges by hand. Here the LLM does it: read one
conversational turn, emit (entity, relation, entity) triples, validate the JSON
(foundations Section 6), MERGE them into the SAME graph, embed each entity for hybrid
recall later (Section 18) -- and meet the problem the uniqueness constraint only hinted
at: deduplication.

Requires the chat endpoint (as every example does). The graph write is OPT-IN: set
NEO4J_URI (see Unit 5) or the script skips cleanly. EMBED_MODEL is optional -- without it
the embedding step is skipped and the rest still runs.

    python agent-memory/examples/06/extract.py
"""

import re
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.append(str(Path(__file__).resolve().parents[1]))               # agent-memory/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common import get_client, MODEL, EMBED_MODEL
from common_graph import get_graph

TURN = ("Hey, I'm Alex -- I just started as a data engineer at Acme Corp, "
        "and we're based out of Portland.")


class Entity(BaseModel):
    name: str
    type: str


class Relation(BaseModel):
    subject: str
    predicate: str
    object: str


class Extraction(BaseModel):
    entities: list[Entity]
    relations: list[Relation]


EXTRACT_PROMPT = """Extract entities and relationships from the message as JSON.
- "entities": each has "name" (canonical, e.g. 'Acme Corp') and "type" (person/company/city/role/...).
- "relations": each has "subject", "predicate" (UPPER_SNAKE_CASE verb, e.g. WORKS_AT, LOCATED_IN), "object".
Use entity names exactly as they appear in "entities". Return ONLY JSON with keys "entities" and "relations".

Message: {turn}"""


def extract(client, turn: str) -> Extraction:
    """Ask the model for triples, and VALIDATE the JSON before we trust it (Section 6)."""
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": EXTRACT_PROMPT.format(turn=turn)}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return Extraction.model_validate_json(r.choices[0].message.content)


def safe_rel(predicate: str) -> str:
    """Sanitize a relationship TYPE to an allow-list of [A-Z_].

    Cypher relationship types are part of the query STRUCTURE -- you cannot bind them as
    parameters (`MERGE (a)-[:$pred]->(b)` is a syntax error). So the model's predicate has
    to be formatted into the query, which is exactly where injection lives (Unit 5). The
    safe pattern: allow-list the type to letters/underscores, then format; bind the node
    VALUES as parameters. A hostile predicate like `KNOWS]->() DETACH DELETE n //` collapses
    to a harmless weird type, not executable Cypher.
    """
    rel = re.sub(r"[^A-Z_]", "", predicate.upper().replace(" ", "_")).strip("_")
    if not rel:
        raise ValueError(f"unusable relation type: {predicate!r}")
    return rel


def write_triples(driver, extraction: Extraction, embed=None) -> None:
    """MERGE entities and relations into the graph. Values bound; types sanitized."""
    for e in extraction.entities:
        driver.execute_query(
            "MERGE (e:Entity {name: $name}) ON CREATE SET e.type = $type",
            name=e.name, type=e.type,
        )
        if embed is not None:
            driver.execute_query(
                "MATCH (e:Entity {name: $name}) SET e.embedding = $vec",
                name=e.name, vec=embed(e.name),
            )
    for r in extraction.relations:
        rel = safe_rel(r.predicate)   # sanitized type -> query structure
        driver.execute_query(
            f"MATCH (a:Entity {{name: $s}}), (b:Entity {{name: $o}}) "
            f"MERGE (a)-[:{rel}]->(b)",
            s=r.subject, o=r.object,   # node values -> bound parameters
        )


def main():
    driver = get_graph()
    if driver is None:
        return   # skip notice already printed -- this unit writes to the graph

    client = get_client()
    extraction = extract(client, TURN)
    print("entities: ", [(e.name, e.type) for e in extraction.entities])
    print("relations:", [(r.subject, r.predicate, r.object) for r in extraction.relations])

    embed = None
    if EMBED_MODEL:
        def embed(text):
            v = client.embeddings.create(model=EMBED_MODEL, input=[text]).data[0].embedding
            return list(v)
    else:
        print("(EMBED_MODEL not set -- storing nodes without embeddings; hybrid recall waits.)")

    with driver:
        write_triples(driver, extraction, embed)

        # The dedup problem, made concrete: a later turn calls the same company "ACME Inc.".
        # Exact-name MERGE can't tell it's the same Acme -> a DUPLICATE node for one company.
        driver.execute_query(
            "MERGE (e:Entity {name: 'ACME Inc.'}) ON CREATE SET e.type = 'company'")
        records, _, _ = driver.execute_query(
            "MATCH (e:Entity) WHERE e.type = 'company' RETURN e.name AS n ORDER BY n")
        print("company nodes now:", [r["n"] for r in records],
              "<- one real company, two nodes. That's the dedup problem (see the lesson).")


if __name__ == "__main__":
    main()
