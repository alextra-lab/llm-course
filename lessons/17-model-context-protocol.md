# Section 17 — Model Context Protocol (MCP)

**Goal:** understand MCP as the *standard* way to expose and consume tools — so a set of
tools (and data sources) can live behind a server and be reused across many apps and
models, instead of being hand-wired into one program.

**Where this fits:** Sections 13–14 taught raw tool calling; Sections 15–16 made tool
*execution* safe to isolate. MCP is the layer on top: a common protocol for *connecting*
models to tools and data — think of it as a standard port (often described as "USB-C for
AI") rather than a new capability.

> **Draft — coming soon.** This section is a placeholder reserved in the syllabus. The
> structure and numbering are final; the full walkthrough and runnable `examples/17/` will
> land in a later update. The outline below is what it will cover.

---

## This section will cover

- **What MCP is** — clients, servers, and the transport between them; how it relates to the
  raw tool-call handshake from Sections 13–14.
- **Consuming an MCP server** — point the tool loop at a server and let the model use the
  tools it advertises, without bespoke glue per tool.
- **Exposing your own tools as a server** — wrap existing functions (the calculator, a
  document search) behind MCP so other apps can reuse them.
- **Trust and permissions** — MCP servers are a trust boundary: OAuth-scoped tokens,
  least-privilege tool grants, and **running server-provided tools inside the sandbox from
  Sections 15–16**.
- **When MCP earns its keep** — versus calling tools directly: reuse across apps, third-party
  tool ecosystems, and clean separation of concerns; and when it's overkill.

> **Security:** an MCP server is third-party code at the end of a connection. Treat its tool
> list, its arguments, and its outputs as untrusted (Section 20), scope its credentials
> narrowly, and execute anything it runs behind the isolation from Sections 15–16.

## Recap

- MCP standardizes *connecting* models to tools and data — it builds on tool calling
  (13–14), not replaces it.
- A server is a trust boundary: scope credentials, validate I/O, and sandbox execution.
- *(Full content and `examples/17/` to follow.)*

## Next

**Section 18 — Embeddings:** we switch from *acting* to *meaning* — turning text into
vectors with the embeddings endpoint and measuring similarity by hand, the foundation for
retrieval.
