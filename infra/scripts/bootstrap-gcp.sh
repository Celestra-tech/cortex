#!/usr/bin/env bash
# Provision everything an environment needs on Google Cloud. Idempotent: re-running
# only creates what is missing and never rotates existing credentials.
#
#   CORTEX_ENV=staging GCP_PROJECT_ID=celestra-cortex GITHUB_REPOSITORY=Celestra-tech/cortex \
#     infra/scripts/bootstrap-gcp.sh
#
# Optional provider keys are stored when present in the environment:
#   OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY
#
# Creates: APIs, Artifact Registry, runtime + deployer service accounts, GitHub Workload
# Identity Federation, Cloud SQL (PostgreSQL 17 + pgvector), Memorystore Redis with AUTH,
# and Secret Manager secrets (database URL, Redis URL, admin token, collector config).
# shellcheck source=infra/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

: "${GITHUB_REPOSITORY:?set GITHUB_REPOSITORY (owner/repo) for Workload Identity Federation}"
require_cmd gcloud python3

PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT_ID" --format='value(projectNumber)')"
[ -n "$PROJECT_NUMBER" ] || die "cannot read project $GCP_PROJECT_ID"

bind_project_role() {
  gcloud_project projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member "$1" --role "$2" --condition None >/dev/null
}

ensure_service_account() {
  local name="$1" display="$2"
  if ! gcloud_project iam service-accounts describe "${name}@${GCP_PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud_project iam service-accounts create "$name" --display-name "$display" >/dev/null
    log "created service account $name"
  fi
}

log "enabling APIs in $GCP_PROJECT_ID"
gcloud_project services enable \
  run.googleapis.com sqladmin.googleapis.com redis.googleapis.com compute.googleapis.com \
  secretmanager.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com \
  monitoring.googleapis.com cloudtrace.googleapis.com logging.googleapis.com

log "artifact registry $REGISTRY"
if ! gcloud --project "$GCP_REGISTRY_PROJECT" artifacts repositories describe "$REGISTRY_REPOSITORY" \
  --location "$GCP_REGION" >/dev/null 2>&1; then
  gcloud --project "$GCP_REGISTRY_PROJECT" --quiet services enable artifactregistry.googleapis.com
  gcloud --project "$GCP_REGISTRY_PROJECT" --quiet artifacts repositories create "$REGISTRY_REPOSITORY" \
    --location "$GCP_REGION" --repository-format docker \
    --description "CELESTRA Cortex container images" >/dev/null
fi
if [ "$GCP_REGISTRY_PROJECT" != "$GCP_PROJECT_ID" ]; then
  # Cloud Run pulls with this project's service agent.
  gcloud --project "$GCP_REGISTRY_PROJECT" --quiet artifacts repositories add-iam-policy-binding \
    "$REGISTRY_REPOSITORY" --location "$GCP_REGION" \
    --member "serviceAccount:service-${PROJECT_NUMBER}@serverless-robot-prod.iam.gserviceaccount.com" \
    --role roles/artifactregistry.reader >/dev/null
fi

log "service accounts"
ensure_service_account "$RUNTIME_SA_NAME" "Cortex API (${CORTEX_ENV})"
ensure_service_account "$DEPLOYER_SA_NAME" "Cortex GitHub Actions deployer"
for role in roles/cloudsql.client roles/monitoring.metricWriter roles/cloudtrace.agent roles/logging.logWriter; do
  bind_project_role "serviceAccount:${RUNTIME_SERVICE_ACCOUNT}" "$role"
done
for role in roles/run.admin roles/compute.networkViewer; do
  bind_project_role "serviceAccount:${DEPLOYER_SERVICE_ACCOUNT}" "$role"
done
gcloud_project iam service-accounts add-iam-policy-binding "$RUNTIME_SERVICE_ACCOUNT" \
  --member "serviceAccount:${DEPLOYER_SERVICE_ACCOUNT}" --role roles/iam.serviceAccountUser >/dev/null
gcloud --project "$GCP_REGISTRY_PROJECT" --quiet artifacts repositories add-iam-policy-binding \
  "$REGISTRY_REPOSITORY" --location "$GCP_REGION" \
  --member "serviceAccount:${DEPLOYER_SERVICE_ACCOUNT}" --role roles/artifactregistry.writer >/dev/null

log "workload identity federation for $GITHUB_REPOSITORY"
if ! gcloud_project iam workload-identity-pools describe "$WIF_POOL" --location global >/dev/null 2>&1; then
  gcloud_project iam workload-identity-pools create "$WIF_POOL" --location global \
    --display-name "GitHub Actions" >/dev/null
fi
if ! gcloud_project iam workload-identity-pools providers describe "$WIF_PROVIDER" \
  --workload-identity-pool "$WIF_POOL" --location global >/dev/null 2>&1; then
  gcloud_project iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
    --workload-identity-pool "$WIF_POOL" --location global \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.environment=assertion.environment" \
    --attribute-condition "assertion.repository == '${GITHUB_REPOSITORY}'" >/dev/null
fi
gcloud_project iam service-accounts add-iam-policy-binding "$DEPLOYER_SERVICE_ACCOUNT" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_REPOSITORY}" >/dev/null

log "cloud sql instance $SQL_INSTANCE"
if ! gcloud_project sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud_project sql instances create "$SQL_INSTANCE" \
    --database-version POSTGRES_17 --edition ENTERPRISE --tier "$SQL_TIER" \
    --region "$GCP_REGION" --availability-type "$SQL_AVAILABILITY" \
    --storage-type SSD --storage-size 20 --storage-auto-increase \
    --backup-start-time 03:00 --enable-point-in-time-recovery --retained-backups-count 14 \
    --maintenance-window-day SUN --maintenance-window-hour 4 \
    --database-flags "max_connections=${SQL_MAX_CONNECTIONS}" \
    --ssl-mode ENCRYPTED_ONLY --deletion-protection \
    --labels "app=cortex,environment=${CORTEX_ENV}"
fi
if ! gcloud_project sql databases describe "$SQL_DATABASE" --instance "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud_project sql databases create "$SQL_DATABASE" --instance "$SQL_INSTANCE" >/dev/null
fi
if ! secret_exists "$SECRET_DATABASE_URL"; then
  # A new password is only ever set together with the secret that carries it.
  db_password="$(random_secret)"
  if gcloud_project sql users list --instance "$SQL_INSTANCE" --format 'value(name)' | grep -qx "$SQL_USER"; then
    gcloud_project sql users set-password "$SQL_USER" --instance "$SQL_INSTANCE" --password "$db_password" >/dev/null
  else
    gcloud_project sql users create "$SQL_USER" --instance "$SQL_INSTANCE" --password "$db_password" >/dev/null
  fi
  # asyncpg reads the Cloud SQL connector's unix socket from the `host` query parameter.
  printf 'postgresql+asyncpg://%s:%s@localhost/%s?host=/cloudsql/%s' \
    "$SQL_USER" "$db_password" "$SQL_DATABASE" "$CLOUDSQL_CONNECTION" | put_secret "$SECRET_DATABASE_URL"
  unset db_password
fi

log "memorystore redis $REDIS_INSTANCE"
if ! gcloud_project redis instances describe "$REDIS_INSTANCE" --region "$GCP_REGION" >/dev/null 2>&1; then
  gcloud_project redis instances create "$REDIS_INSTANCE" --region "$GCP_REGION" \
    --tier "$REDIS_TIER" --size "$REDIS_SIZE_GB" --redis-version redis_7_2 \
    --network "projects/${GCP_PROJECT_ID}/global/networks/${VPC_NETWORK}" \
    --enable-auth --labels "app=cortex,environment=${CORTEX_ENV}"
fi
if ! secret_exists "$SECRET_REDIS_URL"; then
  redis_host="$(gcloud_project redis instances describe "$REDIS_INSTANCE" --region "$GCP_REGION" --format 'value(host)')"
  redis_port="$(gcloud_project redis instances describe "$REDIS_INSTANCE" --region "$GCP_REGION" --format 'value(port)')"
  redis_auth="$(gcloud_project redis instances get-auth-string "$REDIS_INSTANCE" --region "$GCP_REGION" --format 'value(authString)')"
  printf 'redis://:%s@%s:%s/0' "$redis_auth" "$redis_host" "$redis_port" | put_secret "$SECRET_REDIS_URL"
  unset redis_auth
fi

log "secrets"
if ! secret_exists "$SECRET_ADMIN_TOKEN"; then
  random_secret | put_secret "$SECRET_ADMIN_TOKEN"
fi
put_secret "$SECRET_COLLECTOR_CONFIG" <"$INFRA_DIR/monitoring/otel-collector.cloudrun.yaml"
for pair in "OPENAI_API_KEY=openai-api-key" "ANTHROPIC_API_KEY=anthropic-api-key" "GEMINI_API_KEY=gemini-api-key"; do
  var="${pair%%=*}"
  if [ -n "${!var:-}" ]; then
    printf '%s' "${!var}" | put_secret "$(secret_id "${pair#*=}")"
  fi
done

log "secret access for $RUNTIME_SERVICE_ACCOUNT"
for suffix in database-url redis-url admin-token otel-collector-config openai-api-key anthropic-api-key gemini-api-key; do
  id="$(secret_id "$suffix")"
  if gcloud_project secrets describe "$id" >/dev/null 2>&1; then
    gcloud_project secrets add-iam-policy-binding "$id" \
      --member "serviceAccount:${RUNTIME_SERVICE_ACCOUNT}" --role roles/secretmanager.secretAccessor >/dev/null
  fi
done
# The deploy workflow's smoke test bootstraps a throwaway organization with the admin token.
gcloud_project secrets add-iam-policy-binding "$SECRET_ADMIN_TOKEN" \
  --member "serviceAccount:${DEPLOYER_SERVICE_ACCOUNT}" --role roles/secretmanager.secretAccessor >/dev/null

if [ -z "${OPENAI_API_KEY:-}${ANTHROPIC_API_KEY:-}${GEMINI_API_KEY:-}" ] &&
  ! secret_exists "$(secret_id openai-api-key)" &&
  ! secret_exists "$(secret_id anthropic-api-key)" &&
  ! secret_exists "$(secret_id gemini-api-key)"; then
  warn "no provider key is stored; chat completions will fail until one is added"
fi

cat <<EOF

Bootstrap of ${CORTEX_ENV} complete. GitHub environment '${CORTEX_ENV}' variables
(infra/github/configure-repo.sh sets these):

  GCP_PROJECT_ID=${GCP_PROJECT_ID}
  GCP_REGION=${GCP_REGION}
  GCP_REGISTRY_PROJECT=${GCP_REGISTRY_PROJECT}
  GCP_WORKLOAD_IDENTITY_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}
  GCP_DEPLOYER_SERVICE_ACCOUNT=${DEPLOYER_SERVICE_ACCOUNT}

Next: infra/scripts/deploy-api.sh (or push to main), then map ${API_HOST}:
  gcloud beta run domain-mappings create --service ${SERVICE_NAME} --domain ${API_HOST} \\
    --region ${GCP_REGION} --project ${GCP_PROJECT_ID}
EOF
