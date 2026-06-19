"""
Section 22 - A skill registry with progressive disclosure.

A skill is a folder with a SKILL.md: a tiny name/description header, a markdown
instruction body, and (optionally) a fenced python block of bundled code. This
script discovers the skills and shows the key idea: only the cheap name+description
sit in the model's context until a skill is triggered; the full body is loaded on
demand. No model or endpoint required.

    python examples/22/skill_registry.py
"""

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def parse_skill(path):
    """Parse a SKILL.md into {name, description, body}. Frontmatter is `key: value`."""
    text = path.read_text(encoding="utf-8")
    meta = {"name": path.parent.name, "description": "", "body": text.strip()}
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        for line in front.strip().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        meta["body"] = body.strip()
    return meta


def load_registry():
    """Discover every skills/<name>/SKILL.md as a parsed skill."""
    return [parse_skill(p) for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))]


def main():
    skills = load_registry()
    print("Registry -- only this (cheap name + description) stays in context:\n")
    for s in skills:
        print(f"  - {s['name']}: {s['description']}")
    total = sum(len(s["body"]) for s in skills)
    shown = sum(len(s["name"]) + len(s["description"]) for s in skills)
    print(f"\n{len(skills)} skills. Full instructions ({total} chars) are NOT loaded;")
    print(f"only {shown} chars of names+descriptions are. The body is injected only")
    print("when a skill is triggered (see skill_select.py).")


if __name__ == "__main__":
    main()
