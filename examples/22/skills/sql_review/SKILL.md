---
name: sql_review
description: Review a SQL query for safety -- bound parameters, read-only intent, and a row limit.
---

When the user shares a SQL query, check three things and explain any fix in one or
two sentences:

- Values are bound as parameters, never string-formatted into the SQL.
- The statement is read-only unless a write is clearly intended.
- Large result sets are bounded with a LIMIT.

(This is the locked-down query path from Section 16.)
