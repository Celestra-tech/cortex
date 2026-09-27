# Deployment

CELESTRA Cortex runs as two deployables per environment:

```
                 ┌────────────────────────── Vercel ──────────────────────────┐
 browser ──────▶ │ dashboard (Next.js 16)  cortex.celestra.ai                  │
                 └──────────────┬─────────────────────────────────────────────┘
                                │ server-side, org API key from the session
                 ┌──────────────▼──────────── Cloud Run ──────────────────────┐
 SDKs / apps ──▶ │ api container (FastAPI)   ──:4318 OTLP──▶ otel-collector   │──▶ Cloud Trace
                 │   :8000 public            ◀─:9464 scrape─┘  sidecar        │──▶ Managed Prometheus
                 └──────┬──────────────────────────────┬──────────────────────┘
                        │ Cloud SQL connector (socket) │ Direct VPC egress
                 Cloud SQL PostgreSQL 17 + pgvector   Memorystore Redis 7 (AUTH)
```

Logs are JSON on stdout in Cloud Logging's format, correlated with traces and
carrying the `X-Request-ID` of every request.

## One-time setup

You need `gcloud`, `gh`, `jq`, `python3`, a GCP project with billing, a Vercel
account (team or personal), and DNS control of `celestra.ai`. Run each step once per
environment (`staging`, then `production`); every script is idempotent.

### 1. Google Cloud

```bash
export CORTEX_ENV=staging GCP_PROJECT_ID=celestra-cortex GITHUB_REPOSITORY=Celestra-tech/cortex
export OPENAI_API_KEY=... ANTHROPIC_API_KEY=... GEMINI_API_KEY=...   # any subset
infra/scripts/bootstrap-gcp.sh
```

This enables the APIs and creates the Artifact Registry repository, the runtime and
deployer service accounts, Workload Identity Federation for GitHub Actions (no JSON
keys), Cloud SQL (PostgreSQL 17, backups and point-in-time recovery, HA in
production), Memorystore Redis with AUTH, and the Secret Manager secrets: generated
database password and admin token, the Redis URL, provider keys, and the collector
configuration. It prints the values GitHub needs.

Both environments can share one project (every resource name carries the
environment) or use separate projects. With separate projects, set
`GCP_REGISTRY_PROJECT` to the staging project so production deploys the exact image
staging tested; the bootstrap grants production's Cloud Run agent read access.

### 2. Vercel

```bash
CORTEX_ENV=staging VERCEL_TOKEN=... VERCEL_TEAM_ID=team_... infra/vercel/configure-project.sh
```

Creates `cortex-dashboard-<env>` from `infra/vercel/project.json`, sets
`DASHBOARD_ENV`, the API URLs, a generated `DASHBOARD_SESSION_SECRET`, and attaches
the domain. The project is not connected to Git: only CI deploys it.

### 3. GitHub

```bash
GITHUB_REPOSITORY=Celestra-tech/cortex CORTEX_ENV=staging REGISTRY_SCOPE=true \
  GCP_PROJECT_ID=... GCP_WORKLOAD_IDENTITY_PROVIDER=... GCP_DEPLOYER_SERVICE_ACCOUNT=... \
  VERCEL_ORG_ID=... VERCEL_PROJECT_ID=... VERCEL_TOKEN=... \
  infra/github/configure-repo.sh
```

For production add `PRODUCTION_REVIEWERS=alice,bob` to require approval before
production deploys. The script also protects `main` (pull requests with one review
and the CI checks `checks`, `tests / suites`, `tests / migrations`, `docker (api)`,
`docker (dashboard)`, and `infra` passing) and sets the repository variable
`CORTEX_DEPLOY_ENABLED=true`, which turns on automatic staging deploys. Until it is
set, the deploy workflows skip themselves, so forks and fresh clones never try to
reach cloud accounts.

### 4. Monitoring

```bash
CORTEX_ENV=staging GCP_PROJECT_ID=... ALERT_EMAIL=oncall@celestra.ai infra/scripts/configure-monitoring.sh
```

Creates an uptime check on `/health/database` from four regions and the alert
policies in `infra/monitoring/gcp/policies/` (5xx rate, latency, provider failures,
all circuits open, uptime).

### 5. First deploy and DNS

Push to `main` (or run the API and Dashboard workflows by hand). The API deploy
maps `api.<env>` to the service; then add the DNS records:

| Record                           | Type  | Value                  |
| -------------------------------- | ----- | ---------------------- |
| `api.staging.cortex.celestra.ai` | CNAME | `ghs.googlehosted.com` |
| `staging.cortex.celestra.ai`     | CNAME | `cname.vercel-dns.com` |
| `api.cortex.celestra.ai`         | CNAME | `ghs.googlehosted.com` |
| `cortex.celestra.ai`             | CNAME | `cname.vercel-dns.com` |

Cloud Run issues the certificate once the record resolves (up to ~30 minutes).
Domain mapping requires the domain to be verified for the deployer:
`gcloud domains verify celestra.ai`.

### 6. Onboard an organization

```bash
CORTEX_ENV=production GCP_PROJECT_ID=... infra/scripts/create-organization.sh "Acme Inc" acme
```

Prints the organization's first admin API key once. From there the organization
manages its own keys through the API or SDKs (`POST /v1/api-keys`, rotate, revoke) and
signs in to the dashboard with any of them.

## Continuous delivery

| Workflow        | Trigger                             | Does                                                                                |
| --------------- | ----------------------------------- | ----------------------------------------------------------------------------------- |
| `ci.yml`        | pull requests, pushes to `main`     | lint, types, build, formatting; calls `tests.yml`; production images; infra checks  |
| `tests.yml`     | called by CI; manual                | test suites against PostgreSQL + Redis; migrations up/check/down/up                 |
| `api.yml`       | CI green on `main`; release; manual | build once per commit → migrate job → deploy → smoke test                           |
| `dashboard.yml` | CI green on `main`; release; manual | `vercel pull` → `vercel build` → `vercel deploy --prebuilt` → verify sign-in        |
| `release.yml`   | manual, on `main`                   | CI → version + notes → tag → production API → production dashboard → GitHub Release |

Automatic deploys (the `workflow_run` trigger) and the release's production deploys
run only when `CORTEX_DEPLOY_ENABLED` is `true`. Without it, a release still tags
and publishes.

The API image is built once per commit and deployed by digest; production reuses the
image staging ran. Staging smoke tests exercise every success criterion
(`infra/scripts/smoke-test.sh`); production runs the read-only `--health-only` checks.

## Releases

Run **Release** from the Actions tab on `main`. With no input, the first release
takes the version from `package.json` — **CELESTRA Cortex v1.0.0-alpha** — and later
releases bump from Conventional Commits (`feat:` minor, `fix:` patch, `!` major;
prereleases bump their counter: `1.0.0-alpha` → `1.0.0-alpha.1`). Enter a version to
override. Notes are generated by git-cliff (`cliff.toml`) and the GitHub Release is
published only after both production deploys succeed; versions with a `-` are marked
as prereleases.

## Operations

### Rollback

```bash
# API: shift traffic back to the previous revision (instant, no rebuild)
gcloud run revisions list --service cortex-api-production --region us-central1
gcloud run services update-traffic cortex-api-production --region us-central1 \
  --to-revisions cortex-api-production-00042-abc=100

# Dashboard: promote the previous production deployment
vercel rollback --token "$VERCEL_TOKEN" --scope "$VERCEL_TEAM_ID"
```

The next deploy sends 100% of traffic to the new revision again. Migrations are not
rolled back automatically; write them expand-then-contract so the previous revision
keeps working against the new schema.

### Secrets

| Secret                   | Rotate by                                                                                                  |
| ------------------------ | ---------------------------------------------------------------------------------------------------------- |
| Provider keys            | `printf %s "$KEY" \| gcloud secrets versions add cortex-<env>-openai-api-key --data-file=-`, then redeploy |
| Admin token              | add a new version of `cortex-<env>-admin-token`, then redeploy                                             |
| Database password        | `gcloud sql users set-password cortex ...`, add the new URL to `cortex-<env>-database-url`, redeploy       |
| Dashboard session secret | replace `DASHBOARD_SESSION_SECRET` in Vercel and redeploy (signs everyone out)                             |
| Organization API keys    | `POST /v1/api-keys/{id}/rotate` (old key keeps working for the grace period, default 1 hour)               |

Revisions resolve `latest` at startup, so a redeploy (or a new revision) picks up a new
secret version.

### Rate limits

Every API key gets `CORTEX_RATE_LIMIT_REQUESTS_PER_MINUTE` (600) per minute. Raise it
for one organization by setting `rate_limit_per_minute` in its `settings`:

```sql
UPDATE organizations SET settings = settings || '{"rate_limit_per_minute": 3000}' WHERE slug = 'acme';
```

### Incident response

1. `GET /health/database`, `/health/redis`, `/health/providers` show which dependency
   is failing; `/health/providers` reports each provider's circuit breaker.
2. The Grafana-equivalent views live in Cloud Monitoring → Metrics Explorer (PromQL):
   `sum by (route, status) (rate(cortex_http_requests_total{environment="production"}[5m]))`.
3. Logs: `gcloud logging read 'resource.labels.service_name="cortex-api-production" AND severity>=ERROR' --freshness 1h`.
   Every entry carries `request_id`, which clients see as `X-Request-ID` and in SDK errors.
4. Traces: Cloud Trace, filtered by `service.name:cortex-api`; provider calls appear as
   `chat <model>` client spans.
5. If a deploy caused it, roll back (above) before investigating.
