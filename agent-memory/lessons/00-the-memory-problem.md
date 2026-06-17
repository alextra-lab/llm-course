---
title: 'The Memory Problem'
linkTitle: '0. The Memory Problem'
weight: 0
---

**Goal:** see clearly that two different things get called "memory," and that this course
is about the harder one. *Context management* keeps a single conversation coherent inside
the token budget; *memory* is what an agent knows **across** sessions, days, and topics —
after the conversation that taught it is long gone. The first you already built. The
second is a database-and-retrieval discipline, and it's big enough to be its own course.

**Where this fits:** this is the opening of the **second** course, and it assumes the
first. The foundations course ended Section 12 on a deliberate cliffhanger: *"Persisting
facts across sessions is a database problem, not a model one — store the facts, then
retrieve and inject the relevant ones."* That one sentence hides a lot of decisions. This
course is the unfolding of it. You'll lean on §12 (history), §18 (embeddings), §19 (RAG),
and §22 (agents) without re-teaching them.

---

## Two different problems, one overloaded word

In §12 you made a stateless API hold a conversation by resending the whole `messages` list
every turn, and you kept it inside the budget with a sliding window and summarization.
That is **context management**: everything is in service of *this* conversation, *right
now*. Close the session and it all evaporates — by design.

**Memory** is the opposite of evaporating. It's the agent remembering, three weeks later
in a brand-new session, that you're allergic to shellfish, that you already rejected the
blue design, that "the migration" means the Postgres one and not the Python 2 one. None of
that lives in the current `messages` list. It has to be *stored* when learned and
*retrieved* when relevant.

| | Context management (§12) | Memory (this course) |
|---|---|---|
| **Scope** | One conversation | Across sessions, indefinitely |
| **Lives in** | The `messages` list you resend | A datastore you write to and query |
| **Lost when** | The session ends | You delete it (or let it decay on purpose) |
| **Core operation** | Trim / summarize to fit the window | Store, then retrieve what's relevant |
| **Failure mode** | Runs out of context budget | Forgets, or recalls the wrong thing |

They're easy to conflate because both answer "what does the model see in the prompt?" But
the *mechanisms* are unrelated, and reaching for the context-management tool when you have
a memory problem is the most common mistake in this space.

## Why summarization isn't memory

A running summary feels like memory — it survives many turns. But it's lossy compression
of *one conversation*, and it has three properties that disqualify it as real memory:

- **It doesn't survive a new session.** Start fresh tomorrow and the summary is gone with
  the rest of the history. Nothing was *persisted*.
- **It has no structure.** "The user mentioned a few preferences and a deadline" is prose.
  You can't query it for *just* the deadline, update one fact without rewriting the blob,
  or notice that two sessions mention the same person.
- **It can't correlate.** If you learned "Alex works at Acme" on Monday and "Acme is in
  Portland" on Thursday, a per-session summary never connects them. Answering "what city
  is my user's employer in?" requires *joining* facts that were never stated together —
  and a summary can't join.

That third point is the whole reason this course eventually reaches for a graph. Hold onto
it; it comes back in Unit 4.

You can *feel* the gap with the foundations code. Run a §12 chat, then start the script
again — a second, independent session:

```python
# session 2, started cold — the model has never heard of session 1
history = [{"role": "system", "content": "You are a helpful assistant."}]
history.append({"role": "user", "content": "What did I tell you my name was?"})
# -> the model has no idea. Nothing carried over. THAT is the memory problem.
```

Nothing carried over, because nothing was stored. Real memory is the machinery that would
let session 2 answer that question.

## The shape of this course

This course is **opinionated but honest**. It argues *toward* a particular default — a
knowledge graph with hybrid retrieval — but it gets there by walking down a decision tree,
not by asserting. At each step it asks what your problem actually demands, and it's happy
to send you away early if a graph would be overkill:

> **The thesis, as a decision tree** *(you'll build every branch; the full version is
> Unit 11):*
>
> 1. **Need memory across sessions at all?** No → window/summarize (§12). Stop. You're done.
> 2. **Are the facts mostly independent lookups?** Yes → a vector store / plain RAG
>    (§18–19) is enough. Don't build a graph.
> 3. **Do you need to *correlate* facts — multi-hop, "who/what/when across history"?**
>    Yes → now a graph earns its complexity.
> 4. **Is the memory shaped by ongoing conversation** (not a fixed corpus)? → favor
>    *incremental* construction over building the whole graph up front.
> 5. **Whichever branch you land on:** gate what you write, decay what you read, and
>    **measure recall before you optimize.** That's true regardless of substrate.

A word on intellectual honesty up front, because the field is noisy about this: graph and
multi-hop retrieval do **not** universally beat a strong vector baseline. They win on
multi-hop, relational, and global-sensemaking questions; they **lose** on simple fact
lookup, on latency, and on token cost — sometimes by more than 10×. We'll look at the
actual numbers in Unit 4 and treat every "graphs win" headline as *"this paper reports,"*
not settled consensus. The goal is for you to know *when* the graph is worth it, and to
recognize when it isn't.

## A frame for "memory"

It helps to have a vocabulary before we build. A useful organizing frame comes from
**CoALA — Cognitive Architectures for Language Agents** (Sumers, Yao, Narasimhan &
Griffiths, *TMLR* 2024; arXiv:2309.02427), which treats memory as a *first-class
architectural component* of an agent rather than an afterthought bolted onto chat history.
It distinguishes kinds of memory — working, episodic, semantic, procedural — each with its
own role and lifecycle. Unit 1 turns that into a practical taxonomy you can map your own
agent onto ("which of these do I actually need?"). For now the takeaway is just the
reframe: **memory is something you design, not something the `messages` list gives you for
free.**

---

> **Security:** Memory is persistent and attacker-reachable — a more dangerous combination
> than a single conversation. Whatever a user (or a tool result, or a retrieved document)
> says today, you may *store* and then *replay into the prompt for months*. A planted
> instruction or a poisoned "fact" doesn't expire when the session ends. Every later unit
> carries a security note, and Unit 10 is dedicated to it; keep "what we persist, we will
> re-trust later" in mind from the very first node you write.

## Challenges

These are reflection-and-experiment prompts; the building starts in Unit 2.

1. **Make the gap concrete.** Run any §12 chat script, teach it a fact, then run it again
   from scratch and ask for that fact. *Success:* you can state, in one sentence, exactly
   what was lost and why — and which row of the table above describes it.
2. **Classify your own agent.** Pick a real assistant you'd like to build. Walk it down
   the decision tree above and write where it lands (stop at step 1? step 2? step 3?).
   *Success:* an honest answer to "do I even need a graph?" — including the possibility
   that you don't.
3. **Spot the conflation.** Find one feature you've described as "memory" that is really
   context management (or vice-versa). *Success:* you can name which problem it actually is
   and which mechanism fits.

---

## Recap

- "Memory" names two unrelated problems: **context management** (keep one conversation
  coherent — §12) and **memory** (know things across sessions — this course).
- Windowing and summarization are context management: lossy, session-bound, unstructured,
  and unable to **correlate** facts learned at different times.
- Real memory is a **store-then-retrieve** discipline over a datastore — the unfolding of
  §12's closing line.
- The course argues toward a graph **via a decision tree**, honestly: graphs win on
  multi-hop/relational recall and lose on simple lookup, latency, and cost.
- Memory is persistent *and* attacker-reachable — what you store, you re-trust later.

## Next

**Unit 1 — A Taxonomy of Memory:** before building, we map the kinds of memory (working,
episodic, semantic, procedural, profile, derived) onto the CoALA frame and decide *which
ones your agent actually needs* — so you build only what your problem demands.
