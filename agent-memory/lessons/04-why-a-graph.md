---
title: 'Why a Graph'
linkTitle: '4. Why a Graph'
weight: 4
---

**Goal:** make the central decision of this course *honestly*. A graph is more machinery
than a vector store — Docker, a schema, extraction, traversal. When is that complexity
*earned*, and when does it just cost you tokens and latency for no gain? This unit lays out
the three storage models, the real (conditional) evidence, and a clear rule for when to
cross the line.

**Where this fits:** Unit 3 took you to the edge — semantic recall fixed phrasing but
couldn't **correlate** facts. This unit decides whether to cross into graph territory.
It's a "decide" unit: no new code, but the argument that justifies all the building in
Units 5–7. Cross deliberately, or don't cross at all.

---

## Three ways to store a fact

"Alex works at Acme, which is in Portland," stored three ways:

| | Strength | Weakness for memory |
|---|---|---|
| **Rows** (SQL, Unit 2) | Exact lookup by key; transactions | Relationships are hand-written joins; matches strings, not meaning |
| **Vectors** (Unit 3) | Recall by **meaning**; trivial to add facts | Each fact is an island — no way to *follow* a connection |
| **Graph** | Relationships are **first-class, traversable** edges | Most machinery; extraction is lossy; can cost more per query |

The graph's one distinctive power is the **multi-hop join**: "what city is my employer in?"
is *follow `WORKS_AT`, then `LOCATED_IN`* — answered across facts that were never stated
together. That's the capability Unit 3 couldn't provide, and it's the *only* reason to take
on the extra complexity. If your problem never needs it, the graph is overhead.

## The honest part: graphs don't always win

Here's where this course refuses to oversell. The research consensus is **conditional**, not
"graphs are better." Graph and multi-hop retrieval win on **multi-hop, relational, and
global-sensemaking** questions; they **lose** on simple fact lookup, on latency, and on
token cost — sometimes by a lot.

The numbers matter, so look at them — and note they're **author-reported, with
self-implemented baselines**, so read them as *"this paper reports,"* not settled fact:

- **Earlier graph systems underperformed the strongest vector baseline.** On one
  associative-recall comparison, average F1: RAPTOR **48.8**, GraphRAG **49.6**, HippoRAG
  **53.1** — all *below* a strong dense retriever, NV-Embed-v2, at **57.0**. A graph is not
  automatically an upgrade.
- **The reversal is recent and required real work.** **HippoRAG 2** (Gutiérrez et al.,
  *ICML* 2025; arXiv:2502.14802) reaches **59.8**, edging past the vector baseline — but only
  after fixing query contextualization (its personalized-PageRank traversal over a knowledge
  graph). The win is real *and* hard-earned, not free.
- **Graphs can cost an order of magnitude more.** **GraphRAG-Bench** (Xiang et al., *ICLR*
  2026; arXiv:2506.05690) reports that on **simple fact lookup**, a graph approach burned
  **~46,949 tokens** versus **~3,743** for top-5 vector retrieval. For a question a vector
  store answers fine, that's ~12× the cost for no benefit.

And **GraphRAG** itself (Edge et al., Microsoft, 2024; arXiv:2404.16130) — the canonical
build-the-graph-up-front system — was designed for **global sensemaking** ("what are the
themes across this whole corpus?"), a question vectors genuinely struggle with. It's a
*different tool for a different question*, not a drop-in upgrade to fact lookup.

## So: when do you cross the line?

The decision tree from Unit 0, now with the evidence behind step 3:

1. **No cross-session memory needed?** → window/summarize (§12). Stop.
2. **Facts mostly independent lookups?** → vector store / RAG (Unit 3, §19). **Stop here —
   the evidence says a graph would cost more for no gain.**
3. **Need to *correlate* — multi-hop, "who/what/when across history," relational
   questions?** → *now* a graph earns its complexity. This is the only branch that does.
4. **Memory shaped by ongoing conversation** (not a fixed corpus)? → favor **incremental**
   construction (Units 5–6, the Zep/Graphiti model) over GraphRAG's batch build-up-front.

Concretely: if your agent answers "what did the user tell me about X?" — vectors. If it
answers "given everything I know about the user, what connects A to C?" — graph. Most real
assistants have **both** kinds of question, which is why the course's eventual default is
**hybrid** (graph traversal *and* vector recall together), not graph-purist.

The intellectually honest position to carry forward: we're choosing a graph for a
*specific* capability (correlated, multi-hop recall over conversational memory) that the
evidence supports — **not** because graphs are universally better. They aren't. If your
problem stopped at step 2, the best thing this course can tell you is: *don't build the rest
of it.*

---

> **Security:** More structure is more attack surface. A graph turns flat facts into
> *traversable relationships*, so a single poisoned edge ("user `WORKS_AT` admin-group")
> doesn't just sit there — it propagates through every multi-hop query that crosses it. The
> capability that makes graphs useful (the join) is exactly what makes a bad fact more
> dangerous. That's a reason to gate writes (Unit 8) and scope traversals (Unit 10), not a
> reason to avoid graphs — but go in knowing the join cuts both ways.

## Challenges

1. **Classify ten questions.** Write ten things a user might ask your agent and label each
   "vector is enough" or "needs a join." *Success:* a ratio that tells you, for *your*
   agent, whether step 3 is even worth reaching — honestly including "mostly vectors."
2. **Price the wrong tool.** Using the GraphRAG-Bench figures, estimate the token cost of
   answering 1,000 simple-lookup questions with a graph vs. top-5 vectors. *Success:* a
   number that makes "don't graph a lookup problem" visceral.
3. **Find your multi-hop.** Identify one real question your agent must answer that genuinely
   needs two or more facts joined. *Success:* you can write it as a path
   (`A -[REL]-> B -[REL]-> C`) — which is exactly what you'll build in Unit 5.

## Recap

- Three storage models: **rows** (key lookup), **vectors** (meaning), **graph** (traversable
  relationships). The graph's one distinctive power is the **multi-hop join**.
- The evidence is **conditional**: graphs win on multi-hop/relational/global-sensemaking and
  **lose** on simple lookup, latency, and token cost (paper-reported ~46,949 vs ~3,743
  tokens; early graph systems *below* a strong vector baseline until HippoRAG 2).
- **Cross to a graph only at step 3** — when you need correlation. If your problem is
  independent lookups, stop at vectors.
- Real assistants usually need both, so the course's default is **hybrid**, not
  graph-purist.
- The honest stance: choose a graph for a capability the evidence supports, not because
  graphs are "better."

## Next

**Unit 5 — Modeling Memory as a Graph:** decision made. You'll stand up Neo4j, model
sessions, entities, and relationships, and run the multi-hop query this unit argued for —
answering "what city is my employer in?" across facts that were never stated together.
