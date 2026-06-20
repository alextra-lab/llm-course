"""Unit 8 - Hysteresis: Dedup & Promotion

What this shows:
- a deterministic proposal fingerprint (the shape of personal_agent's dedup.py): normalize the
  text (lowercase, drop stopwords, SORT tokens for order-independence), then hash
  category:scope:normalized — so "add a retry budget" and "budget the retries" collapse to one;
- merging duplicates into a single proposal with an incrementing seen_count;
- promotion as hysteresis: only act on a proposal once it has RECURRED (seen_count) and MATURED
  (age) — don't change behaviour on a single fresh observation.

Run (pure Python, no endpoint needed):
    python examples/08/hysteresis_dedup.py
"""

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common_loops import Trace, log_event  # noqa: E402

STOPWORDS = frozenset("a an the to of in for on with and or but not this that it its we our add".split())
_WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, drop stopwords, SORT tokens — order-independent."""
    tokens = [t for t in _WORD.findall(text.lower()) if t not in STOPWORDS]
    return " ".join(sorted(set(tokens)))


def fingerprint(category: str, scope: str, what: str) -> str:
    """sha256(category:scope:normalized_what)[:16] — duplicates share a fingerprint (dedup.py)."""
    return hashlib.sha256(f"{category}:{scope}:{normalize(what)}".encode()).hexdigest()[:16]


@dataclass
class Proposal:
    what: str
    seen_count: int
    age_days: int  # days since first seen


# Reflections arriving over time as (category, scope, what, days_ago). The first three are the
# same idea reworded/reordered (they collapse); the next two are also one idea; the rest are
# one-offs. Note: the fingerprint merges reorderings, stopwords, and punctuation — NOT synonyms
# ("retry" != "retries"). Synonym-level dedup would need embeddings (the Phase-2 upgrade).
INCOMING = [
    ("reliability", "tools", "Add a retry budget for Elasticsearch queries", 9),
    ("reliability", "tools", "Elasticsearch retry budget for queries", 6),
    ("reliability", "tools", "Budget the retry on Elasticsearch queries", 2),
    ("ux", "tools", "Add a progress bar for long tool calls", 3),
    ("ux", "tools", "Progress bar for long tool calls", 1),
    ("cost", "llm_client", "Lower the summarizer temperature", 1),
    ("performance", "tools", "Parallelize the health probe", 10),
]

MIN_SEEN_COUNT = 2  # must recur — a single observation is noise
MIN_AGE_DAYS = 7  # must persist — don't act on something seen only in the last few days


def main() -> None:
    trace = Trace.new(kind="system:promotion")
    proposals: dict[str, Proposal] = {}

    # Dedup: collapse equivalent wordings into one proposal, counting recurrences.
    for category, scope, what, days_ago in INCOMING:
        fp = fingerprint(category, scope, what)
        if fp in proposals:
            p = proposals[fp]
            p.seen_count += 1
            p.age_days = max(p.age_days, days_ago)  # first_seen is the oldest sighting
            trace = log_event(trace, "proposal_merged", fingerprint=fp, seen_count=p.seen_count)
        else:
            proposals[fp] = Proposal(what=what, seen_count=1, age_days=days_ago)

    print(f"{len(INCOMING)} incoming reflections -> {len(proposals)} distinct proposals after dedup\n")
    for fp, p in proposals.items():
        print(f"  [{fp}] seen {p.seen_count}x, {p.age_days}d old: {p.what}")

    # Promotion = hysteresis: act only on the recurring AND matured proposals.
    print(f"\npromote if seen_count >= {MIN_SEEN_COUNT} AND age >= {MIN_AGE_DAYS}d:")
    for fp, p in proposals.items():
        promote = p.seen_count >= MIN_SEEN_COUNT and p.age_days >= MIN_AGE_DAYS
        if promote:
            trace = log_event(trace, "proposal_promoted", fingerprint=fp, seen_count=p.seen_count)
        reason = "PROMOTED" if promote else ("too few" if p.seen_count < MIN_SEEN_COUNT else "too new")
        print(f"  {reason:9} {p.what}")

    print("\nhysteresis: the recurring, matured pattern promotes; a single fresh idea waits.")


if __name__ == "__main__":
    main()
