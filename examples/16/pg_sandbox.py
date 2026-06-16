"""
Section 16 - Sandboxing II: run untrusted SQL safely against Postgres (OPT-IN).

The team runs Postgres, so here's the database equivalent of the sandbox: let a model
(or a user) run SQL without letting it read everything or change anything. The layers:

  - a locked-down ROLE (NOSUPERUSER, NOCREATEDB, only the GRANTs it needs),
  - a READ ONLY transaction so nothing can be written,
  - a short statement_timeout so a heavy query can't hog the server,
  - parameters are bound, never string-formatted into the SQL, and
  - a row LIMIT so a huge result can't blow up memory.

The role is created once by an admin (see the lesson). This script demonstrates the
*session* guards any app can apply on every untrusted query.

OPT-IN: set DATABASE_URL to a throwaway DEV database you control, e.g.
    DATABASE_URL=postgresql://user:pass@localhost:5432/devdb python examples/16/pg_sandbox.py
Without DATABASE_URL (or without psycopg installed) it prints a skip message and exits 0,
so the lesson reads end-to-end with no database.
"""

import os


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set -- skipping the Postgres demo (this is optional).")
        print("Point it at a throwaway dev DB to try the locked-down query path.")
        return
    try:
        import psycopg  # lazy import: only needed for the opt-in path
    except ImportError:
        print("psycopg not installed -- skipping. Install with: pip install 'psycopg[binary]'")
        return

    # An untrusted query we'll run under guards. Parameter is bound, not formatted.
    untrusted_sql = "SELECT %s::text AS note, 1 + 1 AS sum"
    untrusted_param = "hello from a read-only, time-limited transaction"

    with psycopg.connect(url) as conn:
        # READ ONLY transaction: any INSERT/UPDATE/DDL will be rejected.
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                # statement_timeout bounds DB-SIDE work: the server aborts the query
                # after 2s, so an expensive scan can't run to completion server-side.
                cur.execute("SET LOCAL statement_timeout = '2s'")
                cur.execute(untrusted_sql, (untrusted_param,))
                # fetchmany bounds CLIENT-SIDE memory (rows pulled into Python). The two
                # are different limits: time on the server, memory in your process. For
                # a true result-size cap, run on a read replica or wrap the query when
                # its shape allows: SELECT * FROM (<untrusted>) q LIMIT 100.
                rows = cur.fetchmany(100)
                print("query ok:", rows)

        # Prove writes are blocked inside a READ ONLY transaction.
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute("CREATE TEMP TABLE should_fail (x int)")
            print("WARNING: write was NOT blocked -- check your role/grants")
        except psycopg.errors.Error as e:
            print("write correctly blocked:", str(e).splitlines()[0])


if __name__ == "__main__":
    main()
