"""
Section 12 - Prompt engineering: delimiters and output shaping.

Two high-leverage habits:
  1. Wrap the user-supplied text in clear DELIMITERS so the model can tell your
     instructions apart from the data (this also matters for safety -- Section 21).
  2. Specify the OUTPUT FORMAT precisely instead of hoping.

    python examples/12/structure.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from common import get_client, MODEL

client = get_client()

document = (
    "The library opens at 9am on weekdays and 10am on weekends. Members can borrow "
    "up to 10 books for 3 weeks. Late returns are fined 20 cents per day."
)

prompt = f"""Summarize the document between <doc> tags as exactly 3 bullet points.
Each bullet must be under 10 words. Output only the bullets, no preamble.

<doc>
{document}
</doc>"""

response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": prompt}],
    temperature=0,
)
print(response.choices[0].message.content)
print("\nThe <doc> delimiters separate instructions from data; the format rules")
print("('exactly 3 bullets', 'under 10 words', 'only the bullets') shape the output.")
