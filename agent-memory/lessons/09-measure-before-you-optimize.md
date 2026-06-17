---
title: 'Measure Before You Optimize'
linkTitle: '9. Measure Before You Optimize'
weight: 9
---

**Goal:** put a number on how good your memory recall actually is. Units 7 and 8 added knobs —
decay rate, importance thresholds, top-*k*, hybrid weights — and every one changes what gets
retrieved. Without measurement, tuning them is guessing. This unit builds the four standard
retrieval metrics — **recall@k**, **precision@k**, **MRR**, and **nDCG@k** — over a small
**labeled** set, so the choices from the last two units become measured, not asserted.

**Where this fits:** this is the discipline that holds the whole course honest. Unit 4 said
graph retrieval does *not* universally beat vector search — it depends on your data and queries.
The only way to know which side of that line *you* are on is to measure recall on *your* queries.
The metrics here are the same ones the field's long-term-memory benchmarks report.

> **No database required.** The metrics are pure functions over a ranked list, so the main demo
> always runs. An opt-in second half scores a real graph retrieval if `NEO4J_URI` is set.

---

## A labeled set is the prerequisite

You cannot measure recall without knowing the right answer. So the first artifact is a small
**labeled set**: a list of queries, and for each, the set of memory items that *should* be
retrieved. This is the unglamorous, essential work — a few dozen hand-labeled queries from real
conversations are worth more than any amount of intuition.

```python
GOLD = [
    # query           relevant ids     a retriever's ranked output (best first)
    ("where I work",  {"acme"},         ["acme", "portland", "python"]),
    ("my allergy",    {"shellfish"},    ["python", "portland", "shellfish"]),   # relevant is 3rd
    ("my deadlines",  {"q3", "acme"},   ["q3", "python", "acme"]),
]
```

The `ranked` list is what your retriever returned, best first; the metrics compare it to the
`relevant` set. Here the rankings are hand-built to show how the metrics behave; in practice they
come from your real `search_memory` (Unit 7).

## The four metrics

Each is a small, pure function over the ranked list and the relevant set. They answer different
questions, and that is the point — a single number hides too much.

```python
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
    return sum(1.0 / math.log2(i + 1) for i, item in enumerate(ranked[:k], start=1)
               if item in relevant)


def ndcg_at_k(ranked, relevant, k):
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg_at_k(ranked, relevant, k) / ideal if ideal else 0.0
```

- **recall@k** — did the relevant items make it into the top *k* at all? This is usually the
  metric that matters most for memory: if the fact is not in the retrieved set, it cannot reach
  the prompt, and the agent answers as if it never knew.
- **precision@k** — of the *k* you retrieved, how many were relevant? Low precision means you are
  spending prompt budget (Unit 7) on noise.
- **MRR** (mean reciprocal rank) — how high was the *first* relevant item? `1/rank`. Rewards
  putting a good answer at the top.
- **nDCG@k** — rewards relevant items appearing *higher* in the ranking, normalized so a perfect
  ordering scores 1.0. The most complete single number when rank order matters.

Run the example and the reason for reporting several becomes obvious:

```
query           recall@3   prec@3    MRR   nDCG@3
where I work        1.00     0.33   1.00     1.00
my allergy          1.00     0.33   0.33     0.50
my deadlines        1.00     0.67   1.00     0.92
MEAN                1.00     0.44   0.78     0.81
```

Look at **my allergy**: `recall@3 = 1.0` looks perfect — the allergy *is* in the top 3. But
`MRR = 0.33` reveals it was ranked *third*, behind two irrelevant facts. Same retrieval, two very
different stories. If you reported only recall you would call this a success and never fix the
ranking. That is why you report several metrics, and why you **measure before you tune**: the
metric you pick decides which problems you can even see.

```bash
python work/evaluate.py
```

*(Reference: [`examples/09/evaluate.py`](../examples/09/evaluate.py).)*

## Scoring your real retrieval

The metrics do not care where the ranking came from. Point them at the graph and you can score
the actual retriever you built. The opt-in half of the example ranks entities by importance alone
— deliberately naive — and scores it:

```
graph retrieval ranked (by importance): ['shellfish', 'alex', 'acme']
for query 'where do I work?' (relevant={'acme'}): recall@3=1.00 MRR=0.33
```

The employer is in the top 3 (recall is fine) but buried at rank 3 behind two higher-importance
facts (MRR is low). That is a concrete, measured gap: the relevance-aware ranker from Unit 7
would lift the employer up. You now have a number to improve, and a way to tell whether a change
to the decay rate or the hybrid weights actually helped — instead of changing things and hoping.

## What the benchmarks teach

These metrics are the foundation of the field's long-term-memory benchmarks, and the benchmarks
are worth knowing because they map the failure modes you will hit:

- **LoCoMo** (Maharana et al., *ACL* 2024) builds very long conversations — about 600 turns over
  up to 32 sessions — and shows that models struggle most with **temporal and causal** reasoning
  across sessions, not single-fact lookup. If your agent's job is "what changed since last month,"
  that is the hard part to measure.
- **LongMemEval** (Wu et al., *ICLR* 2025) tests five distinct abilities — information extraction,
  multi-session reasoning, temporal reasoning, knowledge updates, and **abstention** (knowing when
  it does *not* know) — across 500 questions, and reports that even commercial assistants drop
  around 30% in accuracy over sustained interaction.

Two honest lessons follow. First, **a single accuracy number hides distinct skills**; build a
labeled set that separates them (lookup vs. multi-hop vs. temporal vs. "should not answer"), the
way these benchmarks do. Second, recall (and the Unit 4 "do graphs even help here" question) is
**conditional on your data** — so measure on your own queries before you trust any headline result,
including this course's.

---

> **Don't optimize a number into the ground.** Metrics guide tuning; they do not replace judgment.
> A retriever can score well on a stale labeled set and still fail on the queries users actually
> ask next month — so refresh the set, and watch precision *and* recall together (it is easy to
> win one by sacrificing the other). The goal is a memory the user trusts, not a leaderboard.

> **Observe:** The metrics here are not a separate machinery — recall@k, MRR, and nDCG are
> computed *from* the recalls you have been logging since Unit 2 (foundations §9), scored against
> your labeled ids. The joinable line is the raw data; the metric is the summary. That closes the
> loop the whole course turns on: *did this change to recall actually raise the number, or did it
> only feel better?*

## Challenges

1. **Find the precision/recall trade-off.** Raise *k* and watch recall rise while precision falls.
   *Success:* you can pick a *k* for your data and justify it with the two numbers.
2. **Label your own set.** Pull ten real queries, write down the relevant memory ids by hand, and
   run the metrics over your Unit 7 `search_memory`. *Success:* you have a mean nDCG for your real
   retriever and can say which queries drag it down.
3. **Measure a tuning change.** Score the importance-only ranker, then the Unit 7 recency × importance
   × relevance ranker, on the same labeled set. *Success:* you can state, with numbers, whether the
   hybrid ranker actually helped — and by how much.

## Recap

- You **cannot tune what you cannot measure.** A small **labeled set** (queries → relevant ids) is
  the prerequisite for every metric here.
- Report **several metrics**: recall@k (did it surface at all?), precision@k (how much noise?), MRR
  (how high was the first hit?), nDCG@k (rank-weighted). One number hides too much — the allergy
  scored recall 1.0 but MRR 0.33.
- Point the metrics at your **real retrieval** to turn vague "is it good?" into a number you can
  improve, and to tell whether a tuning change actually helped.
- **LoCoMo** and **LongMemEval** show the hard parts are multi-session, temporal, and abstention —
  and that recall is **conditional on your data**, so measure on your own queries.

## Next

**Unit 10 — Observability & Privacy:** measurement tells you *how well* memory works; observability
tells you *what it did and for whom*. Next you make memory access **joinable** in your telemetry,
add **visibility scopes** and PII handling, and close the **Cypher injection** door for good.
