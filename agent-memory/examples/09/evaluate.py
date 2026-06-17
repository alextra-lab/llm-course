"""
Unit 9 - Measure before you optimize: score memory retrieval with the standard metrics.

Units 7-8 are full of knobs -- decay rate, importance thresholds, top-k, hybrid weights.
You cannot tune what you cannot measure. This unit builds the four metrics every retrieval
evaluation uses, over a small LABELED set (queries + which memories are actually relevant):

  - recall@k     -- of the relevant memories, how many made the top k?
  - precision@k  -- of the top k, how many were relevant?
  - MRR          -- how high was the FIRST relevant memory? (mean reciprocal rank)
  - nDCG@k       -- rank-weighted gain: relevant items higher up score more.

These are exactly the metrics behind long-term-memory benchmarks like LoCoMo (Maharana et al.,
ACL 2024) and LongMemEval (Wu et al., ICLR 2025) -- which show that even strong systems lag on
multi-session recall, so measuring on YOUR data is not optional.

The metrics are pure functions and always run (no database needed). An OPT-IN second half scores
a real graph retrieval if NEO4J_URI is set; without it, that part skips and the metrics demo
above still makes the whole point.

    python agent-memory/examples/09/evaluate.py
"""

import math
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # agent-memory/examples
from common_graph import get_graph


# --- the metrics (pure functions over a ranked list of ids + the set of relevant ids) -------
def recall_at_k(ranked, relevant, k):
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def precision_at_k(ranked, relevant, k):
    return len(set(ranked[:k]) & relevant) / k


def reciprocal_rank(ranked, relevant):
    for i, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / i
    return 0.0


def dcg_at_k(ranked, relevant, k):
    # binary gain (1 if relevant), discounted by log2 of the rank -- lower ranks count less.
    return sum(1.0 / math.log2(i + 1) for i, item in enumerate(ranked[:k], start=1)
               if item in relevant)


def ndcg_at_k(ranked, relevant, k):
    # normalize by the BEST possible ordering (all relevant items first), so 1.0 = perfect.
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg_at_k(ranked, relevant, k) / ideal if ideal else 0.0


# --- a tiny labeled set: query -> the memory ids that SHOULD be retrieved --------------------
# Each "ranked" list is what some retriever returned, best first. Hand-built here to show how
# the metrics react; in practice these rankings come from your real search_memory (Unit 7).
GOLD = [
    # query              relevant ids        a retriever's ranked output (best first)
    ("where I work",     {"acme"},           ["acme", "portland", "python"]),
    ("my allergy",       {"shellfish"},      ["python", "portland", "shellfish"]),  # relevant is 3rd
    ("my deadlines",     {"q3", "acme"},     ["q3", "python", "acme"]),             # 2 relevant, split
]
K = 3


def evaluate(gold, k):
    rows = []
    for query, relevant, ranked in gold:
        rows.append((query,
                     recall_at_k(ranked, relevant, k),
                     precision_at_k(ranked, relevant, k),
                     reciprocal_rank(ranked, relevant),
                     ndcg_at_k(ranked, relevant, k)))
    return rows


def report(rows, k):
    print(f"{'query':<14} {'recall@'+str(k):>9} {'prec@'+str(k):>8} {'MRR':>6} {'nDCG@'+str(k):>8}")
    for q, rec, prec, rr, nd in rows:
        print(f"{q:<14} {rec:>9.2f} {prec:>8.2f} {rr:>6.2f} {nd:>8.2f}")
    n = len(rows)
    print(f"{'MEAN':<14} {sum(r[1] for r in rows)/n:>9.2f} {sum(r[2] for r in rows)/n:>8.2f} "
          f"{sum(r[3] for r in rows)/n:>6.2f} {sum(r[4] for r in rows)/n:>8.2f}")


# --- OPT-IN: score a real graph retrieval (reuses the Unit 5-7 graph shape) ------------------
SEED = """
MERGE (a:Entity {id:'alex',      name:'Alex'})        ON CREATE SET a.importance=8
MERGE (c:Entity {id:'acme',      name:'Acme Corp'})   ON CREATE SET c.importance=7
MERGE (s:Entity {id:'shellfish', name:'shellfish'})   ON CREATE SET s.importance=9
MERGE (p:Entity {id:'portland',  name:'Portland'})    ON CREATE SET p.importance=4
MERGE (q:Entity {id:'q3',        name:'Q3 deadline'}) ON CREATE SET q.importance=6
MERGE (y:Entity {id:'python',    name:'Python'})      ON CREATE SET y.importance=5
"""


def graph_retrieve(driver, k):
    """A stand-in retriever: rank entity ids by importance (deterministic, no endpoint needed).
    The point is not the ranker -- it's that you can score WHATEVER your real retriever returns."""
    records, _, _ = driver.execute_query(
        "MATCH (e:Entity) RETURN e.id AS id ORDER BY e.importance DESC LIMIT $k", k=k)
    return [r["id"] for r in records]


def main():
    print("metrics over a fixed labeled set:")
    report(evaluate(GOLD, K), K)
    print("\nRead the rows: 'my allergy' has recall@3=1.0 (shellfish is in the top 3) but MRR=0.33\n"
          "(it's ranked 3rd) -- the same retrieval looks good or bad depending on the metric.\n"
          "That is why you report several, and why you measure before you tune.")

    driver = get_graph()
    if driver is None:
        return   # the metrics demo above already made the point; the graph half is optional
    with driver:
        driver.execute_query(SEED)
        ranked = graph_retrieve(driver, K)
        # Score that real ranking against a label. The ranker sorts by importance only, so the
        # high-importance allergy and the user node sit above the actually-relevant employer.
        relevant = {"acme"}   # gold for the query "where do I work?"
        print(f"\ngraph retrieval ranked (by importance): {ranked}")
        print(f"for query 'where do I work?' (relevant={relevant}): "
              f"recall@{K}={recall_at_k(ranked, relevant, K):.2f} "
              f"MRR={reciprocal_rank(ranked, relevant):.2f}")
        print("Recall@3 is fine, but MRR is low -- importance-only ranking buries the employer "
              "below higher-importance facts. A relevance-aware ranker (Unit 7) would lift it.\n"
              "That gap is exactly what a measured score exposes before you ship.")


if __name__ == "__main__":
    main()
