# Section 15 — Embeddings

**Goal:** turn text into **vectors** that capture meaning, and compare them by hand with
cosine similarity. You'll build a tiny semantic search — matching by *meaning*, not
keywords — which is the foundation for retrieval (Section 16) and a core building block
for search, clustering, and deduplication.

**Where this fits:** a change of gears from chat. Same server, different endpoint
(`/v1/embeddings`). We stay close to the metal: a vector is just a list of numbers, and
we compute similarity ourselves with numpy before any database enters the picture.

> **You need an embedding model.** Embeddings come from a *different* kind of model than
> the chat model — `gpt-oss-120b` generates text, not embeddings. Set **`EMBED_MODEL`**
> in your `.env` to an embedding model your endpoint serves. If your endpoint only serves
> the chat model, the scripts will tell you, and you can still read along.

---

## What is an embedding?

An **embedding** is a fixed-length list of numbers — a **vector** — that represents the
*meaning* of a piece of text. The key property: **similar meanings produce nearby
vectors**, even when the words differ. "A feline napped on the rug" and "The cat sat on
the mat" land close together; "We deployed the server" lands far away.

Get one from the embeddings endpoint. Create **`work/embed.py`**:

```python
from common import get_client, EMBED_MODEL

if not EMBED_MODEL:
    raise SystemExit("Set EMBED_MODEL in your .env to an embedding model.")

client = get_client()

response = client.embeddings.create(
    model=EMBED_MODEL,
    input=["The cat sat on the mat.", "A feline rested on the rug."],
)

vectors = [d.embedding for d in response.data]
print("vectors:", len(vectors), "| dimensions:", len(vectors[0]))
print("usage:", response.usage)
```

```bash
python work/embed.py
```

You'll see two vectors, each with hundreds or thousands of dimensions (the number depends
on the model). Each is just a point in a high-dimensional space. *(Reference:
[`examples/15/embed.py`](../examples/15/embed.py).)*

---

## Comparing vectors: cosine similarity

To ask "how similar are these two meanings?", measure the **angle** between their
vectors with **cosine similarity**: the dot product divided by the product of their
lengths. It ranges from **-1** (opposite) through **0** (unrelated) to **1** (identical
direction / meaning).

```python
def cosine(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
```

That's the whole idea behind semantic search: embed everything, then rank by cosine
similarity to the query.

### Build a tiny semantic search

Create **`work/similarity.py`**:

```python
import numpy as np
from common import get_client, EMBED_MODEL

client = get_client()

def embed(texts):
    r = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return np.array([d.embedding for d in r.data])

def cosine(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))

docs = [
    "The cat sat on the mat.",
    "Python is a popular programming language.",
    "A feline napped on the soft rug.",
    "We deployed the web server at noon.",
]
query = "a sleeping cat"

doc_vecs = embed(docs)
query_vec = embed([query])[0]

ranked = sorted(((cosine(query_vec, doc_vecs[i]), docs[i]) for i in range(len(docs))),
                reverse=True)
for score, doc in ranked:
    print(f"{score:.3f}  {doc}")
```

```bash
python work/similarity.py
```

The cat/feline sentences rank highest even though the query says neither "cat" nor "sat"
— the match is by **meaning**. A keyword search would miss "feline" entirely. *(Reference:
[`examples/15/similarity.py`](../examples/15/similarity.py).)*

---

## Why this matters

Embeddings are the engine behind a lot of practical AI:

- **Semantic search / retrieval** — find the most relevant chunks of text (Section 16).
- **Clustering** — group similar items.
- **Deduplication** — near-identical texts have near-identical vectors.
- **Classification** — nearest-labeled-example wins.

A few practical notes: embedding a batch (`input=[...]` with many strings) is far cheaper
than one call each; you pay per token here too (`usage`), and you typically **store** the
vectors so you embed each document only once.

> **Scaling up.** Comparing a query against four documents with a `for` loop is fine.
> Against four *million*, you'd use a **vector database** (FAISS, pgvector, Pinecone, …)
> that indexes vectors for fast nearest-neighbor search. Same cosine idea, optimized.
> We'll stay with the brute-force numpy version in Section 16 so the mechanics stay
> visible.

---

## Challenges

1. **Confirm the intuition.** Add the sentence "a dog barked loudly" to `docs` and query
   "a sleeping cat". *Success:* the dog sentence scores higher than the server sentence
   but lower than the cat ones.
2. **Self-similarity.** Embed one sentence twice and cosine them. *Success:* ~1.0.
3. **Batch vs loop.** Embed 10 sentences in one `create(input=[...])` call and compare
   `usage` to doing 10 separate calls. *Success:* you can state which is cheaper and why.

---

## Recap

- An **embedding** is a vector capturing meaning; similar meanings → nearby vectors.
- Get them from `/v1/embeddings` (a separate `EMBED_MODEL`); each call reports `usage`.
- Compare vectors with **cosine similarity** (−1…1); rank by it for **semantic search**.
- Batch your inputs, store vectors once, and reach for a vector DB at scale.

## Next

**Section 16 — Retrieval-Augmented Generation (RAG):** combine embeddings with the chat
model — retrieve the most relevant text for a question and feed it in, so the model
answers from *your* documents instead of guessing.
