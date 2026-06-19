"""
Section 19 - Embeddings: turn text into a vector.

An embedding is a list of numbers (a vector) that captures the MEANING of a piece
of text. Similar meanings -> nearby vectors. We get them from the /v1/embeddings
endpoint -- usually served by a DIFFERENT model than the chat model, so this uses
EMBED_MODEL from your .env.

    python examples/19/embed.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_embed_client, EMBED_MODEL

if not EMBED_MODEL:
    raise SystemExit(
        "Set EMBED_MODEL in your .env to an embedding model your endpoint serves. "
        "(gpt-oss-120b is a chat model and usually won't produce embeddings.)"
    )

client = get_embed_client()

response = client.embeddings.create(
    model=EMBED_MODEL,
    input=["The cat sat on the mat.", "A feline rested on the rug."],
)

vectors = [d.embedding for d in response.data]
print("number of vectors:", len(vectors))
print("dimensions per vector:", len(vectors[0]))
print("first 5 numbers of vector 0:", [round(x, 4) for x in vectors[0][:5]])
print("usage:", response.usage)
