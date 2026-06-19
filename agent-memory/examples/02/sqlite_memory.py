"""
Unit 2 - The naive baseline: persist conversation turns to SQLite, recall by recency and
keyword -- then FEEL it break. No external services and no endpoint: stdlib sqlite3 only,
so this whole unit runs anywhere.

    python agent-memory/examples/02/sqlite_memory.py
"""

import sqlite3

# A handful of turns from past sessions -- what we'd have persisted as conversation flowed.
TURNS = [
    ("user", "I work at Acme Corp as a data engineer."),
    ("user", "We just moved the team to Portland."),
    ("user", "My favorite language is Python."),
    ("user", "I'm allergic to shellfish, by the way."),
    ("user", "The Q3 deadline got pushed to October."),
]


def init_db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, role TEXT, text TEXT, ts INTEGER)")
    for ts, (role, text) in enumerate(TURNS):
        db.execute("INSERT INTO turns(role, text, ts) VALUES (?, ?, ?)", (role, text, ts))
    db.commit()
    return db


def recall_recent(db, k=3):
    rows = db.execute("SELECT text FROM turns ORDER BY ts DESC LIMIT ?", (k,)).fetchall()
    return [r[0] for r in rows]


def recall_keyword(db, term, k=3):
    # Parameter is BOUND, never string-formatted -- SQL injection discipline from §17.
    rows = db.execute(
        "SELECT text FROM turns WHERE text LIKE ? ORDER BY ts DESC LIMIT ?",
        (f"%{term}%", k),
    ).fetchall()
    return [r[0] for r in rows]


def main():
    db = init_db()

    # What works: recency, and keyword search when the word is literally present.
    print("recent 3:           ", recall_recent(db))
    print("keyword 'Portland': ", recall_keyword(db, "Portland"))

    # Where it BREAKS: keyword search is string-matching, not meaning.
    print("keyword 'live':     ", recall_keyword(db, "live"),
          "<- nothing, though 'moved ... to Portland' is right there")
    print("keyword 'seafood':  ", recall_keyword(db, "seafood"),
          "<- nothing; 'shellfish' never literally says 'seafood'")
    print("\nKeyword recall only finds the words you already typed. Meaning is Unit 3.")


if __name__ == "__main__":
    main()
