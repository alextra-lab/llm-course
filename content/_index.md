---
title: FrenchForet
---

{{< blocks/cover title="FrenchForet — LLM Engineering" image_anchor="top" height="med" >}}
<p class="lead mt-5">Two hands-on, foundations-first courses for building with large language models — from your first raw HTTP call to an agent with a knowledge-graph memory.</p>
<a class="btn btn-lg btn-primary me-3 mb-4" href="/docs/">
  Start with Foundations <i class="fas fa-arrow-alt-circle-right ms-2"></i>
</a>
<a class="btn btn-lg btn-secondary me-3 mb-4" href="https://github.com/alextra-lab/llm-course">
  View on GitHub <i class="fab fa-github ms-2 "></i>
</a>
{{< /blocks/cover >}}

{{% blocks/lead color="primary" %}}
Two courses, meant to be read **in order**. Do the Foundations course first; the Agent Memory
course assumes it and builds on top.
{{% /blocks/lead %}}

<section class="course-cards-section">
<div class="course-cards">

{{< course-card number="01" title="AI Development — Foundations" url="/docs/" cta="Start the course →" badges="24 sections|read in order|Start here" >}}
**Start here.** Build from a raw HTTP request up through tokens, sampling, tools, the tool-use loop, sandboxing, embeddings, RAG, and agents — seeing the raw mechanic first, then the convenient abstraction on top.
{{< /course-card >}}

{{< course-card number="02" title="Agent Memory: From Chat History to a Knowledge Graph" url="/agent-memory/" cta="Continue the course →" badges="12 units|read in order|Assumes Foundations" >}}
**Do Foundations first.** Goes deep on cross-session memory: a taxonomy of memory, conversational ingestion, a Neo4j knowledge graph, hybrid retrieval, lifecycle and decay, measurement, and a measured, opinionated default.
{{< /course-card >}}

</div>
</section>

{{% blocks/section color="white" type="row" %}}

{{% blocks/feature icon="fa-code" title="You write every line" %}}
Each lesson guides you through building small scripts yourself. A runnable reference solution
for everything lives under `examples/`.
{{% /blocks/feature %}}

{{% blocks/feature icon="fa-server" title="One hosted endpoint" %}}
Every example targets an OpenAI-compatible server (vLLM serving `gpt-oss-120b`). Point three
environment variables at it and go; embeddings can use a separate endpoint.
{{% /blocks/feature %}}

{{% blocks/feature icon="fa-shield-halved" title="Security throughout" %}}
Security is a through-line, not a chapter — every lesson ends with a topic-specific note, and
isolation gets dedicated sections.
{{% /blocks/feature %}}

{{% /blocks/section %}}
