---
title: "References & Further Reading"
linkTitle: "13. References"
weight: 13
---

The specifications, standards and documentation this course is built on, grouped by topic,
with the unit that leans on each. Where the course makes a claim about a protocol, a database
or a security property, the source is here. Read the source when your version differs from
the course's: specs and products move faster than courses.

The course targets **MCP 2026-07-28** with the **Python SDK v2**, **Postgres 17** and
**OpenSearch** with its security plugin. Links were checked in October 2026.

## The protocol

- **[MCP specification, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)**
  and its **[changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)**.
  The stateless core, `server/discover`, `input_required`, cache hints, the `Mcp-Method` and
  `Mcp-Name` headers, and trace context in `_meta`. Units 0, 1 and 2, and everything after.
- **[The 2026-07-28 release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)**
  and **[the final release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)**: what
  changed from 2025 and why, including what's deprecated (sampling, roots, logging) and the
  one-year runway. Unit 0.
- **[The specification repository](https://github.com/modelcontextprotocol/modelcontextprotocol)**:
  the schema and the SEPs (enhancement proposals) that the changelog cites, such as SEP-414
  for trace context (Unit 11).
- **[Python SDK](https://github.com/modelcontextprotocol/python-sdk)**: `MCPServer` (formerly
  FastMCP), the first-class `Client`, middleware, `ToolError`, sealed request state, and the
  SDK's own OpenTelemetry spans. Every unit's code.
- **[MCP Inspector](https://github.com/modelcontextprotocol/inspector)**: a client for poking
  at a server by hand. A useful companion to Unit 1's frame tap.
- **[JSON Schema 2020-12](https://json-schema.org/draft/2020-12)**: the dialect the spec uses
  for input and output schemas. Units 1 and 6.

## Identity and authorization

- **[MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)**:
  OAuth 2.1 for HTTP transports, protected resource metadata, audience-bound tokens and no
  token passthrough. Unit 7.
- **[RFC 9728, OAuth 2.0 Protected Resource Metadata](https://www.rfc-editor.org/rfc/rfc9728)**:
  how a client learns which authorization server a resource trusts. Unit 7.
- **[RFC 8707, Resource Indicators for OAuth 2.0](https://www.rfc-editor.org/rfc/rfc8707)**:
  why a token for the CMDB server is refused by the SIEM server. Unit 7, and the identity line
  of Unit 12's review.
- **[RFC 7519, JSON Web Token](https://www.rfc-editor.org/rfc/rfc7519)** and
  **[RFC 8037, Ed25519 in JOSE](https://www.rfc-editor.org/rfc/rfc8037)**: the course issuer's
  tokens. Unit 7.
- **[RFC 9700, OAuth 2.0 Security Best Current Practice](https://www.rfc-editor.org/rfc/rfc9700)**:
  the threat model the MCP authorization page builds on.

## Security

- **[OWASP MCP Top 10](https://owasp.org/www-project-mcp-top-10/)**: the ten categories Unit 10
  maps onto the gateway, and the cases the Unit 12 review reports on.
- **[OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/)**: the wider list
  the MCP one specializes. Prompt injection and excessive agency are the two that matter most
  here.
- **[MCP security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)**:
  the confused deputy, token passthrough, session hijacking and local server compromise, from
  the protocol's maintainers. Units 7 and 10.
- **[Tool poisoning attacks](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks)**
  (Invariant Labs, 2025): instructions hidden in tool descriptions. The reason for Unit 10's
  description vetting and pinning.
- **[The lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)**
  (Simon Willison, 2025): private data, untrusted content and a way to send data out, in one
  agent. Unit 0's argument for a measured default, and Unit 10's egress defence.

## Observability

- **[W3C Trace Context](https://www.w3.org/TR/trace-context/)**: the `traceparent` format the
  joinable record and `_meta` carry. Units 1 and 11.
- **[OpenTelemetry semantic conventions for MCP](https://opentelemetry.io/docs/specs/semconv/gen-ai/mcp/)**
  and **[for generative AI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)**: the span
  names and attributes `telemetry.to_span` follows. Both are still marked *Development*; the
  source now lives in
  [semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai).
  Unit 11.
- **[Service Level Objectives](https://sre.google/sre-book/service-level-objectives/)** (SRE
  book) and **[Implementing SLOs](https://sre.google/workbook/implementing-slos/)** (SRE
  workbook): objectives, indicators and error budgets. Unit 11's objective and the latency
  line of Unit 12's review.

## Load, limits and scale

- **[Scaling your API with rate limiters](https://stripe.com/blog/rate-limiters)** (Stripe):
  request rate, concurrency and load shedding, the same split as Unit 8's limits.
- **[Handling Overload](https://sre.google/sre-book/handling-overload/)** (SRE book): per-caller
  limits, criticality and retry budgets. Unit 8's "busy, retry after N s" refusals.
- **[Valkey](https://valkey.io/)**: the key-value store behind Unit 9's shared limits and cache.
- **[Uvicorn](https://uvicorn.dev)**: the ASGI server and its workers. Unit 9.

## Postgres (the CMDB)

- **[Row security policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)**:
  how an L1 analyst can't see `db-prod-01`. Units 3 and 7.
- **[GRANT](https://www.postgresql.org/docs/current/sql-grant.html)** and
  **[SET ROLE](https://www.postgresql.org/docs/current/sql-set-role.html)**: column privileges,
  the gateway acting as the caller's persona, and the insert-only and select-only logins of the
  audit table. Units 7 and 11.
- **[Client connection defaults](https://www.postgresql.org/docs/current/runtime-config-client.html)**:
  `statement_timeout` and friends. Units 5 and 8.
- **[EXPLAIN](https://www.postgresql.org/docs/current/sql-explain.html)**: the cost gate before
  a query runs. Units 5 and 8.
- **[psycopg connection pools](https://www.psycopg.org/psycopg3/docs/advanced/pool.html)**:
  sizing a pool against a role's connection limit. Units 8 and 9.
- **[SQLGlot](https://sqlglot.com/)**: the SQL parser behind Unit 5's guard.

## OpenSearch (the SIEM)

- **[Document-level security](https://docs.opensearch.org/latest/security/access-control/document-level-security/)**
  and **[field-level security](https://docs.opensearch.org/latest/security/access-control/field-level-security/)**:
  which events and fields each persona may read. Units 4 and 7.
- **[User impersonation](https://docs.opensearch.org/latest/security/access-control/impersonation/)**:
  the `opendistro_security_impersonate_as` header and the
  `plugins.security.authcz.rest_impersonation_user` setting that lets the gateway act as the
  caller. Unit 7.
- **[Point in time](https://docs.opensearch.org/latest/search-plugins/searching-data/point-in-time/)**:
  sealed cursors with `search_after`, and why each one holds a context per shard. Units 4 and 8.
- **[PPL](https://docs.opensearch.org/latest/sql-and-ppl/ppl/index/)**: the piped query
  language Unit 5's open end of the dial exposes and guards.
- **[Python client](https://docs.opensearch.org/latest/clients/python-low-level/)**:
  `opensearch-py`, which every SIEM example uses.
- **[opensearch-mcp-server-py](https://github.com/opensearch-project/opensearch-mcp-server-py)**:
  the project's own MCP server, the permissive end of Unit 5's dial.

## Porting to Elasticsearch

The SIEM runs on OpenSearch so that document- and field-level security are free to use in
class. Everything else ports to Elasticsearch with renames. If your SOC runs Elasticsearch,
this is what changes:

| The course uses (OpenSearch) | Elasticsearch | Notes |
|---|---|---|
| Security plugin roles, `_plugins/_security/api/roles` | Roles via `_security/role` | Same idea: index privileges per role. |
| Document-level security: a role's DLS query | A role's `query` ([DLS and FLS](https://www.elastic.co/docs/deploy-manage/users-roles/cluster-or-deployment-auth/controlling-access-at-document-field-level)) | A **paid** feature in Elasticsearch (not in the free Basic license); check Elastic's subscription matrix for your tier. |
| Field-level security: `"fls": ["~user.email"]` | `field_security: {grant: ["*"], except: ["user.email"]}` | Same licensing as DLS. |
| `masked_fields` (a hash instead of the value) | No direct equivalent among the pages linked here | Exclude the field, or hash it at ingest. |
| Impersonation: `opendistro_security_impersonate_as` | `run_as` privilege and the `es-security-runas-user` header ([docs](https://www.elastic.co/docs/deploy-manage/users-roles/cluster-or-deployment-auth/submitting-requests-on-behalf-of-other-users)) | Not available on Elastic Serverless. |
| `create_pit` / `delete_pit`, `search_after` | `POST /<index>/_pit?keep_alive=...`, `pit.id` in the search, `DELETE /_pit` ([paginating](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/paginate-search-results)) | Unit 4's sealed cursor ports directly. |
| PPL, `_plugins/_ppl` | [ES\|QL](https://www.elastic.co/docs/reference/query-languages/esql), `_query` | A different language: Unit 5's PPL guard must be rewritten, not renamed. |
| `opensearch-py` | [`elasticsearch` Python client](https://www.elastic.co/docs/reference/elasticsearch/clients/python) | Near-identical call shapes for search, PIT and index templates. |
| opensearch-mcp-server-py | [mcp-server-elasticsearch](https://github.com/elastic/mcp-server-elasticsearch) | Deprecated by Elastic in favour of its Agent Builder MCP endpoint. |

Index templates, aggregations, `date_histogram`, `terms` and `search_after` are the same
query DSL on both. The task tools of Units 4 and 6 need no change beyond the client.

## Courses as preparation and complements

This course assumes you know what MCP is and have built a small server. If you haven't, start
with one of these; each also covers ground this course skips.

- **[MCP Course](https://huggingface.co/learn/mcp-course)** (Hugging Face, with Anthropic):
  fundamentals to a deployed app. Good preparation for Unit 1.
- **[MCP: Build Rich-Context AI Apps with Anthropic](https://www.deeplearning.ai/short-courses/mcp-build-rich-context-ai-apps-with-anthropic/)**
  (DeepLearning.AI): servers and clients connected to a desktop host. A short preparation for
  Units 1 and 2.
- **[Introduction to Model Context Protocol](https://anthropic.skilljar.com/introduction-to-model-context-protocol)**
  (Anthropic Academy): tools, resources and prompts in Python. Preparation for Unit 1;
  resources and prompts are covered more there than here.
- **[MCP for Beginners](https://github.com/microsoft/mcp-for-beginners)** (Microsoft): an open
  curriculum in several languages. A complement if your servers aren't in Python.

Where they stop, this course picks up: an enterprise estate behind the server, many callers
with different rights, finite backends, an adversary, and a review that says whether it's
ready.

## Tools used in the labs

- **[marimo](https://marimo.io)**: the reactive notebooks every lab is written in.

## Next

The optional enterprise track starts with
**[E1: Backend APIs with Approvals](E1-backend-apis-with-approvals.md)**: the capstone gateway
carries out the step it proposed, through backend APIs that fail, behind an approval and a
change window.
