---
title: 'Semantic Recall with Embeddings'
linkTitle: '3. Semantic Recall with Embeddings'
weight: 3
---

**Goal:** fix the biggest crack in the Unit 2 baseline. Embed each remembered fact as a
vector and recall by **meaning** — cosine similarity — so a user's question retrieves the
right fact even when they phrase it nothing like the stored text. This is the same
machinery as foundations §18–19, now pointed at *memory* instead of documents.

**Where this fits:** Unit 2's keyword recall failed on "where do I live?" and "what seafood
am I allergic to?" because it matched strings, not meaning. Embeddings close exactly that
gap. But they introduce the *next* limitation — each fact is an independent point — which is
what sends us toward a graph in Unit 4. This unit needs `EMBED_MODEL`; without it the script
skips cleanly and you can still read along.

---

## Recall by meaning, not string

You built this in §18: an embedding maps text to a vector so that *similar meaning →
nearby vectors*, and cosine similarity scores the nearness. We reuse the same two helpers
verbatim — the point of the foundations course is that you already own this code:

```python
def embed(client, texts):
    r = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return np.array([d.embedding for d in r.data])


def cosine(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
```

Now apply it to the **same facts** Unit 2 stored. Embed them once, then for a query embed it
too and return the nearest fact. Create **`work/semantic_recall.py`**:

```python
FACTS = [
    "I work at Acme Corp as a data engineer.",
    "We just moved the team to Portland.",
    "My favorite language is Python.",
    "I'm allergic to shellfish, by the way.",
    "The Q3 deadline got pushed to October.",
]

fact_vecs = embed(client, FACTS)        # embed the memory ONCE, up front

for query in ["where do I live?", "what foods should I avoid?"]:
    q = embed(client, [query])[0]
    best = max(range(len(FACTS)), key=lambda i: cosine(q, fact_vecs[i]))
    print(f"{query!r} -> {FACTS[best]!r}")
```

```bash
python work/semantic_recall.py
```

```
'where do I live?' -> 'We just moved the team to Portland.'
'what foods should I avoid?' -> "I'm allergic to shellfish, by the way."
```

Both questions that **keyword recall whiffed on in Unit 2 now hit** — "live" found Portland,
"foods to avoid" found shellfish — without the query sharing a single word with the stored
fact. That's the whole value of semantic recall: users get to ask in their own words.
*(Reference: [`examples/03/semantic_recall.py`](../examples/03/semantic_recall.py).)*

> **This is the step-2 branch of the decision tree.** If your agent's memory is a bag of
> mostly-independent facts and you just need to fetch the relevant ones, **you're done
> here** — semantic recall over a vector store is plain RAG (§19), and it's the right tool.
> Don't build a graph for this. The rest of the course is for when this *isn't* enough.

## Where embeddings stop

Semantic recall fixed *phrasing*. It does **not** fix *correlation*. Each fact is an
independent point in vector space, with no notion that two facts are about the same thing or
connect to each other. Watch it fail on a question that needs *two* facts joined:

> **"What city is my employer based in?"**

The answer requires connecting *"I work at Acme Corp"* to *"Acme is in Portland."* Embed
that question and you'll retrieve the **employer** fact (closest by meaning) — or maybe the
**city** fact — but the store can only hand you ranked *individual* facts. It can't *follow*
"Acme" from one fact to the other, because to a vector store "Acme Corp" in fact 1 and
"Acme" in fact 2 are just two regions of space, not the same entity. You (or the model,
in-context) are left to do the join by hand, and that gets fragile fast as memory grows and
the hops get longer.

This is the precise boundary between **step 2** and **step 3** of the decision tree:
independent lookups → vectors; **correlated, multi-hop** recall → something relational. Unit
4 examines the evidence for crossing that boundary honestly, and Unit 5 builds the graph
that does the join *for* you.

A practical aside you'll use later: the choice isn't vectors *or* graph. The strongest
memory systems keep embeddings **and** structure together — recall candidate entities by
vector similarity, then traverse their relationships. You'll store a vector on every graph
node in Unit 6 for exactly this **hybrid** recall. Embeddings don't get thrown away; they
become one half of the retrieval.

---

> **Security:** Semantic recall will surface whatever is in the store, including a fact a
> previous (possibly hostile) turn planted — and it'll surface it for any query that's
> merely *near* it in meaning, which is easier to engineer than an exact keyword match. What
> you embed, you make findable. Gate what enters memory (Unit 8) and scope what a given query
> may retrieve (Unit 10); semantic reach makes both matter more, not less.

## Challenges

1. **Beat the baseline, measured.** Run your five questions from Unit 2's challenge through
   semantic recall and compare the hit-rate. *Success:* a concrete before/after number — and
   at least one question where semantics wins that keyword lost.
2. **Find semantic recall's own miss.** Construct a question that retrieves a *plausible but
   wrong* fact (high cosine, wrong answer). *Success:* you can explain why nearness in
   meaning isn't the same as correctness — motivating reranking (Unit 7).
3. **Expose the join gap.** Ask "what city is my employer in?" and show that top-1 (and even
   top-2) recall doesn't *connect* the employer and city facts. *Success:* you can state
   precisely what operation is missing — and which unit supplies it.

## Recap

- Embedding facts and recalling by **cosine similarity** fixes Unit 2's fatal flaw: users
  can ask in **their own words**, not the stored words.
- For a bag of **independent** facts, this *is* the answer — vector-store RAG (§19). Don't
  over-build past it.
- Embeddings don't fix **correlation**: each fact is an isolated point, so **multi-hop**
  questions ("employer → its city") can't be answered by ranking individual facts.
- That boundary — independent lookup vs. correlated recall — is steps 2→3 of the decision
  tree, and the reason the course continues.
- Vectors aren't discarded later; they become half of **hybrid** graph + vector recall.

## Next

**Unit 4 — Why a Graph:** before we reach for heavier machinery, the honest case. When does
a graph actually beat strong vector recall — and when does it just cost you more tokens and
latency for no gain? We'll look at the real benchmark numbers and decide deliberately.
