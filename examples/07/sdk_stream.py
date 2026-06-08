"""
Section 7 - Streaming: the SDK way.

Same stream, no protocol parsing. Set stream=True and iterate; each chunk carries
a delta. We also ask for usage at the end with stream_options -- normally a
streamed response doesn't include the usage block.

    python examples/07/sdk_stream.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

stream = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "Count from 1 to 10 slowly."}],
    stream=True,
    stream_options={"include_usage": True},  # ask for a final usage chunk
)

print("=== streaming via the SDK ===")
pieces = []
usage = None
for chunk in stream:
    if chunk.usage is not None:
        usage = chunk.usage  # arrives in the final chunk
    if not chunk.choices:
        continue
    piece = chunk.choices[0].delta.content
    if piece:
        pieces.append(piece)
        print(piece, end="", flush=True)

# If you need the whole answer as one string, reassemble the deltas.
full_text = "".join(pieces)
print("\n\n=== reassembled length ===", len(full_text), "chars")
print("=== usage ===", usage)
