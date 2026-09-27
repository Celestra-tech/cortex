# Roadmap

Cortex grows in three milestones. Each one ships as a usable system, not a
collection of partial features. Scope, deliverables, and exit criteria for
every item are in [docs/roadmap.md](docs/roadmap.md).

| Milestone           | Theme                    | Scope                                          | Status      |
| ------------------- | ------------------------ | ---------------------------------------------- | ----------- |
| **v0.1 Foundation** | The core substrate       | Router, Memory, Knowledge                      | Complete    |
| **v0.2**            | Intelligence and insight | Decision Engine, Observatory                   | In progress |
| **v1.0**            | Enterprise readiness     | Enterprise SDK, Governance, Production Runtime | In progress |

## v0.1 Foundation — complete

- **Router.** Multi-provider routing with objectives, organization policy,
  fallback, circuit breakers, and a per-attempt execution log.
- **Memory.** Conversations, a Redis hot path over PostgreSQL, and ranked
  long-term memories.
- **Knowledge.** Ingestion, semantic chunking, hybrid pgvector and full-text
  retrieval, citations, and confidence.

## v0.2 — in progress

- **Observatory.** Shipped: execution log, usage and cost overview, live event
  stream, Prometheus metrics, OpenTelemetry traces. Next: cost budgets and
  alerts, retrieval quality dashboards, trace-linked execution views.
- **Decision Engine.** Planned: declarative policies that decide model, tools,
  and escalation per request from cost, risk, and confidence signals, with
  every decision explained and auditable.

## v1.0 — in progress

- **Production Runtime.** Alpha: hardened container images, Cloud Run and
  Vercel deployment, migrations as jobs, monitoring and alerting, release
  automation. Next: native provider streaming, horizontal event fan-out,
  self-hosted Kubernetes manifests.
- **Enterprise SDK.** Planned: published packages on npm and PyPI, SSO-backed
  service identities, typed webhooks, and long-term API stability guarantees.
- **Governance.** Planned: audit log API, data retention and residency
  controls, PII redaction, and per-organization model allowlists enforced by
  policy.

## Versioning

The platform currently ships as `1.0.0-alpha` pre-releases while the milestones
above are completed. Pre-releases may change APIs; each change is recorded in
the [changelog](CHANGELOG.md). Stable `1.0.0` follows the completion of every
v1.0 item.

## Proposing changes

Major features begin as an RFC; see [CONTRIBUTING.md](CONTRIBUTING.md#rfcs).
Roadmap discussions happen in GitHub Discussions and issues labeled `roadmap`.
