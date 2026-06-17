"""
Unit 5 - Modeling memory as a graph: write a tiny memory graph in Neo4j BY HAND (OPT-IN).

Before we let an LLM extract structure for us (Unit 6), we build the graph the slow,
explicit way -- writing nodes and edges in Cypher ourselves -- so you see exactly what
shape the memory takes and why it answers a question a row or a vector can't: a
multi-hop JOIN across facts that arrived in different turns.

OPT-IN: start a throwaway Neo4j and point the env at it (see the lesson):
    docker run --rm -d --name memgraph-neo4j -p 7474:7474 -p 7687:7687 \
        -e NEO4J_AUTH=neo4j/devpassword neo4j:5
    export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=devpassword
    python agent-memory/examples/05/model_graph.py
Without NEO4J_URI (or without the `neo4j` driver) it prints a skip notice and exits 0,
so the lesson reads end-to-end with no database.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # agent-memory/examples
from common_graph import get_graph

# One entity per name. MERGE relies on this constraint to avoid creating a second
# "Acme Corp" the next time we see it -- the first hint of the dedup problem (Unit 6).
SCHEMA = (
    "CREATE CONSTRAINT entity_name IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.name IS UNIQUE"
)

# A handful of facts that arrived across DIFFERENT turns of a conversation. Each is
# trivial on its own; the value is entirely in how they connect. We model them as a
# session that mentions entities, plus a small relationship vocabulary between entities.
# MERGE is "create if absent, else match" -- so re-running this script is idempotent.
WRITE = """
MERGE (s:Session {id: $session})
MERGE (alex:Entity {name: 'Alex'})       ON CREATE SET alex.type = 'person'
MERGE (acme:Entity {name: 'Acme Corp'})  ON CREATE SET acme.type = 'company'
MERGE (pdx:Entity  {name: 'Portland'})   ON CREATE SET pdx.type  = 'city'
MERGE (s)-[:MENTIONS]->(alex)
MERGE (s)-[:MENTIONS]->(acme)
MERGE (alex)-[:WORKS_AT]->(acme)
MERGE (acme)-[:LOCATED_IN]->(pdx)
"""

# The payoff: a TWO-HOP question. "Which city is my user's employer based in?" was never
# stated directly -- nobody ever said "Alex is connected to Portland." It has to be
# JOINED across WORKS_AT and LOCATED_IN. A vector store would hand you the three facts as
# separate chunks; only the graph does the join for you, in one traversal.
MULTI_HOP = """
MATCH (p:Entity {name: 'Alex'})-[:WORKS_AT]->(c)-[:LOCATED_IN]->(city)
RETURN p.name AS person, c.name AS employer, city.name AS city
"""


def main():
    driver = get_graph()
    if driver is None:
        return   # skip notice already printed by get_graph()

    with driver:
        driver.execute_query(SCHEMA)
        # Parameters are BOUND ($session), never string-formatted into the query.
        # String-formatting user text into Cypher is injection -- see the lesson.
        driver.execute_query(WRITE, session="sess-1")

        records, _, _ = driver.execute_query(MULTI_HOP)
        for r in records:
            print(f"{r['person']} works at {r['employer']}, which is in {r['city']}.")

    print("\nThree facts, stated separately, joined in one traversal -- that's the graph.")


if __name__ == "__main__":
    main()
