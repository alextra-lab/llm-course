---
title: 'Why a Graph'
linkTitle: '4. Why a Graph'
weight: 4
---

**Goal:** make the central decision of this course *honestly*. A graph is more work than a
vector store — Docker, a schema, extraction, traversal. When is that extra complexity *worth
it*, and when does it only cost you tokens and latency for no benefit? This unit describes the
three storage models, the real (conditional) evidence, and a clear rule for when to move to a
graph.

**Where this fits:** Unit 3 brought you to the limit — semantic recall fixed wording but could
not **correlate** facts. This unit decides whether to move to a graph. It is a "decide" unit:
no new code, but the argument that justifies all the building in Units 5–7. Move to a graph on
purpose, or do not move at all.

---

## Three ways to store a fact

"Alex works at Acme, which is in Portland," stored three ways:

| | Strength | Weakness for memory |
|---|---|---|
| **Rows** (SQL, Unit 2) | Exact lookup by key; transactions | Relationships are hand-written joins; matches strings, not meaning |
| **Vectors** (Unit 3) | Recall by **meaning**; easy to add facts | Each fact is separate — no way to *follow* a connection |
| **Graph** | Relationships are **first-class, traversable** edges | Most work to build; extraction is lossy; can cost more per query |

The graph's one special power is the **multi-hop join**: "what city is my employer in?" means
*follow `WORKS_AT`, then `LOCATED_IN`* — answered across facts that were never stated together.
That is the ability Unit 3 could not provide, and it is the *only* reason to accept the extra
complexity. If your problem never needs it, the graph is just extra cost.

## The honest part: graphs don't always win

Here this course refuses to oversell. The research result is **conditional**, not "graphs are
better." Graph and multi-hop retrieval are better on **multi-hop, relational, and
global-sensemaking** questions (finding themes across a whole collection of documents). They
are worse on simple fact lookup, on latency, and on token cost — sometimes by a large margin.

The numbers matter, so look at them — and note that they are **reported by the authors, with
baselines they implemented themselves**, so read them as *"this paper reports,"* not as settled
fact:

- **Earlier graph systems were worse than the strongest vector baseline.** On one
  associative-recall comparison, average F1: RAPTOR **48.8**, GraphRAG **49.6**, HippoRAG
  **53.1** — all *below* a strong dense retriever, NV-Embed-v2, at **57.0**. A graph is not
  automatically an improvement.
- **The change is recent and required real work.** **HippoRAG 2** (Gutiérrez et al., *ICML*
  2025; arXiv:2502.14802) reaches **59.8**, just above the vector baseline — but only after
  fixing query contextualization (its personalized-PageRank traversal over a knowledge graph).
  The improvement is real *and* hard to get, not free.
- **Graphs can cost about ten times more.** **GraphRAG-Bench** (Xiang et al., *ICLR* 2026;
  arXiv:2506.05690) reports that on **simple fact lookup**, a graph approach used **~46,949
  tokens** versus **~3,743** for top-5 vector retrieval. For a question a vector store answers
  well, that is about 12× the cost for no benefit.

And **GraphRAG** itself (Edge et al., Microsoft, 2024; arXiv:2404.16130) — the well-known
build-the-graph-in-advance system — was designed for **global sensemaking** ("what are the
themes across this whole collection?"), a question that vectors genuinely handle poorly. It is
a *different tool for a different question*, not a direct replacement for fact lookup.

## So when do you move to a graph?

The decision tree from Unit 0, now with the evidence behind step 3:

1. **No cross-session memory needed?** → window/summarize (§12). Stop.
2. **Facts mostly independent lookups?** → vector store / RAG (Unit 3, §19). **Stop here — the
   evidence says a graph would cost more for no benefit.**
3. **Need to *correlate* — multi-hop, "who/what/when across history," relational questions?**
   → *now* a graph is worth its complexity. This is the only branch where it is.
4. **Memory shaped by ongoing conversation** (not a fixed corpus)? → prefer **incremental**
   construction (Units 5–6, the Zep/Graphiti model) over GraphRAG's build-everything-in-advance
   approach.

In concrete terms: if your agent answers "what did the user tell me about X?" — use vectors. If
it answers "given everything I know about the user, what connects A to C?" — use a graph. Most
real assistants have **both** kinds of question, which is why the course's final default is
**hybrid** (graph traversal *and* vector recall together), not graph-only.

The honest position to keep in mind: we choose a graph for a *specific* ability (correlated,
multi-hop recall over conversational memory) that the evidence supports — **not** because graphs
are better in general. They are not. If your problem stopped at step 2, the best advice this
course can give is: *do not build the rest of it.*

---

> **Security:** More structure means more attack surface. A graph turns flat facts into
> *traversable relationships*, so a single poisoned edge ("user `WORKS_AT` admin-group") does
> not just sit there — it spreads through every multi-hop query that passes over it. The ability
> that makes graphs useful (the join) is exactly what makes a bad fact more dangerous. That is a
> reason to control writes (Unit 8) and limit traversals (Unit 10), not a reason to avoid graphs
> — but understand that the join works in both directions.

## Challenges

1. **Classify ten questions.** Write ten things a user might ask your agent and label each
   "vector is enough" or "needs a join." *Success:* a ratio that tells you, for *your* agent,
   whether step 3 is even worth reaching — honestly including "mostly vectors."
2. **Price the wrong tool.** Using the GraphRAG-Bench numbers, estimate the token cost of
   answering 1,000 simple-lookup questions with a graph versus top-5 vectors. *Success:* a
   number that makes "do not use a graph for a lookup problem" concrete.
3. **Find your multi-hop.** Identify one real question your agent must answer that genuinely
   needs two or more facts joined. *Success:* you can write it as a path
   (`A -[REL]-> B -[REL]-> C`) — which is exactly what you will build in Unit 5.

## Recap

- Three storage models: **rows** (key lookup), **vectors** (meaning), **graph** (traversable
  relationships). The graph's one special power is the **multi-hop join**.
- The evidence is **conditional**: graphs are better on multi-hop/relational/global-sensemaking
  and worse on simple lookup, latency, and token cost (paper-reported ~46,949 vs ~3,743 tokens;
  early graph systems *below* a strong vector baseline until HippoRAG 2).
- **Move to a graph only at step 3** — when you need correlation. If your problem is independent
  lookups, stay with vectors.
- Real assistants usually need both, so the course's default is **hybrid**, not graph-only.
- The honest position: choose a graph for an ability the evidence supports, not because graphs
  are "better."

## Next

**Unit 5 — Modeling Memory as a Graph:** the decision is made. You will start Neo4j, model
sessions, entities, and relationships, and run the multi-hop query this unit argued for —
answering "what city is my employer in?" across facts that were never stated together.
