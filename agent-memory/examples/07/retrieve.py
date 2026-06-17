"""
Unit 7 - Retrieval & context assembly: get facts back OUT of the memory graph (OPT-IN).

Units 5-6 filled a graph. Now we read from it two ways and combine them:

  1. ENTITY-MATCH TRAVERSAL  -- you already know the anchor node ("Acme Corp"); walk its
     edges and return the connected facts. Precise, but needs an exact starting point.
  2. BROAD MEANING RECALL    -- "what am I allergic to?" names no node; embed the query and
     rank entities by vector similarity (the vectors Unit 6 stored). Fuzzy, no anchor needed.

Then we RERANK the candidates by recency x importance x relevance (Generative Agents,
Park et al., UIST 2023; arXiv:2304.03442), ASSEMBLE the top facts into a prompt, and wrap
the whole thing as a `search_memory` tool an agent can call (foundations Section 22).

OPT-IN: set NEO4J_URI (see Unit 5) or the script skips cleanly. EMBED_MODEL is optional --
without it the RELEVANCE term is unavailable, so we rank on recency x importance and say so.

    docker run --rm -d --name memgraph-neo4j -p 7474:7474 -p 7687:7687 \
        -e NEO4J_AUTH=neo4j/devpassword neo4j:5
    export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=devpassword
    python agent-memory/examples/07/retrieve.py
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))               # agent-memory/examples
sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common import get_embed_client, EMBED_MODEL
from common_graph import get_graph


def log_event(session_id, trace_id, step, operation, **fields):
    """One joinable telemetry line per memory op (foundations Section 9 shape). The
    session_id/trace_id/step tuple ties this run together; Unit 10 adds redaction + scope."""
    print(json.dumps({"session_id": session_id, "trace_id": trace_id, "step": step,
                      "operation": operation, **fields}, sort_keys=True), file=sys.stderr)

# A small memory graph to read from. Each entity carries two ranking signals besides its
# embedding: `importance` (1-10, how much this fact matters -- the allergy is a 9) and
# `age_days` (time since we last SAW it -- the allergy is old). We store age as a fixed
# number, not a live clock, so the demo is deterministic; in production it is "now minus
# last-access". MERGE makes re-running idempotent.
SEED = """
MERGE (alex:Entity {name:'Alex'})        ON CREATE SET alex.type='person',   alex.importance=8, alex.age_days=1
MERGE (acme:Entity {name:'Acme Corp'})   ON CREATE SET acme.type='company',  acme.importance=7, acme.age_days=1
MERGE (pdx:Entity  {name:'Portland'})    ON CREATE SET pdx.type='city',      pdx.importance=4,  pdx.age_days=30
MERGE (py:Entity   {name:'Python'})      ON CREATE SET py.type='language',   py.importance=5,   py.age_days=10
MERGE (sf:Entity   {name:'shellfish'})   ON CREATE SET sf.type='allergen',   sf.importance=9,   sf.age_days=60
MERGE (q3:Entity   {name:'Q3 deadline'}) ON CREATE SET q3.type='event',      q3.importance=6,   q3.age_days=3
MERGE (alex)-[:WORKS_AT]->(acme)
MERGE (acme)-[:LOCATED_IN]->(pdx)
MERGE (alex)-[:USES]->(py)
MERGE (alex)-[:ALLERGIC_TO]->(sf)
MERGE (alex)-[:HAS_DEADLINE]->(q3)
"""

DECAY = 0.97   # per-day base for recency = DECAY ** age_days. A knob; smaller forgets faster.


def traverse(driver, name):
    """ENTITY-MATCH: return the facts directly connected to one known node, both directions."""
    records, _, _ = driver.execute_query(
        "MATCH (e:Entity {name:$name})-[r]->(m) RETURN e.name AS s, type(r) AS p, m.name AS o "
        "UNION "
        "MATCH (e:Entity {name:$name})<-[r]-(m) RETURN m.name AS s, type(r) AS p, e.name AS o",
        name=name,
    )
    return [f"{r['s']} {r['p']} {r['o']}" for r in records]


def candidates(driver, with_embedding=False):
    """Pull every entity with its ranking signals. Production restricts this set first (a
    vector index returns the top-N by similarity); scoring the whole graph is fine for a demo.
    We only fetch the embedding when we'll actually use it for the relevance term."""
    emb = ", e.embedding AS embedding" if with_embedding else ""
    records, _, _ = driver.execute_query(
        f"MATCH (e:Entity) RETURN e.name AS name, e.importance AS importance, "
        f"e.age_days AS age_days{emb}"
    )
    return [dict(r) for r in records]


def normalize(xs):
    """Min-max each signal to [0,1] so three different scales can be summed (Generative Agents)."""
    lo, hi = min(xs), max(xs)
    if hi == lo:
        return [0.0 for _ in xs]
    return [(x - lo) / (hi - lo) for x in xs]


def cosine(a, b):
    import numpy as np
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def rank(cands, query_vec):
    """Score = recency + importance + relevance, each normalized to [0,1] and summed with
    equal weight -- the Generative Agents retrieval function. Relevance joins only if we have
    embeddings (query_vec) AND the node was stored with one; otherwise we rank on the other two."""
    recency = normalize([DECAY ** c["age_days"] for c in cands])
    importance = normalize([c["importance"] for c in cands])
    if query_vec is not None and all(c.get("embedding") for c in cands):
        relevance = normalize([cosine(query_vec, c["embedding"]) for c in cands])
    else:
        relevance = [0.0 for _ in cands]   # term unavailable -> contributes nothing
    scored = [(c["name"], rec + imp + rel)
              for c, rec, imp, rel in zip(cands, recency, importance, relevance)]
    return sorted(scored, key=lambda t: t[1], reverse=True)


def search_memory(driver, query, embed=None, k=3, session_id=None, trace_id=None, step=0):
    """HYBRID recall + assembly: rank entities, then traverse the top-k and gather their facts
    into one context block ready to drop into a prompt. This is the body of the agent tool."""
    query_vec = embed(query) if embed is not None else None
    has_relevance = query_vec is not None
    top = rank(candidates(driver, with_embedding=has_relevance), query_vec)[:k]
    facts = []
    for name, _ in top:
        facts.extend(traverse(driver, name))
    facts = list(dict.fromkeys(facts))   # dedup, keep order
    if session_id is not None:   # the recall operation logs itself (foundations Section 9)
        log_event(session_id, trace_id, step, "recall", query=query,
                  ranked_by="relevance" if has_relevance else "recency+importance",
                  entities=[name for name, _ in top], recalled=len(facts))
    return "\n".join(f"- {f}" for f in facts)


# The tool an agent calls (foundations Section 22). The schema is what the model sees; the
# function above is what runs when it calls. We build the spec here; wiring it into a chat
# loop is the same tool-calling pattern as Section 22.
SEARCH_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": "Recall facts about the user from long-term memory. Call before "
                       "answering anything that depends on what you already know about them.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "what to recall"}},
            "required": ["query"],
        },
    },
}


def main():
    driver = get_graph()
    if driver is None:
        return   # skip notice already printed -- this unit reads from the graph

    embed = None
    if EMBED_MODEL:
        client = get_embed_client()   # only needed for the relevance term; retrieval itself is pure graph
        def embed(text):
            v = client.embeddings.create(model=EMBED_MODEL, input=[text]).data[0].embedding
            return list(v)
    else:
        print("(EMBED_MODEL not set -- ranking on recency x importance only; "
              "relevance joins once embeddings are stored. See Unit 6.)\n")

    with driver:
        driver.execute_query(SEED)
        if embed is not None:   # backfill embeddings onto the seed nodes for the relevance term
            for c in candidates(driver):
                driver.execute_query("MATCH (e:Entity {name:$n}) SET e.embedding=$v",
                                     n=c["name"], v=embed(c["name"]))

        # 1. Entity-match traversal: we KNOW the anchor.
        print("traverse('Acme Corp'):")
        for f in traverse(driver, "Acme Corp"):
            print("   ", f)

        # 2 + 3. Broad recall + rerank + assembly: no anchor, just a question. Note how the
        # OLD-but-IMPORTANT allergy still surfaces -- recency alone would bury it; importance
        # rescues it. That is the whole point of the multi-signal score.
        session_id, trace_id = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]   # one run; see Section 9
        for i, q in enumerate(["what am I allergic to?", "where is my employer?"]):
            print(f"\nsearch_memory({q!r}) ->")
            print(search_memory(driver, q, embed, session_id=session_id, trace_id=trace_id, step=i))

        # 4. Assemble into a prompt the way the agent would (we build the messages; calling the
        # model is the Section 22 loop).
        context = search_memory(driver, "what am I allergic to?", embed,
                                session_id=session_id, trace_id=trace_id, step=2)
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Use the memory below.\n"
                                          f"<memory>\n{context}\n</memory>"},
            {"role": "user", "content": "Can you suggest a seafood restaurant?"},
        ]
        print("\nassembled system prompt:\n", messages[0]["content"])
        print("\nsearch_memory tool name:", SEARCH_MEMORY_TOOL["function"]["name"],
              "(wire into a tool-calling loop exactly as in foundations Section 22)")


if __name__ == "__main__":
    main()
