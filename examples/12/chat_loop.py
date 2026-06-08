"""
Section 12 - Conversation state: the API is stateless; YOU keep the history.

The server remembers nothing between calls. To hold a conversation you keep a
`messages` list and resend the WHOLE thing every turn, appending each reply.
This script runs a scripted three-turn conversation (no typing needed) and
prints prompt_tokens each turn so you can watch the history -- and its cost --
grow.

    python examples/12/chat_loop.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

# The running history. We will append to it every turn.
history = [{"role": "system", "content": "You are a concise travel assistant."}]

user_turns = [
    "I'm planning a trip to Japan.",
    "What's the best season to visit?",      # 'visit' relies on remembering Japan
    "And what should I pack for that season?" # relies on remembering the season
]

for turn in user_turns:
    history.append({"role": "user", "content": turn})

    response = client.chat.completions.create(model=MODEL, messages=history)
    reply = response.choices[0].message.content

    # IMPORTANT: append the assistant reply so the next turn has the context.
    history.append({"role": "assistant", "content": reply})

    print(f"USER: {turn}")
    print(f"ASSISTANT: {reply}")
    print(f"   (prompt_tokens this turn: {response.usage.prompt_tokens})\n")

print("Notice prompt_tokens climbs every turn -- you resend (and pay for) the")
print("entire history each time. That's what Section 12 is about managing.")
