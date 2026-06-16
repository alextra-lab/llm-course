#!/usr/bin/env python3
"""Add Hugo front matter to each lesson in lessons/.

Idempotent: files that already begin with a front-matter block ('---') are left
untouched. For every other lesson it parses the leading H1 of the form

    # Section N — Title

and rewrites the file with YAML front matter (title, linkTitle "N. Title",
weight N), removing that H1 so Docsy renders the title once.
"""
from __future__ import annotations

import pathlib
import re
import sys

LESSONS = pathlib.Path(__file__).resolve().parent.parent / "lessons"
H1 = re.compile(r"^#\s+Section\s+(\d+)\s+—\s+(.+?)\s*$")


def yaml_single_quote(value: str) -> str:
    # Single-quoted YAML; lesson titles contain ", &, /, () but no single quotes.
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    changed = 0
    for path in sorted(LESSONS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = H1.match(line)
            if m:
                num, title = int(m.group(1)), m.group(2)
                # Drop the H1 line and a single trailing blank line if present.
                rest = lines[i + 1 :]
                if rest and rest[0].strip() == "":
                    rest = rest[1:]
                front = [
                    "---",
                    f"title: {yaml_single_quote(title)}",
                    f"linkTitle: {yaml_single_quote(f'{num}. {title}')}",
                    f"weight: {num}",
                    "---",
                    "",
                ]
                path.write_text("\n".join(front + rest) + "\n", encoding="utf-8")
                changed += 1
                break
        else:
            print(f"WARN no 'Section N — Title' H1 in {path.name}", file=sys.stderr)
    print(f"front matter added to {changed} lesson(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
