"""
Section 22 - Trigger a skill by description, then inject its full instructions.

The model sees only the skill names + descriptions (progressive disclosure). We
ask it which skill (if any) fits the user's task, then inject that one skill's
full SKILL.md body into the system prompt and answer. The model is the only part
that needs the endpoint, so without OPENAI_* set this skips cleanly.

    python examples/22/skill_select.py
"""

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from skill_registry import load_registry


def choose_skill(client, model, skills, task):
    """Ask the model which skill applies (by description). Returns a name or 'none'."""
    catalog = "\n".join(f"- {s['name']}: {s['description']}" for s in skills)
    prompt = (
        "Route the task to at most one skill. Reply with ONLY the skill name, "
        "or 'none' if no skill fits.\n\nSkills:\n" + catalog +
        f"\n\nTask: {task}\nSkill:"
    )
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], temperature=0)
    return response.choices[0].message.content.strip()


def answer_with_skill(client, model, skill, task):
    """Inject the chosen skill's full body as system instructions, then answer."""
    messages = [
        {"role": "system", "content": "Use this skill:\n\n" + skill["body"]},
        {"role": "user", "content": task},
    ]
    out = client.chat.completions.create(model=model, messages=messages)
    return out.choices[0].message.content


def main():
    if not (os.environ.get("OPENAI_BASE_URL") and os.environ.get("OPENAI_API_KEY")):
        print("OPENAI_BASE_URL / OPENAI_API_KEY not set -- skipping the model demo.")
        print("The registry (skill_registry.py) and sandbox (skill_run.py) run offline.")
        return
    from common import get_client, MODEL
    client = get_client()
    skills = load_registry()
    by_name = {s["name"]: s for s in skills}

    task = "Give me the word and character counts for 'the quick brown fox'."
    chosen = choose_skill(client, MODEL, skills, task)
    print(f"task: {task}\nchosen skill: {chosen}")
    if chosen in by_name:
        print("\nanswer (with skill injected):")
        print(answer_with_skill(client, MODEL, by_name[chosen], task))
    else:
        print("no skill triggered -- answering without one.")


if __name__ == "__main__":
    main()
