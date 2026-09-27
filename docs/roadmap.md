# Roadmap in detail

This document expands [ROADMAP.md](../ROADMAP.md) with the deliverables and
exit criteria for each milestone. An item is done when its exit criteria hold
in CI and in a staging deployment, and its documentation is published.

Status values: **Shipped**, **In progress**, **Planned**.

## v0.1 Foundation

The substrate every later capability builds on.

### Router — Shipped

| Deliverable                                                                   | Status  |
| ----------------------------------------------------------------------------- | ------- |
| Provider adapters: OpenAI, Anthropic, Gemini, OpenAI-compatible hosts         | Shipped |
| Model catalog with capabilities, context windows, and pricing                 | Shipped |
| Objectives: `balanced`, `quality`, `speed`, `cost`                            | Shipped |
| Routing modes: `auto`, `preferred`, `strict`                                  | Shipped |
| Organization routing policy (allowed providers, blocked models, cost ceiling) | Shipped |
| Fallback executor with transient retries and circuit breakers                 | Shipped |
| Execution log: one row per attempt with latency, tokens, and cost             | Shipped |
| Server-sent event streaming protocol                                          | Shipped |

Exit criteria: a provider outage degrades to the next provider without a
client-visible error; every attempt is queryable through `/v1/executions`.

### Memory — Shipped

| Deliverable                                                    | Status  |
| -------------------------------------------------------------- | ------- |
| Conversations and immutable messages                           | Shipped |
| Redis session window with PostgreSQL rebuild on miss           | Shipped |
| Long-term memories: episodic, semantic, procedural, preference | Shipped |
| Ranked recall by relevance, importance, and recency            | Shipped |
| Memory grounding inside completions                            | Shipped |

Exit criteria: losing Redis never loses data; context assembly stays within
the model's window.

### Knowledge — Shipped

| Deliverable                                                         | Status  |
| ------------------------------------------------------------------- | ------- |
| Ingestion: PDF, DOCX, Markdown, plain text                          | Shipped |
| Structure-aware semantic chunking                                   | Shipped |
| Pluggable embeddings (local, OpenAI, Gemini) with embedding spaces  | Shipped |
| Hybrid retrieval: pgvector HNSW and full-text search fused with RRF | Shipped |
| Numbered citations, confidence scoring, and a retrieval log         | Shipped |
| Knowledge grounding inside completions                              | Shipped |

Exit criteria: grounded answers cite their sources; changing embedding models
never compares incompatible vectors.

## v0.2

### Observatory — In progress

| Deliverable                                                | Status  |
| ---------------------------------------------------------- | ------- |
| Usage, latency, provider mix, and cost overview            | Shipped |
| Live event stream over WebSocket, authenticated by API key | Shipped |
| Prometheus metrics and OpenTelemetry traces                | Shipped |
| Dashboard: health and executions                           | Shipped |
| Cost budgets with alerts per organization                  | Planned |
| Retrieval quality dashboards from the knowledge query log  | Planned |
| Execution views linked to traces                           | Planned |

Exit criteria: an operator can explain any request's cost, latency, and
provider choice from the dashboard alone.

### Decision Engine — Planned

A policy layer that decides how each request is handled, based on signals
Cortex already collects.

| Deliverable                                                       | Status  |
| ----------------------------------------------------------------- | ------- |
| Declarative policy format, versioned per organization             | Planned |
| Signals: cost, latency, retrieval confidence, risk classification | Planned |
| Decisions: model tier, grounding, escalation, refusal             | Planned |
| Explanations recorded with every decision                         | Planned |
| Policy simulation against historical executions                   | Planned |

Exit criteria: every decision is reproducible from its recorded inputs and
policy version. Requires an RFC before implementation.

## v1.0

### Production Runtime — In progress (alpha)

| Deliverable                                                         | Status  |
| ------------------------------------------------------------------- | ------- |
| Non-root multi-stage images for the API and dashboard               | Shipped |
| Cloud Run and Vercel deployment with staging and production         | Shipped |
| Migrations as a separate job; startup validation of unsafe settings | Shipped |
| API keys with roles, expiry, and rotation; rate limiting            | Shipped |
| Monitoring, alerting, and release automation                        | Shipped |
| Native provider streaming                                           | Planned |
| Horizontal fan-out for the live event stream                        | Planned |
| Kubernetes manifests for self-hosting                               | Planned |

### Enterprise SDK — Planned

| Deliverable                                                 | Status  |
| ----------------------------------------------------------- | ------- |
| Published packages on npm and PyPI with semantic versioning | Planned |
| Service identities backed by SSO                            | Planned |
| Typed webhooks for platform events                          | Planned |
| API stability policy and deprecation windows                | Planned |

### Governance — Planned

| Deliverable                                        | Status  |
| -------------------------------------------------- | ------- |
| Audit log API over the existing `audit_logs` table | Planned |
| Retention and residency controls per organization  | Planned |
| PII detection and redaction before provider calls  | Planned |
| Model allowlists enforced by the Decision Engine   | Planned |

Exit criteria for v1.0: every item above is shipped, the public API is frozen
under the stability policy, and a production deployment has run the release
process end to end.
