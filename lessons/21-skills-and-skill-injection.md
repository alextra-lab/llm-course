# Section 21 — Skills / Skill Injection

**Goal:** understand *skills* — packaged units of instructions (and often code and
resources) that are disclosed into the model's context on demand — and why injecting a
skill is really a context-management decision with a security edge.

**Where this fits:** this sits between guardrails (Section 20) and agents (Section 22). A
skill is how you give an agent reusable, composable expertise without stuffing everything
into one giant system prompt. It draws on the context window (Section 3) and memory
(Section 12), and — because a skill can carry instructions *and* code — on the sandboxing
from Sections 15–16.

> **Draft — coming soon.** This section is a placeholder reserved in the syllabus. The
> structure and numbering are final; the full walkthrough and runnable `examples/21/` will
> land in a later update. The outline below is what it will cover.

---

## This section will cover

- **What a skill is** — a named bundle (instructions + optional scripts/resources) the model
  can pull in when relevant; the difference between a skill and a plain tool.
- **Progressive disclosure / injection** — load a skill's full instructions into context
  only when it's triggered, to keep the prompt small (ties back to Sections 3 and 12).
- **Triggering** — how a skill gets selected (by description match / the model's judgment),
  and how to make triggering reliable.
- **Composing skills in an agent** — give the Section 22 agent a library of skills and let it
  choose; how skills, tools, and MCP servers fit together.
- **Sandboxing skill-provided code** — a skill's bundled scripts are untrusted input; run
  them behind the isolation from Sections 15–16.

> **Security:** skill content — both instructions and bundled code — is **untrusted input**.
> Injected instructions can carry prompt injection (Section 20); bundled code must run inside
> the sandbox (Sections 15–16) with least privilege. Trust a skill no more than its source.

## Recap

- A skill packages instructions (and code) and is **injected on demand** to keep context
  small and capabilities composable.
- Skill instructions and code are untrusted: apply the guardrails (20) and sandboxing (15–16).
- *(Full content and `examples/21/` to follow.)*

## Next

**Section 22 — Agents:** now we compose everything — the tool loop (Section 14), sandboxed
execution (15–16), retrieval (18–19), guardrails (20), and skills — into an agent that plans
and takes multiple steps toward a goal.
