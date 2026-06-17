---
title: 'A Taxonomy of Memory'
linkTitle: '1. A Taxonomy of Memory'
weight: 1
---

**Goal:** get a vocabulary before you build. "Memory" isn't one thing — an agent has
several *kinds*, each with its own content, lifecycle, and storage. This unit lays out a
practical taxonomy (working, episodic, semantic, procedural, plus profile and derived) and
helps you answer the only question that matters before writing code: **which of these does
*my* agent actually need?** Build the ones your problem demands; skip the rest.

**Where this fits:** Unit 0 drew the line between context management and memory and promised
a vocabulary. This is it. Everything downstream — what you persist (Unit 2), what you embed
(Unit 3), what you put in the graph (Units 5–6) — is easier once you can name *which kind*
of memory a given fact is.

---

## The kinds of memory

The organizing frame comes from **CoALA** (Sumers et al., *TMLR* 2024; arXiv:2309.02427),
which borrows from cognitive science to give agents a memory architecture. Its four core
types, plus two that earn their place in practice:

| Kind | What it holds | Example | Lifecycle |
|---|---|---|---|
| **Working** | The current context — what's in the prompt *right now* | The active `messages` list | Dies with the session (§12) |
| **Episodic** | Specific past events, "what happened" | "Last Tuesday we debugged the auth bug together" | Persisted; recalled by relevance |
| **Semantic** | General facts the agent knows | "Acme Corp is in Portland" | Persisted; updated as facts change |
| **Procedural** | How to do things — skills, routines | A tool-use pattern, a workflow, code | Rarely changes; often in weights/code |
| **Profile** | Stable facts about *this user* | "Allergic to shellfish; prefers terse answers" | Long-lived; privacy-sensitive |
| **Derived** | Computed *from* other memory | A summary, a reflection, a consolidation | Regenerated as raw memory grows |

**Working memory** you already built — it's the context-management of §12, and it's *not*
what this course is about. The rest are the persistent kinds.

The split between **episodic** ("what happened, when") and **semantic** ("what's true") is
the one to internalize, because it changes how you store and retrieve. Episodic memory is
*time-stamped and specific* — you recall it by recency and relevance ("what did we decide
about the migration?"). Semantic memory is *timeless and general* — you recall it by lookup
or meaning ("where is Acme based?"). A good system distills episodic into semantic over
time: many turns about Acme's location consolidate into one durable fact.

**Reflexion** (Shinn et al., *NeurIPS* 2023; arXiv:2303.11366) is the canonical example of
**derived, episodic-verbal** memory at work: an agent reflects on a failed attempt, writes
a short verbal lesson ("I forgot to check the return code"), stores it, and retrieves it
next time to do better. The reflection isn't a raw event — it's *derived* from one — and it
shows why "derived" deserves its own row: the most useful memory is often something you
*computed*, not something a user literally said.

## Which does your agent need?

The point of a taxonomy isn't completeness — it's **subtraction**. Most agents need a
*subset*, and building memory you don't need is the over-engineering this course is at pains
to avoid. Map your agent onto the decision tree from Unit 0:

- A **stateless Q&A bot** over docs needs **semantic** memory (the docs) and nothing else —
  that's plain RAG (§18–19). Stop there; don't build episodic stores or a graph.
- A **personal assistant** needs **profile** (your preferences) + **episodic** (what you've
  discussed) + **semantic** (facts it's learned about your world). This is the case that
  pulls you down the tree toward correlation — and eventually a graph.
- A **coding agent** leans on **procedural** memory (how this repo builds, your conventions)
  plus episodic (what we tried last time — Reflexion's territory).

Name the kinds your agent needs, and you've scoped the rest of the course to yourself.

---

> **Security:** The memory types don't carry equal risk. **Profile** memory is, by
> definition, personal data — names, preferences, health facts like the shellfish allergy —
> so it inherits privacy obligations (retention, deletion, access scope) the moment you
> persist it. **Semantic** memory learned from untrusted conversation can be *poisoned* (a
> planted "fact"). Tag memory by kind and you can apply the right policy to each; Unit 10
> makes this concrete.

## Challenges

1. **Inventory your agent.** For an assistant you want to build, list every "thing it should
   remember" and tag each with a kind from the table. *Success:* you can point to at least
   one kind you *don't* need — and justify skipping it.
2. **Episodic vs. semantic.** Take five things a user might say and sort them into episodic
   ("what happened") vs. semantic ("what's true"). *Success:* you can articulate why "we
   met Tuesday" is stored and recalled differently from "the user is vegetarian."
3. **Find the derived memory.** Identify one fact your agent would be better off *computing*
   (a summary, a preference inferred from behavior) rather than storing verbatim. *Success:*
   you can name what it's derived *from* and when you'd recompute it.

## Recap

- "Memory" is several kinds: **working** (context, §12), **episodic** (events), **semantic**
  (facts), **procedural** (skills), plus **profile** (user data) and **derived** (computed).
- The **episodic → semantic** distinction drives storage and retrieval: events are recalled
  by time/relevance and consolidate into timeless facts.
- **Derived** memory (Reflexion's reflections, summaries) is often the most useful — it's
  computed, not stated.
- A taxonomy is for **subtraction**: build only the kinds your agent's place on the decision
  tree demands.
- Different kinds carry different **risk** — profile is personal data; semantic can be poisoned.

## Next

**Unit 2 — The Naive Baseline:** enough theory — we build. You'll persist turns to SQLite
and recall them by recency and keyword, the simplest possible memory, and *feel* exactly
where it breaks so you know what each later piece is buying you.
