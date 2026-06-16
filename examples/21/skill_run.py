"""
Section 21 - Run a skill's bundled code inside the Section 15 sandbox.

A skill can ship code, and that code is untrusted input -- so we don't import it,
we extract it from SKILL.md and run it behind the portable sandbox from Section 15
(separate process, hard limits, stripped environment). The sandbox contains a
runaway script; it does not block filesystem or network (Section 16 does that).
No endpoint required.

    python examples/21/skill_run.py
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.append(str(HERE.parents[1] / "15"))  # reuse the Section 15 sandbox
from safe_exec import run_untrusted

SKILL = HERE.parent / "skills" / "word_stats" / "SKILL.md"


def extract_code(skill_md):
    """Pull the first fenced ```python block out of a SKILL.md (or '' if none)."""
    match = re.search(r"```python\n(.*?)```", skill_md.read_text(encoding="utf-8"),
                      re.DOTALL)
    return match.group(1) if match else ""


def main():
    code = extract_code(SKILL)
    if not code.strip():
        print("no bundled code in this skill -- nothing to run.")
        return
    print("Running the skill's bundled code in the Section 15 sandbox...\n")
    result = run_untrusted(code, timeout=5.0)
    print(f"  ok={result.ok}  note={result.note}")
    if result.stdout.strip():
        print(f"  stdout: {result.stdout.strip()}")
    if result.stderr.strip():
        print(f"  stderr: {result.stderr.strip().splitlines()[-1]}")


if __name__ == "__main__":
    main()
