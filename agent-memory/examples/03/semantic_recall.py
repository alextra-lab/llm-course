"""
Unit 3 - Semantic recall: embed the same facts and retrieve by MEANING, not keyword.
Reuses the foundations embed + cosine helpers (Sections 19-20). Needs EMBED_MODEL; skips
cleanly without it, so the unit still reads.

    python agent-memory/examples/03/semantic_recall.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[3] / "examples"))  # foundations examples
from common import get_embed_client, EMBED_MODEL

# The SAME facts Unit 2 stored in SQLite -- now we'll recall them by meaning.
FACTS = [
    "I work at Acme Corp as a data engineer.",
    "We just moved the team to Portland.",
    "My favorite language is Python.",
    "I'm allergic to shellfish, by the way.",
    "The Q3 deadline got pushed to October.",
]


def embed(client, texts):
    r = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return np.array([d.embedding for d in r.data])


def cosine(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def main():
    if not EMBED_MODEL:
        print("EMBED_MODEL not set -- skipping (this unit needs embeddings). See Section 19.")
        return
    client = get_embed_client()
    fact_vecs = embed(client, FACTS)   # embed the memory ONCE, up front

    # The two queries that keyword search whiffed on in Unit 2 -- now by meaning.
    for query in ["where do I live?", "what foods should I avoid?"]:
        q = embed(client, [query])[0]
        best = max(range(len(FACTS)), key=lambda i: cosine(q, fact_vecs[i]))
        print(f"{query!r}\n  -> {FACTS[best]!r}\n")

    print("Semantics match 'live' to Portland and 'foods to avoid' to shellfish -- "
          "the recall Unit 2 couldn't do. But each fact is still an island (Unit 4).")


if __name__ == "__main__":
    main()
