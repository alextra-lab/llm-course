"""
Unit 5: the guarded middle of the dial for SQL -- check a query's parse tree, not its text.

A model writes SQL; this module decides whether it may run. It parses the query with sqlglot
(Postgres dialect) and refuses, with an instruction, anything that is not:

  - exactly one statement;
  - a read: SELECT (with WITH, UNION, subqueries) and nothing that writes, locks, sets or
    copies -- searched for anywhere in the tree, not just at the top;
  - on allowlisted schemas only (cmdb), every table schema-qualified;
  - using functions sqlglot recognises as standard SQL, or ones on a short allowlist.
    Anything else (pg_sleep, pg_read_file, dblink, set_config, ...) is refused, because
    "functions we haven't thought about" is exactly where the surprises are.

What passes is wrapped with a row cap and run in a READ ONLY transaction with its own short
statement_timeout, AS the persona -- so the database's own grants and row-level security
still apply underneath. The guard narrows what the persona could do; it never widens it.

    python examples/05/sql_guard.py "SELECT hostname FROM cmdb.assets LIMIT 3"
    python examples/05/sql_guard.py "SET statement_timeout = 0; SELECT pg_sleep(30)"
"""

from __future__ import annotations

import sys

import sqlglot
from sqlglot import exp

ALLOWED_SCHEMAS = {"cmdb"}
MAX_ROWS = 200
TIMEOUT = "2s"
# Postgres functions sqlglot doesn't model as standard SQL but that are harmless and useful.
ALLOWED_FUNCTIONS = {"host", "string_agg", "array_agg", "date_part", "split_part", "left",
                     "right", "initcap", "btrim", "inet_same_family"}
# Standard functions that are still a bad idea on a shared database.
DENIED = (exp.GenerateSeries,)
WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
          exp.TruncateTable, exp.Command, exp.Set, exp.Into, exp.Lock, exp.Copy,
          exp.Grant, exp.Use)


class SqlRefused(ValueError):
    """The query may not run. The message says what to change."""


def check(sql: str) -> exp.Expression:
    """Parse and check one query. Returns the parsed tree, or raises SqlRefused."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        first = str(e).splitlines()[0][:160]
        raise SqlRefused(f"the SQL doesn't parse ({first}). Send one Postgres SELECT.") from None
    if len(statements) != 1:
        raise SqlRefused(f"send exactly one statement; this has {len(statements)}. No `;` "
                         "chains.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SqlRefused(f"only SELECT queries may run here, not {tree.key.upper()}.")
    for node in tree.walk():
        if isinstance(node, WRITES):
            raise SqlRefused(f"{node.key.upper()} isn't allowed: this tool only reads. "
                             "Send a plain SELECT.")
        if isinstance(node, exp.Lock) or (isinstance(node, exp.Select) and node.args.get("locks")):
            raise SqlRefused("row locks (FOR UPDATE / FOR SHARE) aren't allowed.")
    for fn in tree.find_all(exp.Func):
        if isinstance(fn, DENIED):
            raise SqlRefused("generate_series() isn't allowed here (it can generate unbounded "
                             "rows).")
        if isinstance(fn, exp.Anonymous) and fn.name.lower() not in ALLOWED_FUNCTIONS:
            raise SqlRefused(f"the function {fn.name}() isn't allowed. Use standard SQL "
                             "functions (count, sum, lower, coalesce, date_trunc, ...).")
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func) or (table.name in ctes and not table.db):
            continue                       # a table function (checked above) or a CTE
        if not table.db:
            raise SqlRefused(f"qualify the table {table.name!r} with its schema; only "
                             f"{', '.join(sorted(ALLOWED_SCHEMAS))} is queryable (e.g. "
                             "cmdb.assets).")
        if table.db not in ALLOWED_SCHEMAS:
            raise SqlRefused(f"schema {table.db!r} isn't queryable here; only "
                             f"{', '.join(sorted(ALLOWED_SCHEMAS))}.")
    return tree


def capped(tree: exp.Expression, max_rows: int = MAX_ROWS) -> str:
    """The query as it will run: wrapped so it can never return more than max_rows + 1 rows
    (the extra row tells the caller it was truncated)."""
    return f"SELECT * FROM ({tree.sql(dialect='postgres')}) AS guarded LIMIT {max_rows + 1}"


def run(conn, sql: str, max_rows: int = MAX_ROWS) -> dict:
    """Check, cap and run `sql` on an open connection (already AS the persona)."""
    tree = check(sql)
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = '{TIMEOUT}'")
        cur = conn.execute(capped(tree, max_rows))
        columns = [c.name for c in cur.description or []]
        rows = cur.fetchall()
    truncated = len(rows) > max_rows
    rows = [[_plain(v) for v in r] for r in rows[:max_rows]]
    out = {"columns": columns, "rows": rows, "returned": len(rows), "truncated": truncated}
    if truncated:
        out["note"] = (f"more than {max_rows} rows matched; only the first {max_rows} are "
                       "shown. Aggregate (count, GROUP BY) or filter instead of listing.")
    return out


def _plain(v):
    return v if isinstance(v, (int, float, str, bool, type(None))) else str(v)


if __name__ == "__main__":
    for query in sys.argv[1:] or ["SELECT hostname FROM cmdb.assets LIMIT 3"]:
        try:
            print("would run:", capped(check(query)))
        except SqlRefused as e:
            print("refused:", e)
