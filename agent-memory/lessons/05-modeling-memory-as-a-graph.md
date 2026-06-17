---
title: 'Modeling Memory as a Graph'
linkTitle: '5. Modeling Memory as a Graph'
weight: 5
---

**Goal:** stand up a real graph database (Neo4j, via Docker) and model a conversation's
memory in it **by hand** — sessions, turns, entities, and a small vocabulary of
relationships — writing the nodes and edges yourself in Cypher. By the end you'll run a
**multi-hop query** that answers a question no single stored fact contains, and *feel* why
that's the thing a row in a table or a chunk in a vector store can't give you.

**Where this fits:** Unit 4 made the *decision* — for memory you need to **correlate**
(multi-hop, "who/what/when across history"), a graph earns its complexity. This unit is the
first hands-on payoff of that decision. We build the graph the slow, explicit way on
purpose: writing Cypher by hand now means that when Unit 6 lets an LLM extract these same
nodes and edges automatically, you'll know *exactly* what it's producing and why.

> **Opt-in, like §16.** This unit needs Neo4j. As with the foundations course's Postgres
> demo, the runnable script skips cleanly when the database isn't configured — so you can
> read the whole unit even if you don't start a container. To *run* it, you'll need a
> working `docker` and `pip install neo4j`.

---

## Why a graph's *shape* fits memory

A quick recap of the conclusion from Unit 4, because it's what the rest of this unit
builds on. Three ways to store "Alex works at Acme, which is in Portland":

- **Rows** (a table): great for lookups by key, but relationships live in foreign keys and
  joins you write by hand, and the *kinds* of relationship are fixed by your schema.
- **Vectors** (§18–19): great at "find facts that *mean* something similar to this query."
  But each fact is an independent point; nothing *connects* "Alex" to "Portland."
- **A graph:** facts are **nodes**, relationships are **first-class edges** you can
  traverse. "Which city is Alex's employer in?" becomes *follow `WORKS_AT`, then follow
  `LOCATED_IN`* — a path, answered in one query, across facts that were never stated
  together.

Memory *is* a web of connected entities accumulated over time, so we store it as one.

## Start Neo4j (throwaway, local)

One command brings up a disposable Neo4j with both its protocols exposed — `7687` (the
**Bolt** wire protocol the driver speaks) and `7474` (the browser UI, handy for *seeing*
your graph):

```bash
docker run --rm -d --name memgraph-neo4j -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/devpassword neo4j:5
```

`--rm` makes it ephemeral (it vanishes when stopped); `NEO4J_AUTH` sets the password
inline so there's no first-run setup. Point the course env at it — the same opt-in shape
as §16's `DATABASE_URL`:

```bash
export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=devpassword
```

> **It's a throwaway dev box.** A trivial password and an open port are fine for a local
> container you'll delete in ten minutes — never for anything real. Stop it with
> `docker stop memgraph-neo4j` when you're done; `--rm` removes it for you.

Our shared helper opens the driver from those three variables and **skips cleanly** if
they're unset or the driver isn't installed — so the script (and this lesson) still runs
end-to-end with nothing configured. It lives in
[`agent-memory/examples/common_graph.py`](../examples/common_graph.py); the heart of it:

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

Same discipline as the foundations course: one bit of connection boilerplate, factored
into one place, that degrades gracefully instead of crashing.

## Model the memory: nodes, edges, and a constraint

Create **`work/model_graph.py`**. First, a **uniqueness constraint** so there's only ever
one node per entity name — this is what lets us *re-see* "Acme Corp" tomorrow and land on
the same node instead of making a duplicate:

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
**relationship vocabulary** between those entities (`WORKS_AT`, `LOCATED_IN`). Each fact
below arrived in a *different turn* of the conversation — they're only connected because we
connect them:

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

`MERGE` is "match if it exists, create if it doesn't" — so running the script twice
doesn't double anything (it's **idempotent**), and `ON CREATE SET` fills in properties only
the first time. That idempotence is exactly what you want from a memory writer that sees
the same entity again and again.

## The payoff: a multi-hop query

Here's the question that makes the graph worth its weight. Nobody ever said *"Alex is
connected to Portland."* To answer it you must **traverse two edges** and join three facts:

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

You'll see:

```
Alex works at Acme Corp, which is in Portland.
```

That sentence existed **nowhere** in what we stored. The graph *derived* it by walking
`WORKS_AT` then `LOCATED_IN`. Open the browser UI at <http://localhost:7474> (log in with
`neo4j` / `devpassword`) and run the same `MATCH` to *see* the path light up.

> **What a vector store can't do.** A vector store would happily return all three facts as
> the top chunks for "where does Alex work" — but it hands you three *separate* points and
> leaves the join to you (or to the model, hoping it connects them in-context). The graph
> does the join *in the query*. That's the difference Unit 4 argued for, now in your hands.
> *(Reference: [`examples/05/model_graph.py`](../examples/05/model_graph.py).)*

This shape — a temporally-ordered conversation incrementally building a typed entity graph
— is the basis of modern conversational-memory systems; **Zep / Graphiti** (Rasmussen et
al., 2025; arXiv:2501.13956) is a representative one, and it adds *bi-temporal* edges (when
a fact was true vs. when you learned it) on top of essentially this model. We'll layer
time and incremental ingestion on in later units; right now you have the skeleton they all
share.

---

> **Security:** Notice `$session` is a **bound parameter**, never formatted into the query
> string. Building Cypher with f-strings around user-supplied text is **Cypher injection** —
> the graph cousin of the SQL injection in §16. A "name" like `' }) DETACH DELETE (n) //`
> formatted straight into a `MERGE` can rewrite or wipe your memory. Always pass values as
> parameters (`driver.execute_query(q, name=user_text)`); never f-string them in. Unit 10
> returns to this with access scopes and PII.

## Challenges

1. **A shared-node multi-hop.** Add a second person who also `WORKS_AT` Acme, in a separate
   `MERGE`. Then write a query for *"who else works where Alex works?"* — a path out to the
   company and back to a different person. *Success:* the new colleague appears without you
   ever stating "Alex and they are connected."
2. **Memory that accumulates across sessions.** Run the writer again with `session="sess-2"`
   and add one new fact (say, `Acme -[:COMPETES_WITH]-> 'Globex'`). Confirm there's still
   exactly **one** `Acme Corp` node (the constraint + `MERGE` did their job) and that the
   old multi-hop query still works. *Success:* two sessions, one growing graph — the whole
   point of cross-session memory.
3. **Prove the injection.** Insert an entity whose name is
   `' }) DETACH DELETE (n) //` — once by f-string-formatting it into the Cypher, once as a
   bound parameter. *Success:* you can show the formatted version is dangerous (and the
   bound version stores it harmlessly as a literal weird name).

## Recap

- Memory is a web of connected entities over time, so we store it as a **graph**: facts are
  **nodes**, relationships are **traversable edges**.
- **Neo4j via Docker** is an opt-in backend (like §16 Postgres); the shared `get_graph()`
  helper skips cleanly when it isn't configured.
- We modeled `Session -[:MENTIONS]-> Entity` plus an entity **relationship vocabulary**, and
  wrote it with **`MERGE`** (idempotent) guarded by a **uniqueness constraint** (no
  duplicate entities — the seed of dedup).
- A **multi-hop query** derives a fact (`Alex → Acme → Portland`) that lives in **no single
  stored row** — the thing rows and vectors can't do for you.
- **Bind your parameters.** F-stringing user text into Cypher is injection.

## Next

**Unit 6 — Ingestion: Extracting Structure:** you wrote those nodes and edges by hand.
Next we let an LLM read a raw conversational turn and produce them automatically — entity
and relation extraction — then embed the entities for hybrid recall and confront the
problem the constraint only hinted at: **deduplication** (is "Acme," "Acme Corp," and
"ACME, Inc." one node or three?).
