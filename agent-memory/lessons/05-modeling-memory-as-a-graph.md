---
title: 'Modeling Memory as a Graph'
linkTitle: '5. Modeling Memory as a Graph'
weight: 5
---

**Goal:** start a real graph database (Neo4j, via Docker) and model a conversation's memory
in it **by hand** — sessions, turns, entities, and a small vocabulary of relationships —
writing the nodes and edges yourself in Cypher. By the end you will run a **multi-hop query**
that answers a question no single stored fact contains, and see why a row in a table or a
chunk in a vector store cannot give you this.

**Where this fits:** Unit 4 made the *decision* — for memory you need to **correlate**
(multi-hop, "who/what/when across history"), and a graph is worth its complexity. This unit is
the first practical result of that decision. We build the graph the slow, explicit way on
purpose: writing Cypher by hand now means that when Unit 6 lets an LLM extract these same
nodes and edges automatically, you will know *exactly* what it is producing and why.

> **Optional (opt-in), like §16.** This unit needs Neo4j. As with the foundations course's
> Postgres demo, the script skips cleanly when the database is not configured — so you can read
> the whole unit even if you do not start a container. To *run* it, you need a working `docker`
> and `pip install neo4j`.

---

## Why a graph's *shape* fits memory

A short recap of the conclusion from Unit 4, because the rest of this unit builds on it. Three
ways to store "Alex works at Acme, which is in Portland":

- **Rows** (a table): good for lookups by key, but relationships live in foreign keys and joins
  you write by hand, and the *kinds* of relationship are fixed by your schema.
- **Vectors** (§18–19): good at "find facts that *mean* something similar to this query." But
  each fact is an independent point; nothing *connects* "Alex" to "Portland."
- **A graph:** facts are **nodes**, relationships are **first-class edges** you can traverse.
  "Which city is Alex's employer in?" becomes *follow `WORKS_AT`, then follow `LOCATED_IN`* — a
  path, answered in one query, across facts that were never stated together.

Memory *is* a network of connected entities collected over time, so we store it as one.

## Start Neo4j (local, temporary)

One command starts a temporary Neo4j with both of its protocols exposed — `7687` (the **Bolt**
protocol that the driver uses) and `7474` (the browser interface, useful for *viewing* your
graph):

```bash
docker run --rm -d --name memgraph-neo4j -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/devpassword neo4j:5
```

`--rm` makes it temporary (it is removed when stopped); `NEO4J_AUTH` sets the password directly
so there is no first-run setup. Point the course environment at it — the same optional pattern
as §16's `DATABASE_URL`:

```bash
export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=devpassword
```

> **This is a temporary development database.** A simple password and an open port are fine for
> a local container that you will delete in ten minutes — never for anything real. Stop it with
> `docker stop memgraph-neo4j` when you are done; `--rm` removes it for you.

Our shared helper opens the driver from those three variables and **skips cleanly** if they are
not set or the driver is not installed — so the script (and this lesson) still runs end-to-end
with nothing configured. It lives in
[`agent-memory/examples/common_graph.py`](../examples/common_graph.py); the core of it:

```python
def get_graph():
    uri = os.environ.get("NEO4J_URI")
    if not uri:
        print("NEO4J_URI not set -- skipping the graph demo (this is optional).")
        return None
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("neo4j driver not installed -- skipping. Install with: pip install neo4j")
        return None
    driver = GraphDatabase.driver(uri, auth=(os.environ.get("NEO4J_USER", "neo4j"),
                                             os.environ.get("NEO4J_PASSWORD", "neo4j")))
    driver.verify_connectivity()
    return driver
```

Same discipline as the foundations course: one piece of connection setup, placed in one file,
that fails cleanly instead of crashing.

## Model the memory: nodes, edges, and a constraint

Create **`work/model_graph.py`**. First, a **uniqueness constraint** so there is only ever one
node per entity name — this is what lets us see "Acme Corp" again tomorrow and reach the same
node instead of creating a duplicate:

```python
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # agent-memory/examples
from common_graph import get_graph

SCHEMA = (
    "CREATE CONSTRAINT entity_name IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.name IS UNIQUE"
)
```

Now the memory itself. We model a **session** that **mentions** entities, plus a small
**relationship vocabulary** between those entities (`WORKS_AT`, `LOCATED_IN`). Each fact below
arrived in a *different turn* of the conversation — they are connected only because we connect
them:

```python
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
```

`MERGE` means "match if it exists, create if it does not" — so running the script twice does not
create duplicates (it is **idempotent**), and `ON CREATE SET` fills in properties only the first
time. That idempotence is exactly what you want from a memory writer that sees the same entity
again and again.

## The result: a multi-hop query

Here is the question that makes the graph worth its cost. Nobody ever said *"Alex is connected
to Portland."* To answer it you must **traverse two edges** and join three facts:

```python
MULTI_HOP = """
MATCH (p:Entity {name: 'Alex'})-[:WORKS_AT]->(c)-[:LOCATED_IN]->(city)
RETURN p.name AS person, c.name AS employer, city.name AS city
"""


def main():
    driver = get_graph()
    if driver is None:
        return   # skip notice already printed

    with driver:
        driver.execute_query(SCHEMA)
        driver.execute_query(WRITE, session="sess-1")   # $session is BOUND, not formatted

        records, _, _ = driver.execute_query(MULTI_HOP)
        for r in records:
            print(f"{r['person']} works at {r['employer']}, which is in {r['city']}.")


main()
```

```bash
python work/model_graph.py
```

You will see:

```
Alex works at Acme Corp, which is in Portland.
```

That sentence existed **nowhere** in what we stored. The graph *derived* it by following
`WORKS_AT` then `LOCATED_IN`. Open the browser interface at <http://localhost:7474> (log in with
`neo4j` / `devpassword`) and run the same `MATCH` to *see* the path.

> **What a vector store cannot do.** A vector store would return all three facts as the top
> chunks for "where does Alex work" — but it gives you three *separate* points and leaves the
> join to you (or to the model, hoping it connects them in context). The graph does the join *in
> the query*. That is the difference Unit 4 argued for, now working in front of you.
> *(Reference: [`examples/05/model_graph.py`](../examples/05/model_graph.py).)*

This structure — a time-ordered conversation that incrementally builds a typed entity graph —
is the basis of modern conversational-memory systems. **Zep / Graphiti** (Rasmussen et al.,
2025; arXiv:2501.13956) is one example, and it adds *bi-temporal* edges (when a fact was true
vs. when you learned it) on top of essentially this model. We will add time and incremental
ingestion in later units; right now you have the basic structure they all share.

---

> **Security:** Notice that `$session` is a **bound parameter**, never formatted into the query
> string. Building Cypher with f-strings around user-supplied text is **Cypher injection** — the
> graph version of the SQL injection in §16. A "name" like `' }) DETACH DELETE (n) //` formatted
> directly into a `MERGE` can change or delete your memory. Always pass values as parameters
> (`driver.execute_query(q, name=user_text)`); never put them in with an f-string. Unit 10
> returns to this with access scopes and PII.

## Challenges

1. **A shared-node multi-hop.** Add a second person who also `WORKS_AT` Acme, in a separate
   `MERGE`. Then write a query for *"who else works where Alex works?"* — a path out to the
   company and back to a different person. *Success:* the new colleague appears without you ever
   stating "Alex and they are connected."
2. **Memory that grows across sessions.** Run the writer again with `session="sess-2"` and add
   one new fact (for example, `Acme -[:COMPETES_WITH]-> 'Globex'`). Confirm there is still
   exactly **one** `Acme Corp` node (the constraint + `MERGE` did their job) and that the old
   multi-hop query still works. *Success:* two sessions, one growing graph — the whole point of
   cross-session memory.
3. **Show the injection.** Insert an entity whose name is `' }) DETACH DELETE (n) //` — once by
   formatting it into the Cypher with an f-string, once as a bound parameter. *Success:* you can
   show the formatted version is dangerous (and the bound version stores it safely as an
   ordinary, strange-looking name).

## Recap

- Memory is a network of connected entities over time, so we store it as a **graph**: facts are
  **nodes**, relationships are **traversable edges**.
- **Neo4j via Docker** is an optional backend (like §16 Postgres); the shared `get_graph()`
  helper skips cleanly when it is not configured.
- We modeled `Session -[:MENTIONS]-> Entity` plus an entity **relationship vocabulary**, and
  wrote it with **`MERGE`** (idempotent), protected by a **uniqueness constraint** (no duplicate
  entities — the start of deduplication).
- A **multi-hop query** derives a fact (`Alex → Acme → Portland`) that is in **no single stored
  row** — the thing rows and vectors cannot do for you.
- **Bind your parameters.** Putting user text into Cypher with an f-string is injection.

## Next

**Unit 6 — Ingestion: Extracting Structure:** you wrote those nodes and edges by hand. Next we
let an LLM read a raw conversational turn and produce them automatically — entity and relation
extraction — then embed the entities for hybrid recall and face the problem the constraint only
hinted at: **deduplication** (are "Acme," "Acme Corp," and "ACME, Inc." one node or three?).
