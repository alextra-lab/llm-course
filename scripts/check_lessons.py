#!/usr/bin/env python3
"""CI check: every work/*.py a lesson tells a student to build must be self-contained.

Lessons build small scripts incrementally inside ```python blocks. We attribute
each block to the most recently mentioned `work/<file>.py`, concatenate per file,
and verify two things:

  1. Every block parses (no syntax errors).
  2. The course-helper symbols a student commonly forgets when starting a new file
     (client, MODEL, EMBED_MODEL, get_client) are imported/defined in that file
     before use -- this is the class of bug where a snippet uses `client` but never
     shows `from common import ...` / `client = get_client()`.

We deliberately only gate on those helper symbols, not arbitrary modules, so that
free-standing illustrative snippets (e.g. a one-line formula using numpy) don't
trip the check. Exits non-zero on any problem.
"""
import ast
import glob
import re
import sys

HELPERS = {"client", "MODEL", "EMBED_MODEL", "get_client"}


def blocks_by_file(text):
    """Yield (filename, code) for each python block, attributed to its work/ file."""
    cur = "(intro)"
    in_block = False
    lang = None
    buf = []
    out = {}
    order = []
    for line in text.split("\n"):
        m = re.search(r"work/([A-Za-z0-9_]+\.py)", line)
        if m and not in_block:
            cur = m.group(1)
        if line.strip().startswith("```"):
            if not in_block:
                in_block = True
                fence = re.match(r"```(\w+)", line)
                lang = fence.group(1) if fence else None
                buf = []
            else:
                in_block = False
                if lang in ("python", "py"):
                    out.setdefault(cur, []).append("\n".join(buf))
                    if cur not in order:
                        order.append(cur)
                buf = []
            continue
        if in_block:
            buf.append(line)
    return [(fn, "\n".join(out[fn])) for fn in order]


def undefined_helpers(code):
    tree = ast.parse(code)  # raises SyntaxError -> caller reports it
    defined = set()
    used = []

    class V(ast.NodeVisitor):
        def visit_Import(self, n):
            for a in n.names:
                defined.add((a.asname or a.name).split(".")[0])

        def visit_ImportFrom(self, n):
            for a in n.names:
                defined.add(a.asname or a.name)

        def visit_FunctionDef(self, n):
            defined.add(n.name)
            self.generic_visit(n)

        def visit_ClassDef(self, n):
            defined.add(n.name)
            self.generic_visit(n)

        def visit_Name(self, n):
            if isinstance(n.ctx, ast.Store):
                defined.add(n.id)
            else:
                used.append(n.id)

    V().visit(tree)
    return sorted({u for u in used if u in HELPERS and u not in defined})


def main():
    problems = []
    for md in sorted(glob.glob("lessons/*.md")):
        with open(md, encoding="utf-8") as fh:
            text = fh.read()
        for fn, code in blocks_by_file(text):
            try:
                missing = undefined_helpers(code)
            except SyntaxError as e:
                problems.append(f"{md} :: {fn}: SyntaxError: {e.msg} (line {e.lineno})")
                continue
            if missing:
                problems.append(
                    f"{md} :: {fn}: uses {missing} but never imports/defines them "
                    f"(missing `from common import ...` / `client = get_client()`?)"
                )
    if problems:
        print("Lesson check FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("Lesson check passed: all work/*.py snippets are self-contained.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
