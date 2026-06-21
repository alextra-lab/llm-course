---
title: 'The Memory Problem'
linkTitle: 'Introduction'
weight: -1
---

**Goal:** understand that two different things are both called "memory," and that this
course is about the more difficult one. *Context management* keeps a single conversation
consistent while staying within the token budget. *Memory* is what an agent knows **across**
sessions, days, and topics — after the conversation that created it has ended. You already
built the first one. The second is a database-and-retrieval problem, and it is large enough
to be its own course.

**Where this fits:** this is the start of the **second** course, and it assumes the first.
The foundations course ended Section 13 with a hint: *"Persisting facts across sessions is a
database problem, not a model one — store the facts, then retrieve and inject the relevant
ones."* That one sentence contains many design decisions. This course develops it. You will
build on §13 (history), §19 (embeddings), §20 (RAG), and §23 (agents), and will not teach
them again.

---

## Two different problems, one word

In §13 you made a stateless API hold a conversation by resending the whole `messages` list
every turn. You kept it within the budget with a sliding window and summarization. That is
**context management**: everything serves *this* conversation, *right now*. When the session
ends, all of it disappears — and that is on purpose.

**Memory** is the opposite: it stays. It is the agent remembering, three weeks later in a
new session, that you are allergic to shellfish, that you already rejected the blue design,
and that "the migration" means the Postgres one and not the Python 2 one. None of that is in
the current `messages` list. It must be *stored* when learned and *retrieved* when relevant.

| | Context management (§13) | Memory (this course) |
|---|---|---|
| **Scope** | One conversation | Across sessions, indefinitely |
| **Lives in** | The `messages` list you resend | A datastore you write to and query |
| **Lost when** | The session ends | You delete it (or let it decay on purpose) |
| **Core operation** | Trim / summarize to fit the window | Store, then retrieve what's relevant |
| **Failure mode** | Runs out of context budget | Forgets, or recalls the wrong thing |

People confuse the two because both answer "what does the model see in the prompt?" But the
*mechanisms* are unrelated. Using the context-management tool for a memory problem is the
most common mistake in this area.

## Why summarization isn't memory

A running summary looks like memory, because it survives many turns. But it is a compressed,
lossy copy of *one conversation*, and it has three properties that make it not real memory:

- **It does not survive a new session.** Start again tomorrow and the summary is gone with
  the rest of the history. Nothing was *persisted*.
- **It has no structure.** "The user mentioned a few preferences and a deadline" is plain
  text. You cannot query it for *only* the deadline, update one fact without rewriting the
  whole text, or notice that two sessions mention the same person.
- **It cannot correlate.** Suppose you learned "Alex works at Acme" on Monday and "Acme is
  in Portland" on Thursday. A per-session summary never connects them. Answering "what city
  is my user's employer in?" needs you to *join* facts that were never stated together, and
  a summary cannot join.

That third point is the main reason this course later uses a graph. Remember it; it returns
in Unit 4.

You can see the gap with the foundations code. Run a §13 chat, then start the script again
as a second, separate session:

```python
# session 2, with no earlier history — the model has never seen session 1
history = [{"role": "system", "content": "You are a helpful assistant."}]
history.append({"role": "user", "content": "What did I tell you my name was?"})
# -> the model has no idea. Nothing carried over. THAT is the memory problem.
```

Nothing carried over, because nothing was stored. Real memory is the mechanism that would
let session 2 answer that question.

## The shape of this course

This course states clear opinions, but stays honest. It argues *for* one default — a
knowledge graph with hybrid retrieval — but it reaches that default by following a decision
tree, not by simply asserting it. At each step it asks what your problem actually needs, and
it will tell you to stop early if a graph is more than you need:

> **The thesis, as a decision tree** *(you will build every branch; the full version is
> Unit 11):*
>
> 1. **Do you need memory across sessions at all?** No → window/summarize (§13). Stop. You're done.
> 2. **Are the facts mostly independent lookups?** Yes → a vector store / plain RAG
>    (§19–20) is enough. Do not build a graph.
> 3. **Do you need to *correlate* facts — multi-hop, "who/what/when across history"?**
>    Yes → now a graph is worth its extra complexity.
> 4. **Is the memory shaped by ongoing conversation** (not a fixed corpus)? → prefer
>    *incremental* construction over building the whole graph in advance.
> 5. **On any branch:** control what you write, decay what you read, and **measure recall
>    before you optimize.** That is true whatever the storage.

One point about honesty, because there are many strong claims in this field: graph and
multi-hop retrieval do **not** always beat a strong vector baseline. They perform better on
multi-hop, relational, and global-sensemaking questions. They perform worse on simple fact
lookup, on latency, and on token cost — sometimes by more than 10×. We will look at the real
numbers in Unit 4, and treat every "graphs win" claim as *"this paper reports,"* not as an
established fact. The goal is for you to know *when* the graph is worth it, and when it is
not.

## A vocabulary for "memory"

It helps to have words for this before we build. A useful frame comes from **CoALA —
Cognitive Architectures for Language Agents** (Sumers, Yao, Narasimhan & Griffiths, *TMLR*
2024; arXiv:2309.02427). It treats memory as a *core architectural component* of an agent,
not an extra feature added on top of chat history. It separates kinds of memory — working,
episodic, semantic, procedural — each with its own role and lifecycle. Unit 1 turns this
into a practical taxonomy that you can map your own agent onto ("which of these do I actually
need?"). For now the main point is the change in view: **memory is something you design, not
something the `messages` list gives you for free.**

---

> **Security:** Memory is persistent and can be reached by an attacker — a more dangerous
> combination than a single conversation. Whatever a user (or a tool result, or a retrieved
> document) says today, you may *store* it and then *put it back into the prompt for months*.
> A malicious instruction or a false "fact" does not expire when the session ends. Every
> later unit has a security note, and Unit 10 is dedicated to this topic. From the very first
> node you write, remember: what we persist, we will trust again later.

> **Observe:** A memory you cannot see is a memory you cannot debug. Every later unit also
> carries an `Observe` note: it instruments what the unit builds — a joinable
> `session_id`/`trace_id`/`step` log line, reused from foundations §10 — and names the
> question that telemetry answers ("what did the agent remember, and for whom?", "did recall
> improve over the baseline?"). Units 9 and 10 are dedicated to measurement and joinable
> telemetry, but you instrument from the first node you write, not at the end. This is the
> repo's [Observability Standard](../../OBSERVABILITY.md), the same through-line as security.

## Challenges

These are thinking-and-experiment tasks; the building starts in Unit 2.

1. **Make the gap concrete.** Run any §13 chat script, teach it a fact, then run it again
   from the start and ask for that fact. *Success:* you can say, in one sentence, exactly
   what was lost and why — and which row of the table above describes it.
2. **Classify your own agent.** Pick a real assistant you would like to build. Follow the
   decision tree above and write down where it stops (step 1? step 2? step 3?). *Success:* an
   honest answer to "do I even need a graph?" — including the possibility that you do not.
3. **Find the confusion.** Find one feature you have called "memory" that is really context
   management (or the opposite). *Success:* you can name which problem it actually is, and
   which mechanism fits.

---

## Recap

- "Memory" names two unrelated problems: **context management** (keep one conversation
  consistent — §13) and **memory** (know things across sessions — this course).
- Windowing and summarization are context management: lossy, limited to one session, without
  structure, and unable to **correlate** facts learned at different times.
- Real memory is a **store-then-retrieve** problem over a datastore — the development of
  §13's final sentence.
- The course argues for a graph **through a decision tree**, and stays honest: graphs are
  better at multi-hop/relational recall and worse at simple lookup, latency, and cost.
- Memory is persistent *and* reachable by attackers — what you store, you trust again later.

## Next

**Unit 1 — A Taxonomy of Memory:** before building, we map the kinds of memory (working,
episodic, semantic, procedural, profile, derived) onto the CoALA frame and decide *which ones
your agent actually needs* — so you build only what your problem requires.
