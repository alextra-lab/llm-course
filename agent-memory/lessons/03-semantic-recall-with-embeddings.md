---
title: 'Semantic Recall with Embeddings'
linkTitle: '3. Semantic Recall with Embeddings'
weight: 3
---

**Goal:** fix the biggest problem in the Unit 2 baseline. Embed each remembered fact as a
vector and recall by **meaning** — using cosine similarity — so a user's question retrieves
the right fact even when the question uses very different words than the stored text. This is
the same method as foundations §18–19, now applied to *memory* instead of documents.

**Where this fits:** Unit 2's keyword recall failed on "where do I live?" and "what seafood am
I allergic to?" because it matched strings, not meaning. Embeddings fix exactly that gap. But
they create the *next* limitation — each fact is an independent point — which is what leads us
toward a graph in Unit 4. This unit needs `EMBED_MODEL`; without it the script skips cleanly
and you can still read along.

---

## Recall by meaning, not by string

You built this in §18: an embedding maps text to a vector so that *similar meaning → nearby
vectors*, and cosine similarity measures how near they are. We reuse the same two helpers
exactly — the point of the foundations course is that you already have this code:

```python
def embed(client, texts):
    r = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return np.array([d.embedding for d in r.data])


def cosine(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
```

Now apply it to the **same facts** Unit 2 stored. Embed them once, then for each query embed
the query too and return the nearest fact. Create **`work/semantic_recall.py`**:

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

Both questions that **keyword recall failed on in Unit 2 now succeed** — "live" found
Portland, "foods to avoid" found shellfish — even though the query shares no words with the
stored fact. This is the whole value of semantic recall: users can ask in their own words.
*(Reference: [`examples/03/semantic_recall.py`](../examples/03/semantic_recall.py).)*

> **This is the step-2 branch of the decision tree.** If your agent's memory is a set of
> mostly independent facts and you only need to fetch the relevant ones, **you are done
> here** — semantic recall over a vector store is plain RAG (§19), and it is the right tool.
> Do not build a graph for this. The rest of the course is for when this is *not* enough.

## Where embeddings stop

Semantic recall fixed *wording*. It does **not** fix *correlation*. Each fact is an
independent point in vector space, with no idea that two facts are about the same thing or are
connected to each other. Watch it fail on a question that needs *two* facts joined:

> **"What city is my employer based in?"**

The answer requires connecting *"I work at Acme Corp"* to *"Acme is in Portland."* If you
embed that question, you will retrieve the **employer** fact (the nearest by meaning) — or
maybe the **city** fact — but the store can only give you ranked *individual* facts. It cannot
*follow* "Acme" from one fact to the other, because to a vector store "Acme Corp" in fact 1
and "Acme" in fact 2 are just two regions of space, not the same entity. You (or the model, in
context) must do the join yourself, and this becomes unreliable as memory grows and the
number of hops increases.

This is the exact boundary between **step 2** and **step 3** of the decision tree: independent
lookups → vectors; **correlated, multi-hop** recall → something relational. Unit 4 reviews the
evidence for crossing that boundary honestly, and Unit 5 builds the graph that does the join
*for* you.

One practical note for later: the choice is not vectors *or* graph. The strongest memory
systems keep embeddings **and** structure together — they find candidate entities by vector
similarity, then traverse their relationships. You will store a vector on every graph node in
Unit 6 for exactly this **hybrid** recall. Embeddings are not discarded; they become one half
of the retrieval.

---

> **Security:** Semantic recall will return whatever is in the store, including a fact that a
> previous (possibly malicious) turn added — and it will return it for any query that is
> merely *near* it in meaning, which is easier to trigger than an exact keyword match. What you
> embed, you make findable. Control what enters memory (Unit 8) and limit what a given query
> may retrieve (Unit 10); semantic reach makes both more important, not less.

## Challenges

1. **Beat the baseline, with numbers.** Run your five questions from Unit 2's challenge
   through semantic recall and compare the success rate. *Success:* a clear before/after
   number — and at least one question that semantics answers and keyword did not.
2. **Find where semantic recall is wrong.** Build a question that retrieves a *believable but
   wrong* fact (high cosine, wrong answer). *Success:* you can explain why "near in meaning" is
   not the same as "correct" — which is why we add reranking in Unit 7.
3. **Show the join gap.** Ask "what city is my employer in?" and show that top-1 (and even
   top-2) recall does not *connect* the employer and city facts. *Success:* you can state
   exactly which operation is missing — and which unit provides it.

## Recap

- Embedding facts and recalling by **cosine similarity** fixes Unit 2's main weakness: users
  can ask in **their own words**, not the stored words.
- For a set of **independent** facts, this *is* the answer — vector-store RAG (§19). Do not
  build more than this.
- Embeddings do not fix **correlation**: each fact is a separate point, so **multi-hop**
  questions ("employer → its city") cannot be answered by ranking individual facts.
- That boundary — independent lookup vs. correlated recall — is steps 2→3 of the decision
  tree, and the reason the course continues.
- Vectors are not discarded later; they become half of **hybrid** graph + vector recall.

## Next

**Unit 4 — Why a Graph:** before we use heavier tools, the honest case. When does a graph
actually beat strong vector recall — and when does it only cost you more tokens and latency for
no benefit? We will look at the real benchmark numbers and decide carefully.
