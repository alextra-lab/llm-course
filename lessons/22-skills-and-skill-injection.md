---
title: 'Skills / Skill Injection'
linkTitle: '22. Skills / Skill Injection'
weight: 22
---

**Goal:** understand *skills* — packaged units of instructions (and often code and
resources) that are disclosed into the model's context on demand — and why injecting a
skill is really a context-management decision with a security edge.

**Where this fits:** this sits between guardrails (Section 21) and agents (Section 23). A
skill is how you give an agent reusable, composable expertise without stuffing everything
into one giant system prompt. It draws on the context window (Section 4) and memory
(Section 13), and — because a skill can carry instructions *and* code — on the sandboxing
from Sections 16–17.

> **Reminder — only the triggering step needs the endpoint.** The registry and the
> sandboxed-execution demos run with no endpoint. Only the model-driven *selection* step
> needs your endpoint, and it degrades gracefully without one.

---

## What a skill is

A **tool** (Sections 14–18) is one callable the model can invoke. A **skill** is bigger: a
named bundle of *expertise* — instructions, and optionally bundled code and resources — that
you load into the model's context when it's relevant. Where a tool answers "what can the
model *do*," a skill answers "how should it *approach* this kind of task."

The catch: if you paste every skill's full instructions into the system prompt, the prompt
balloons and the model drowns (Section 4). So the defining move of a skills system is
**progressive disclosure** — keep only a cheap *catalog* in context, and inject a skill's
full instructions only when it's actually triggered.

We'll build the whole thing from scratch: a registry, description-based triggering, and
sandboxed execution of bundled code.

---

## A skill registry + progressive disclosure

A skill is just a folder with a `SKILL.md`: a tiny `name` / `description` header
(*frontmatter*), a markdown instruction body, and — optionally — a fenced ```python block of
bundled code. For example, `skills/word_stats/SKILL.md`:

```markdown
---
name: word_stats
description: Compute word, character, and sentence counts for a piece of text.
---

When the user asks for statistics about a piece of text, report words, characters,
and sentences as `words=… chars=… sentences=…`.

(... plus a fenced python block of bundled code ...)
```

Create **`work/skill_registry.py`** to discover skills and parse that header. The point is
that the *body* is read but kept aside — only `name` and `description` are the catalog:

```python
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

def parse_skill(path):
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
    return [parse_skill(p) for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))]
```

Now show the disclosure gap — how little is in context versus how much is held back:

```python
def main():
    skills = load_registry()
    print("Catalog -- only this stays in context:\n")
    for s in skills:
        print(f"  - {s['name']}: {s['description']}")
    total = sum(len(s["body"]) for s in skills)
    shown = sum(len(s["name"]) + len(s["description"]) for s in skills)
    print(f"\nFull instructions ({total} chars) are NOT loaded; only {shown} chars are.")

if __name__ == "__main__":
    main()
```

```bash
python work/skill_registry.py
```

A handful of one-line descriptions sit in the prompt; the full instructions wait on disk
until needed. That's the same budget discipline as Sections 4 and 13, applied to
capabilities. *(Reference: [`examples/22/skill_registry.py`](../examples/22/skill_registry.py).)*

---

## Triggering by description

How does a skill get selected? You give the model the catalog — names and descriptions —
and let it pick the one that fits (or none). Then you **inject** that one skill's full body
into the system prompt and answer. Create **`work/skill_select.py`**:

```python
import os, sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # the examples/ dir
from skill_registry import load_registry

def choose_skill(client, model, skills, task):
    catalog = "\n".join(f"- {s['name']}: {s['description']}" for s in skills)
    prompt = ("Route the task to at most one skill. Reply with ONLY the skill name, "
              "or 'none' if no skill fits.\n\nSkills:\n" + catalog +
              f"\n\nTask: {task}\nSkill:")
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], temperature=0)
    return response.choices[0].message.content.strip()

def answer_with_skill(client, model, skill, task):
    messages = [{"role": "system", "content": "Use this skill:\n\n" + skill["body"]},
                {"role": "user", "content": task}]
    return client.chat.completions.create(model=model, messages=messages).choices[0].message.content
```

Wire it together, degrading gracefully without creds (the Section 17 pattern):

```python
def main():
    if not (os.environ.get("OPENAI_BASE_URL") and os.environ.get("OPENAI_API_KEY")):
        print("OPENAI_* not set -- skipping the model demo (registry + sandbox still run).")
        return
    from common import get_client, MODEL
    client = get_client()
    skills = load_registry()
    by_name = {s["name"]: s for s in skills}
    task = "Give me the word and character counts for 'the quick brown fox'."
    chosen = choose_skill(client, MODEL, skills, task)
    print("chosen skill:", chosen)
    if chosen in by_name:
        print(answer_with_skill(client, MODEL, by_name[chosen], task))
    else:
        print("no skill triggered -- answering without one.")

if __name__ == "__main__":
    main()
```

```bash
python work/skill_select.py
```

**Triggering is only as reliable as your descriptions.** Make each `description` sharp and
non-overlapping — it's the only thing the model sees when deciding — and always allow
`none`, so an unrelated task doesn't get a skill forced onto it. *(Reference:
[`examples/22/skill_select.py`](../examples/22/skill_select.py).)*

---

## Run bundled skill code in the sandbox

A skill can ship code — and a skill's bundled code is **untrusted input**, no different from
the SQL or shell of Sections 16–17. So we don't `import` it; we extract it from `SKILL.md`
and run it behind the Section 16 sandbox. Create **`work/skill_run.py`**:

```python
import re, sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.append(str(HERE.parents[1] / "15"))   # reuse the Section 16 sandbox
from safe_exec import run_untrusted

SKILL = HERE.parent / "skills" / "word_stats" / "SKILL.md"

def extract_code(skill_md):
    match = re.search(r"```python\n(.*?)```", skill_md.read_text(encoding="utf-8"), re.DOTALL)
    return match.group(1) if match else ""

def main():
    code = extract_code(SKILL)
    if not code.strip():
        print("no bundled code in this skill -- nothing to run.")
        return
    result = run_untrusted(code, timeout=5.0)
    print(f"ok={result.ok}  note={result.note}")
    if result.stdout.strip():
        print("stdout:", result.stdout.strip())

if __name__ == "__main__":
    main()
```

```bash
python work/skill_run.py
```

The skill's script runs in a separate process with hard limits, so a runaway script is
contained, not trusted. Remember the honest limit from Section 16: this caps *runaway
execution* — it does not block the filesystem or network. A skill that needs to read files
or reach the internet belongs in a container (Section 17). *(Reference:
[`examples/22/skill_run.py`](../examples/22/skill_run.py).)*

---

## Composing skills in an agent

Put the pieces together and you have what a Section 23 agent uses: a **library of skills**,
a catalog in the prompt, and a select-then-inject step before (or during) the tool loop. The
three capability layers compose cleanly:

- a **tool** / **MCP server** (Sections 14–18) is a *callable* the model invokes;
- a **skill** is *instructions (and code)* injected into context to shape how it works;
- the **agent** (Section 23) is the loop that chooses among them and takes multiple steps.

A skill can even tell the model *which tools or MCP servers to use* for a task — instructions
and capabilities reinforcing each other.

> **Security:** skill content — both instructions and bundled code — is **untrusted input**.
> Injected instructions can carry prompt injection (Section 21); bundled code must run inside
> the sandbox (Sections 16–17) with least privilege. Trust a skill no more than its source.

## Challenges

1. **Add a skill.** Add a `skills/<name>/SKILL.md` with a sharp, non-overlapping
   `description`. *Success:* `skill_select.py` triggers it for a matching task and picks a
   different skill (or `none`) for an unrelated one.
2. **Prove progressive disclosure.** Show that `skill_registry.py`'s catalog contains only
   names and descriptions — never a skill's instruction body — and that the full body is
   loaded only after selection in `skill_select.py`. *Success:* you can point to exactly
   where the body enters context (the `system` message), and nowhere earlier.
3. **Contain a bad skill.** Add a skill whose bundled code loops forever, then lower the
   `timeout` you pass to `run_untrusted`. *Success:* `skill_run.py` reports `ok=False` with a
   timeout note — the misbehaving skill is stopped by the limit, not by trusting it.

## Recap

- A skill packages instructions (and code) and is **injected on demand** to keep context
  small and capabilities composable — progressive disclosure over a cheap catalog.
- Triggering is description-based: sharp, non-overlapping descriptions (and a `none` option)
  make selection reliable.
- Skill instructions and code are untrusted: apply the guardrails (20) and sandboxing
  (15–16); the portable sandbox contains runaway code but not filesystem/network access.
- Skills, tools/MCP, and the agent loop are three layers that compose (Section 23).

## Next

**[Section 23 — Agents](23-agents.md):** now we compose everything — the tool loop (Section 15), sandboxed
execution (15–16), retrieval (18–19), guardrails (20), and skills — into an agent that plans
and takes multiple steps toward a goal.
