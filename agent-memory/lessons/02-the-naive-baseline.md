---
title: 'The Naive Baseline'
linkTitle: '2. The Naive Baseline'
weight: 2
---

**Goal:** build the simplest memory that could possibly work — persist conversation turns
to **SQLite** and recall them by **recency** and **keyword** — and then deliberately *feel
it break*. This is the baseline every later piece improves on; you can't appreciate
embeddings (Unit 3) or a graph (Unit 5) until you've hit the wall this hits.

**Where this fits:** Unit 1 named the kinds of memory; this is the first persistent store.
It needs **no endpoint and no Docker** — just Python's standard-library `sqlite3` — so it
runs anywhere. We're building **semantic/episodic** memory the crudest way on purpose.

> **Why start crude?** The house rule (foundations course): see the raw mechanic before the
> abstraction. A keyword-search-over-SQLite memory is something you fully understand in ten
> lines — which means when it fails, you'll know *exactly why*, and what the next tool fixes.

---

## Persist the turns

Cross-session memory is, at bottom, *writing facts down so a later session can read them*.
The humblest version: a table of turns. Create **`work/sqlite_memory.py`**:

```python
import sqlite3

TURNS = [
    ("user", "I work at Acme Corp as a data engineer."),
    ("user", "We just moved the team to Portland."),
    ("user", "My favorite language is Python."),
    ("user", "I'm allergic to shellfish, by the way."),
    ("user", "The Q3 deadline got pushed to October."),
]


def init_db():
    db = sqlite3.connect(":memory:")   # a real app uses a file; :memory: keeps the demo clean
    db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, role TEXT, text TEXT, ts INTEGER)")
    for ts, (role, text) in enumerate(TURNS):
        db.execute("INSERT INTO turns(role, text, ts) VALUES (?, ?, ?)", (role, text, ts))
    db.commit()
    return db
```

That's persistence. Swap `:memory:` for a filename and these turns survive the process —
which is the entire difference between context (§12) and memory.

## Recall: recency and keyword

Two cheap ways to get facts back out. **Recency** — the last few turns — and **keyword** —
rows containing a search term:

```python
def recall_recent(db, k=3):
    rows = db.execute("SELECT text FROM turns ORDER BY ts DESC LIMIT ?", (k,)).fetchall()
    return [r[0] for r in rows]


def recall_keyword(db, term, k=3):
    rows = db.execute(
        "SELECT text FROM turns WHERE text LIKE ? ORDER BY ts DESC LIMIT ?",
        (f"%{term}%", k),                # the term is BOUND, never f-string'd into the SQL
    ).fetchall()
    return [r[0] for r in rows]
```

```bash
python work/sqlite_memory.py
```

```
recent 3:            ['The Q3 deadline got pushed to October.', "I'm allergic to shellfish, by the way.", 'My favorite language is Python.']
keyword 'Portland':  ['We just moved the team to Portland.']
```

So far so good — when the word you search for is *literally in the text*, keyword recall
finds it. *(Reference: [`examples/02/sqlite_memory.py`](../examples/02/sqlite_memory.py).)*

## Now feel it break

Ask the questions a real user asks, in the words a real user uses:

```
keyword 'live':     []   <- nothing, though "moved the team to Portland" is right there
keyword 'seafood':  []   <- nothing; "shellfish" never literally says "seafood"
```

Both whiff. "Where do I **live**?" should return Portland; "what **seafood** am I allergic
to?" should return shellfish. Keyword search can't, because it matches **strings, not
meaning** — and users almost never recall a fact in the exact words it was stored. This is
the core failure: a memory you can only query with the words already in it is barely a
memory at all.

Two more cracks you'll feel as the table grows:

- **No dedup.** Say "I work at Acme" in three sessions and you get three rows. The store
  has no notion that they're the *same fact* — the seed of the dedup problem (Unit 6).
- **No relationships.** "Alex works at Acme" and "Acme is in Portland" are two unrelated
  rows. Ask "what city is my employer in?" and there's no way to *join* them — the gap a
  graph eventually fills (Units 4–5).

This baseline isn't wrong — recency and keyword are genuinely useful and you'll keep them as
*part* of a hybrid system. It's just not *enough*. Each later unit is a named answer to one
of these cracks.

The tiered-store idea has pedigree: **MemGPT** (Packer et al., 2023; arXiv:2310.08560)
frames agent memory like an operating system's virtual memory — a small fast "context" tier
backed by a large external store, with the agent **paging** facts in and out as needed. Our
SQLite table is that external store in its most basic form; the rest of the course is better
policies for deciding *what* to page in.

---

> **Security:** Even here, the discipline matters: the search term is a **bound parameter**
> (`LIKE ?`), never formatted into the SQL. F-stringing user input into a query is SQL
> injection (foundations §16) — and a memory store is *full* of user input. Build the habit
> now; it's the same habit that keeps Cypher safe in Unit 5.

## Challenges

1. **Make it survive.** Change `:memory:` to a file path, run twice, and confirm the turns
   from the first run are recalled in the second. *Success:* you can state in one sentence
   why this is "memory" and the §12 history wasn't.
2. **Quantify the whiff.** Write five natural-language questions about the stored facts and
   count how many keyword recall answers correctly. *Success:* a hit-rate number you can
   compare against Unit 3's semantic recall on the *same* questions.
3. **Watch it duplicate.** Insert "I work at Acme Corp" three times and show recall returns
   the same fact thrice. *Success:* you can articulate why dedup needs a notion of *identity*
   the row store doesn't have.

## Recap

- Cross-session memory is **persisting facts so a later session can read them** — a SQLite
  table is the minimum viable version (and needs no services).
- **Recency** and **keyword** recall are cheap and useful, but keyword matches **strings,
  not meaning** — it misses any fact a user phrases differently than it was stored.
- The baseline also has **no dedup** and **no relationships** — the cracks Units 6 and 4–5
  fill.
- **MemGPT**'s OS-style paging is the mental model: a small context tier over a large
  external store, with a policy for what to page in.
- Bind your query parameters even in a toy store — it's user input all the way down.

## Next

**Unit 3 — Semantic Recall with Embeddings:** we fix the biggest crack first. Embed the
facts and retrieve by **meaning**, so "where do I live?" finds Portland and "what foods
should I avoid?" finds shellfish — the exact questions keyword recall just failed.
