---
title: 'The Naive Baseline'
linkTitle: '2. The Naive Baseline'
weight: 2
---

**Goal:** build the simplest memory that could work — persist conversation turns to
**SQLite** and recall them by **recency** and **keyword** — and then watch it fail on
purpose. This is the baseline that every later piece improves on. You cannot see the value of
embeddings (Unit 3) or a graph (Unit 5) until you reach the limit that this baseline reaches.

**Where this fits:** Unit 1 named the kinds of memory; this is the first persistent store. It
needs **no endpoint and no Docker** — only Python's standard-library `sqlite3` — so it runs
anywhere. We build **semantic/episodic** memory in the most basic way on purpose.

> **Why start so basic?** The rule from the foundations course: see the raw mechanism before
> the convenient abstraction. A keyword-search-over-SQLite memory is something you fully
> understand in ten lines. So when it fails, you will know *exactly why*, and what the next
> tool fixes.

---

## Persist the turns

Cross-session memory is, at its core, *writing facts down so that a later session can read
them*. The most basic version is a table of turns. Create **`work/sqlite_memory.py`**:

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

That is persistence. Replace `:memory:` with a filename and these turns survive the process
— which is the whole difference between context ([Foundations Lesson 13 — Conversation State & Memory](../../lessons/13-conversation-state-and-memory.md)) and memory.

## Recall: recency and keyword

Two cheap ways to get facts back out. **Recency** — the last few turns — and **keyword** —
rows that contain a search term:

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

This works when the word you search for is *literally in the text*: keyword recall finds it.
*(Reference: [`examples/02/sqlite_memory.py`](../examples/02/sqlite_memory.py).)*

## Now watch it fail

Ask the questions a real user asks, in the words a real user uses:

```
keyword 'live':     []   <- nothing, though "moved the team to Portland" is right there
keyword 'seafood':  []   <- nothing; "shellfish" never literally says "seafood"
```

Both fail. "Where do I **live**?" should return Portland; "what **seafood** am I allergic
to?" should return shellfish. Keyword search cannot do this, because it matches **strings,
not meaning** — and users almost never recall a fact in the exact words it was stored in.
This is the main failure: a memory you can only query with the words already inside it is
hardly a memory.

Two more problems appear as the table grows:

- **No deduplication.** Say "I work at Acme" in three sessions and you get three rows. The
  store has no idea that they are the *same fact* — the start of the deduplication problem
  (Unit 6).
- **No relationships.** "Alex works at Acme" and "Acme is in Portland" are two unrelated
  rows. Ask "what city is my employer in?" and there is no way to *join* them — the gap that
  a graph later fills (Units 4–5).

This baseline is not wrong. Recency and keyword are genuinely useful, and you will keep them
as *part* of a combined system. It is simply not *enough*. Each later unit is a named answer
to one of these problems.

The idea of a layered store is well established. **MemGPT** (Packer et al., 2023;
arXiv:2310.08560) describes agent memory like an operating system's virtual memory: a small,
fast "context" layer in front of a large external store, with the agent moving facts in and
out as needed (it calls this *paging*). Our SQLite table is that external store in its most
basic form. The rest of the course is about better rules for deciding *what* to bring into
context.

---

> **Security:** Even here, the discipline matters: the search term is a **bound parameter**
> (`LIKE ?`), never formatted into the SQL. Putting user input into a query with an f-string
> is SQL injection ([Foundations Lesson 17 — Sandboxing II: Containers & Postgres](../../lessons/17-sandboxing-containers-and-postgres.md)) — and a memory store is *full* of user input. Build the
> habit now; it is the same habit that keeps Cypher safe in Unit 5.

> **Observe:** This is the first thing you build, so it is the first thing you instrument. Emit
> one joinable `session_id`/`trace_id`/`step` line ([Foundations Lesson 10 — Observability & Logging](../../lessons/10-observability-and-logging.md)) per write and per recall —
> `operation="write"` or `"recall"`, with the search term and how many rows came back. That log
> answers the baseline question the next two units must beat: *for this query, did keyword recall
> surface the right fact, or nothing?* See the repo's [Observability Standard](../../OBSERVABILITY.md).

## Challenges

1. **Make it survive.** Change `:memory:` to a file path, run the script twice, and confirm
   the turns from the first run are recalled in the second. *Success:* you can say in one
   sentence why this is "memory" and the [Lesson 13](../../lessons/13-conversation-state-and-memory.md) history was not.
2. **Measure the failures.** Write five natural-language questions about the stored facts and
   count how many keyword recall answers correctly. *Success:* a success rate you can compare
   against Unit 3's semantic recall on the *same* questions.
3. **Watch it duplicate.** Insert "I work at Acme Corp" three times and show that recall
   returns the same fact three times. *Success:* you can explain why deduplication needs a
   notion of *identity* that the row store does not have.

## Recap

- Cross-session memory is **persisting facts so a later session can read them** — a SQLite
  table is the minimum version (and needs no services).
- **Recency** and **keyword** recall are cheap and useful, but keyword matches **strings, not
  meaning** — it misses any fact a user phrases differently than it was stored.
- The baseline also has **no deduplication** and **no relationships** — the problems that
  Units 6 and 4–5 solve.
- **MemGPT**'s OS-style paging is the model to keep in mind: a small context layer over a
  large external store, with a rule for what to bring in.
- Bind your query parameters even in a small demo store — it is all user input.

## Next

**[Unit 3 — Semantic Recall with Embeddings](03-semantic-recall-with-embeddings.md):** we fix the biggest problem first. Embed the
facts and retrieve by **meaning**, so "where do I live?" finds Portland and "what foods should
I avoid?" finds shellfish — the exact questions that keyword recall just failed.
