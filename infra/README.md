# infra

Everything needed to deploy and operate CELESTRA Cortex. Production images live in
[`docker/`](../docker). The end-to-end procedure is in
[docs/deployment.md](../docs/deployment.md).

| Path          | Contents                                                                                              |
| ------------- | ----------------------------------------------------------------------------------------------------- |
| `cloudrun/`   | Cloud Run service + migrate job templates, per-environment settings, the manifest renderer            |
| `vercel/`     | Dashboard project settings and `configure-project.sh` (env vars, session secret, domain)              |
| `monitoring/` | OpenTelemetry collector configs, Prometheus alerts, Grafana dashboard, Cloud Monitoring policies      |
| `github/`     | `configure-repo.sh`: environments, variables, secrets, branch protection                              |
| `scripts/`    | `bootstrap-gcp`, `migrate`, `deploy-api`, `configure-monitoring`, `create-organization`, `smoke-test` |
| `postgres/`   | Init scripts mounted by `docker-compose.yml` (extensions, test database)                              |
| `redis/`      | `redis.conf` mounted by `docker-compose.yml`                                                          |

## Environments

|             | Development                 | Staging                                                           | Production                                                              |
| ----------- | --------------------------- | ----------------------------------------------------------------- | ----------------------------------------------------------------------- |
| API         | `http://localhost:8000`     | `https://api.staging.cortex.celestra.ai`                          | `https://api.cortex.celestra.ai`                                        |
| Dashboard   | `http://localhost:3000`     | `https://staging.cortex.celestra.ai`                              | `https://cortex.celestra.ai`                                            |
| Runs on     | docker compose / `pnpm dev` | Cloud Run `cortex-api-staging`, Vercel `cortex-dashboard-staging` | Cloud Run `cortex-api-production`, Vercel `cortex-dashboard-production` |
| Data        | local PostgreSQL + Redis    | Cloud SQL + Memorystore `cortex-staging`                          | Cloud SQL (HA) + Memorystore (HA) `cortex-production`                   |
| Deployed by | —                           | every green commit on `main`                                      | the Release workflow                                                    |

Settings are typed and validated at startup. In staging and production the API
refuses to start with API-key auth off, wildcard CORS, a default database password,
or a short admin token; production additionally requires `https://` CORS origins.
Non-secret settings live in `cloudrun/<env>.env.yaml`; secrets live in Secret Manager
(`cortex-<env>-database-url`, `-redis-url`, `-admin-token`, `-openai-api-key`,
`-anthropic-api-key`, `-gemini-api-key`, `-otel-collector-config`) and are referenced,
never copied, by the rendered manifests.

## Local observability

```bash
docker compose -f docker-compose.yml -f infra/monitoring/docker-compose.monitoring.yml up --build
```

Prometheus on `:9090` (with `monitoring/alerts.yml` loaded), Grafana on `:3001`
(dashboard "Cortex API"), and Jaeger on `:16686` receiving the API's traces.

## Docker compose data

PostgreSQL init scripts only run when the `postgres-data` volume is empty. To re-run
them locally: `docker compose down -v && docker compose up`. Application schema is
owned by Alembic in `apps/api/migrations`, never by these scripts.

## Tests

`python3 infra/cloudrun/test_render.py` renders every manifest and alert policy. CI
(`ci.yml`, job `infra`) also runs shellcheck, actionlint, `promtool check rules`,
and `otelcol validate` on the collector configs.
