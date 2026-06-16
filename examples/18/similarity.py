"""
Section 18 - Embeddings: cosine similarity and semantic search, by hand.

We embed a few documents and a query, then rank the documents by COSINE
SIMILARITY to the query -- the standard way to compare embedding vectors. No
vector database, just numpy, so you see the actual math.

    python examples/18/similarity.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, EMBED_MODEL

if not EMBED_MODEL:
    raise SystemExit("Set EMBED_MODEL in your .env (see examples/18/embed.py).")

client = get_client()


def embed(texts):
    response = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return np.array([d.embedding for d in response.data])


def cosine(a, b):
    # cosine similarity = how aligned two vectors are, from -1 to 1 (1 = identical meaning)
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

scored = sorted(
    ((cosine(query_vec, doc_vecs[i]), docs[i]) for i in range(len(docs))),
    reverse=True,
)

print(f"query: {query!r}\n")
for score, doc in scored:
    print(f"  {score:.3f}  {doc}")

print("\nThe cat/feline sentences rank highest -- the match is by MEANING, not words.")
