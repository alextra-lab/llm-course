"""
Section 12 - Keeping history inside the budget: windowing and summarizing.

As a conversation grows, the resent history eats more of the context window
(Section 3) and costs more (Section 10). Two common fixes:
  1. Sliding window  -- keep the system message + the last N turns.
  2. Summarize       -- replace old turns with a short model-written summary.

    python examples/12/trim_history.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()


def sliding_window(history, keep_turns=4):
    """Keep the system message plus the last `keep_turns` user/assistant messages."""
    system = [m for m in history if m["role"] == "system"]
    rest = [m for m in history if m["role"] != "system"]
    return system + rest[-keep_turns:]


def summarize(messages):
    """Compress a list of messages into one short summary string."""
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content":
                   "Summarize this conversation in 2 sentences, keeping key facts:\n"
                   + transcript}],
        max_tokens=120,
    )
    return r.choices[0].message.content.strip()


# A pretend long history.
history = [{"role": "system", "content": "You are a helpful assistant."}]
for i in range(1, 7):
    history.append({"role": "user", "content": f"Question number {i}?"})
    history.append({"role": "assistant", "content": f"Answer number {i}."})

print(f"full history: {len(history)} messages")

windowed = sliding_window(history, keep_turns=4)
print(f"windowed:     {len(windowed)} messages (system + last 4)")

# Summarize everything except the last 2 turns, then keep the summary + recent turns.
old, recent = history[1:-2], history[-2:]
summary = summarize(old)
compacted = (history[:1]
             + [{"role": "system", "content": "Earlier conversation summary: " + summary}]
             + recent)
print(f"summarized:   {len(compacted)} messages")
print("summary:", summary)
