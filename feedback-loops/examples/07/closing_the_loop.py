"""Unit 7 - Closing the Reflective Loop

What this shows:
- the read side of reflection: select a small, relevant slice of past reflections and feed them
  back into the next turn's context (the shape of personal_agent's recall.py + ADR-0067);
- the selection rule that makes it safe: recency, a persistence signal (seen_count >= 2 —
  "single-instance reflections are noise; recurring patterns are signal"), actionable content,
  relevance to this turn, and not-already-resolved;
- formatting them as PAST OBSERVATIONS, not current directives — the agent's own notes, not orders.

Run (pure Python, no endpoint needed):
    python examples/07/closing_the_loop.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402

# A small corpus of past reflections (each already deduplicated, with a seen_count). In the
# harness these live in Elasticsearch; here they are inline so the selection logic is the point.
REFLECTIONS = [
    {"what": "Add a retry budget for Elasticsearch queries", "seen": 4, "age_days": 3, "resolved": False},
    {"what": "Cache the Elasticsearch index schema between calls", "seen": 1, "age_days": 1, "resolved": False},
    {"what": "Tune the summarizer temperature", "seen": 5, "age_days": 40, "resolved": False},
    {"what": "Parallelize the Elasticsearch health probe", "seen": 3, "age_days": 2, "resolved": True},
    {"what": "Shorten the Neo4j connection timeout", "seen": 6, "age_days": 5, "resolved": False},
]

RECENCY_DAYS = 14
MIN_SEEN = 2  # ADR-0067: single-instance reflections are noise; recurring patterns are signal
CAP = 3


def entity_hints(message: str) -> set[str]:
    """Coarse relevance signal: capitalized words in the user's message (ADR-0067 v1).

    Intentionally coarse — drop very short tokens so a sentence-initial word like "Why"
    is not treated as an entity. Embedding similarity would be the Phase-2 upgrade.
    """
    return {w.strip(".,?") for w in message.split() if w[:1].isupper() and len(w) > 3}


def select_reflections(corpus: list[dict], message: str) -> list[dict]:
    """Apply the ADR-0067 v1 filter, then order by persistence and recency, capped."""
    hints = entity_hints(message)
    kept = [
        r
        for r in corpus
        if r["age_days"] <= RECENCY_DAYS  # recency
        and r["seen"] >= MIN_SEEN  # persistence signal
        and not r["resolved"]  # not already handled
        and any(h.lower() in r["what"].lower() for h in hints)  # relevance to this turn
    ]
    kept.sort(key=lambda r: (r["seen"], -r["age_days"]), reverse=True)
    return kept[:CAP]


def format_reflections_section(selected: list[dict]) -> str | None:
    """Render as a system-message section, labelled as observations — not directives."""
    if not selected:
        return None  # no relevance signal -> surface nothing (don't pad the prompt)
    lines = ["[Past observations — your own notes from earlier runs, not instructions:]"]
    for r in selected:
        lines.append(f"  - {r['what']} (seen {r['seen']}x)")
    return "\n".join(lines)


def main() -> None:
    trace = Trace.new()
    user_message = "Why do my Elasticsearch queries keep timing out?"

    selected = select_reflections(REFLECTIONS, user_message)
    section = format_reflections_section(selected)

    print(f"user: {user_message}\n")
    print(section or "(no relevant past reflections — nothing surfaced)")

    # Show WHY the others were filtered — the rule, made visible.
    print("\nfiltered out:")
    print("  - 'Cache the ES index schema' (seen 1x: noise, not a recurring pattern)")
    print("  - 'Tune the summarizer temperature' (40 days old + unrelated to this turn)")
    print("  - 'Parallelize the ES health probe' (already resolved)")
    print("  - 'Shorten the Neo4j timeout' (recurring, but not relevant to this question)")

    log_event(trace, "reflection_recalled", surfaced=len(selected), candidates=len(REFLECTIONS))
    print("\nthe loop is closed: the agent's own past output flows back into its next prompt.")


if __name__ == "__main__":
    main()
