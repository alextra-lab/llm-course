"""
Section 19 - Retrieval-Augmented Generation: answer from YOUR documents.

The model doesn't know your private or fresh data, and if you ask anyway it may
make something up. RAG fixes this: embed your documents, retrieve the few most
relevant to the question (Section 18), put them in the prompt, and tell the model
to answer ONLY from that context.

    python examples/19/rag.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, get_embed_client, MODEL, EMBED_MODEL

if not EMBED_MODEL:
    raise SystemExit("Set EMBED_MODEL in your .env (see examples/18/embed.py).")

client = get_client()              # chat
embed_client = get_embed_client()  # embeddings (same endpoint unless EMBED_BASE_URL is set)

# A tiny knowledge base about a made-up company (so the model can't "already know").
DOCS = [
    "Acme Corp's return policy allows returns within 30 days with a receipt.",
    "Acme Corp was founded in 1987 in Portland, Oregon.",
    "Acme Corp's warranty covers manufacturing defects for 2 years.",
    "Acme Corp ships to the US and Canada only.",
    "The Acme widget weighs 1.2 kilograms and comes in blue or red.",
]


def embed(texts):
    r = embed_client.embeddings.create(model=EMBED_MODEL, input=texts)
    return np.array([d.embedding for d in r.data])


def cosine(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


DOC_VECS = embed(DOCS)   # embed the corpus ONCE, up front


def retrieve(query, k=2):
    q = embed([query])[0]
    scored = sorted(((cosine(q, DOC_VECS[i]), DOCS[i]) for i in range(len(DOCS))),
                    reverse=True)
    return [doc for _, doc in scored[:k]]


def answer(query):
    context = "\n".join(f"- {c}" for c in retrieve(query))
    prompt = (
        "Answer the question using ONLY the context below. "
        'If the context does not contain the answer, say "I don\'t know".\n\n'
        f"Context:\n{context}\n\nQuestion: {query}"
    )
    r = client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": prompt}], temperature=0
    )
    return r.choices[0].message.content, context


# 1. A question the documents DO answer -> grounded answer.
q1 = "How long is Acme's warranty?"
a1, ctx1 = answer(q1)
print(f"Q: {q1}\nretrieved:\n{ctx1}\nA: {a1}\n")

# 2. A question the documents DON'T answer -> the model should decline, not invent.
q2 = "Who is Acme's CEO?"
a2, _ = answer(q2)
print(f"Q: {q2}\nA: {a2}")
print("\n(With grounding, #2 should say 'I don't know' instead of hallucinating.)")
