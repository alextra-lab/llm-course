# Agent Memory: From Chat History to a Knowledge Graph

The **second** course in this repo — a hands-on, opinionated follow-on to the
[foundations course](../README.md). The foundations course covered memory in about a lesson
and a half (§12 stateless history + windowing/summarization; §18–19 embeddings and RAG).
This course goes deep on the part that's actually hard: what an agent knows **across**
sessions — a taxonomy of memory, conversational ingestion, a real knowledge graph, hybrid
retrieval, lifecycle and decay, and a *measured*, opinionated default.

> **A note on these courses.** This material is based on my own evolving experience
> building AI applications and working with LLMs. It's practical and opinionated, not
> authoritative — the field moves quickly, and some choices here will date or differ from
> yours. Verify anything before relying on it in production.

## Who this is for

This course **assumes the foundations course.** It leans on §12 (conversation state), §18
(embeddings), §19 (RAG), and §22 (agents) and does *not* re-teach them. If those aren't
familiar, do the foundations course first — start at
[`../lessons/01-hello-world.md`](../lessons/01-hello-world.md).

## The thesis

This course is **opinionated but honest**. It argues *toward* a particular default — a
knowledge graph with hybrid (graph + embedding) retrieval — but it gets there by walking
down a decision tree, and it will send you away early if your problem doesn't need a graph:

1. **Need memory across sessions at all?** No → window/summarize (§12). Stop.
2. **Mostly independent fact lookups?** Yes → a vector store / plain RAG (§18–19). Don't build a graph.
3. **Need to *correlate* facts — multi-hop, "who/what/when across history"?** Yes → a graph earns its keep.
4. **Memory shaped by ongoing conversation** (not a fixed corpus)? → favor *incremental* construction.
5. **Whichever branch:** gate writes, decay reads, and **measure recall before optimizing.**

Graph retrieval does **not** universally beat strong vector RAG — it wins on
multi-hop/relational/global-sensemaking and loses on simple lookup, latency, and token
cost. The course treats every "graphs win" claim as *"this paper reports,"* not consensus,
and teaches you *when* the graph is worth it.

## What's new beyond the foundations setup

Same house style: hosted vLLM (`gpt-oss-120b`), OpenAI SDK, thin dependencies, you write
every line. The graph units add **one optional backend**, opt-in exactly like the §16
Postgres demo:

- **Neo4j via Docker** for the hands-on graph. Set `NEO4J_URI` / `NEO4J_USER` /
  `NEO4J_PASSWORD` and `pip install neo4j`. Every graph script **skips cleanly** when those
  aren't set, so you can read any unit without a database running.

The reusable connection helper is [`examples/common_graph.py`](examples/common_graph.py);
the foundations `examples/common.py` (`get_client`, `MODEL`, `EMBED_MODEL`) is reused as-is
for the LLM client and embeddings.

```bash
# Reuse your foundations .env (OPENAI_BASE_URL / OPENAI_API_KEY / MODEL / EMBED_MODEL),
# then for the graph units start a throwaway local Neo4j and point at it:
docker run --rm -d --name memgraph-neo4j -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/devpassword neo4j:5
export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=devpassword
pip install neo4j
```

## How this course works

Same as the foundations course: **you write the code** in your own `work/` folder, running
each script as you go; a reference solution for everything lives under `examples/NN/`. Each
unit **builds** a piece, **decides** a tradeoff, and **cites** the SOTA it draws on — and
the whole arc converges on a single, defensible default.

## Outline

The arc is 12 standalone units (0–11). *(Authoring in progress — links appear as units
land.)*

0. **[The Memory Problem](lessons/00-the-memory-problem.md)** — context management (§12) vs. memory; the thesis stated. ✅
1. **[A Taxonomy of Memory](lessons/01-a-taxonomy-of-memory.md)** — working/episodic/semantic/procedural/profile/derived; which does your agent need? ✅
2. **[The Naive Baseline](lessons/02-the-naive-baseline.md)** — persist turns to SQLite, recall by recency/keyword; feel it break. ✅
3. **[Semantic Recall with Embeddings](lessons/03-semantic-recall-with-embeddings.md)** — vector similarity over facts (reuses §18); meaning-match, but no relationships. ✅
4. **[Why a Graph](lessons/04-why-a-graph.md)** — vector vs. relational vs. graph, and the honest, *conditional* evidence. ✅
5. **[Modeling Memory as a Graph](lessons/05-modeling-memory-as-a-graph.md)** — Neo4j via Docker; sessions, entities, and multi-hop Cypher by hand. ✅
6. **[Ingestion: Extracting Structure](lessons/06-ingestion-extracting-structure.md)** — LLM entity/relation extraction from a turn; embed entities; the dedup problem. ✅
7. **[Retrieval & Context Assembly](lessons/07-retrieval-and-context-assembly.md)** — entity-match traversal + hybrid graph/vector + rerank; a `search_memory` tool. ✅
8. **[Curation & Lifecycle](lessons/08-curation-and-lifecycle.md)** — promotion/demotion, decay (time vs. access), the promotion gate, consolidation. ✅
9. **[Measure Before You Optimize](lessons/09-measure-before-you-optimize.md)** — recall@k / precision@k / MRR / nDCG over your memory. ✅
10. **[Observability & Privacy](lessons/10-observability-and-privacy.md)** — joinable telemetry, visibility scopes, PII, Cypher injection, access control. ✅
11. **[The Opinionated Default](lessons/11-the-opinionated-default.md)** — wire a memory-backed agent; deliver the decision tree + when *not* to build this. ✅

Start with [`lessons/00-the-memory-problem.md`](lessons/00-the-memory-problem.md).
