#!/usr/bin/env bash
# Shared configuration for the Cortex deployment scripts. Sourced, not executed.
#
# Required:  CORTEX_ENV       staging | production
#            GCP_PROJECT_ID   project that hosts the environment
# Optional:  GCP_REGION             default us-central1
#            GCP_REGISTRY_PROJECT   project that hosts Artifact Registry (default GCP_PROJECT_ID);
#                                   share one registry so production deploys the image staging tested
#
# Every resource name is derived here so the scripts, workflows, and docs agree.
# shellcheck disable=SC2034  # variables are consumed by the scripts that source this file

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPTS_DIR")"
REPO_ROOT="$(dirname "$INFRA_DIR")"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() {
  printf '\033[1;31merror:\033[0m %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' is required but not installed"
  done
}

: "${CORTEX_ENV:?set CORTEX_ENV to staging or production}"
: "${GCP_PROJECT_ID:?set GCP_PROJECT_ID}"
GCP_REGION="${GCP_REGION:-us-central1}"
GCP_REGISTRY_PROJECT="${GCP_REGISTRY_PROJECT:-$GCP_PROJECT_ID}"

case "$CORTEX_ENV" in
  staging)
    API_HOST="api.staging.cortex.celestra.ai"
    DASHBOARD_URL="https://staging.cortex.celestra.ai"
    MIN_INSTANCES=0
    MAX_INSTANCES=5
    API_CPU=1
    API_MEMORY=1Gi
    SQL_TIER=db-custom-1-3840
    SQL_AVAILABILITY=zonal
    SQL_MAX_CONNECTIONS=100
    REDIS_TIER=basic
    REDIS_SIZE_GB=1
    ;;
  production)
    API_HOST="api.cortex.celestra.ai"
    DASHBOARD_URL="https://cortex.celestra.ai"
    MIN_INSTANCES=1
    MAX_INSTANCES=20
    API_CPU=2
    API_MEMORY=2Gi
    SQL_TIER=db-custom-2-7680
    SQL_AVAILABILITY=regional
    # 20 instances x (pool 10 + overflow 5) = 300, plus the migrate job and operators.
    SQL_MAX_CONNECTIONS=400
    REDIS_TIER=standard
    REDIS_SIZE_GB=1
    ;;
  *) die "CORTEX_ENV must be staging or production, got '$CORTEX_ENV'" ;;
esac

API_URL="https://${API_HOST}"

SERVICE_NAME="cortex-api-${CORTEX_ENV}"
JOB_NAME="cortex-migrate-${CORTEX_ENV}"
SQL_INSTANCE="cortex-${CORTEX_ENV}"
SQL_DATABASE="celestra_cortex"
SQL_USER="cortex"
CLOUDSQL_CONNECTION="${GCP_PROJECT_ID}:${GCP_REGION}:${SQL_INSTANCE}"
REDIS_INSTANCE="cortex-${CORTEX_ENV}"
VPC_NETWORK="${VPC_NETWORK:-default}"
VPC_SUBNET="${VPC_SUBNET:-default}"

RUNTIME_SA_NAME="cortex-api-${CORTEX_ENV}"
RUNTIME_SERVICE_ACCOUNT="${RUNTIME_SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_SA_NAME="cortex-deployer"
DEPLOYER_SERVICE_ACCOUNT="${DEPLOYER_SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
WIF_POOL="github"
WIF_PROVIDER="github-actions"

REGISTRY_REPOSITORY="cortex"
REGISTRY="${GCP_REGION}-docker.pkg.dev/${GCP_REGISTRY_PROJECT}/${REGISTRY_REPOSITORY}"
API_IMAGE_REPO="${REGISTRY}/cortex-api"

COLLECTOR_IMAGE="otel/opentelemetry-collector-contrib:0.120.0"

# Secret Manager ids. Provider keys are optional: the router skips unconfigured providers.
secret_id() { printf 'cortex-%s-%s' "$CORTEX_ENV" "$1"; }
SECRET_DATABASE_URL="$(secret_id database-url)"
SECRET_REDIS_URL="$(secret_id redis-url)"
SECRET_ADMIN_TOKEN="$(secret_id admin-token)"
SECRET_COLLECTOR_CONFIG="$(secret_id otel-collector-config)"
# env var name -> secret suffix; required ones must exist before a deploy.
REQUIRED_SECRETS=(
  "CORTEX_DATABASE_URL=database-url"
  "CORTEX_REDIS_URL=redis-url"
  "CORTEX_ADMIN_TOKEN=admin-token"
)
OPTIONAL_SECRETS=(
  "CORTEX_OPENAI_API_KEY=openai-api-key"
  "CORTEX_ANTHROPIC_API_KEY=anthropic-api-key"
  "CORTEX_GEMINI_API_KEY=gemini-api-key"
)

gcloud_project() { gcloud --project "$GCP_PROJECT_ID" --quiet "$@"; }

secret_exists() {
  gcloud_project secrets versions describe latest --secret "$1" >/dev/null 2>&1
}

# Adds a version when the value changed; creates the secret when missing.
# Reads the value from stdin so it never appears in argv or shell history.
put_secret() {
  local id="$1" value current
  value="$(cat)"
  [ -n "$value" ] || die "refusing to store an empty value in $id"
  if ! gcloud_project secrets describe "$id" >/dev/null 2>&1; then
    gcloud_project secrets create "$id" --replication-policy automatic \
      --labels "app=cortex,environment=${CORTEX_ENV}" >/dev/null
  fi
  current="$(gcloud_project secrets versions access latest --secret "$id" 2>/dev/null || true)"
  if [ "$current" != "$value" ]; then
    printf '%s' "$value" | gcloud_project secrets versions add "$id" --data-file=- >/dev/null
    log "stored a new version of $id"
  fi
}

random_secret() {
  # 48 random bytes, URL-safe, no padding: safe in URLs and headers.
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

# `--secret NAME=ID` flags for render.py: required secrets must exist, optional ones
# are included only when they have a version.
secret_flags() {
  local entry name suffix id
  for entry in "${REQUIRED_SECRETS[@]}"; do
    name="${entry%%=*}" suffix="${entry#*=}" id="$(secret_id "$suffix")"
    secret_exists "$id" || die "missing secret $id (run infra/scripts/bootstrap-gcp.sh)"
    printf -- '--secret\n%s=%s\n' "$name" "$id"
  done
  for entry in "${OPTIONAL_SECRETS[@]}"; do
    name="${entry%%=*}" suffix="${entry#*=}" id="$(secret_id "$suffix")"
    if secret_exists "$id"; then
      printf -- '--secret\n%s=%s\n' "$name" "$id"
    fi
  done
}

# Label values allow [a-z0-9_-], at most 63 characters.
label_value() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-' | cut -c1-63
}

# Resolves the image to deploy: IMAGE (full reference) or IMAGE_TAG in the registry.
resolve_image() {
  if [ -n "${IMAGE:-}" ]; then
    printf '%s' "$IMAGE"
  elif [ -n "${IMAGE_TAG:-}" ]; then
    printf '%s:%s' "$API_IMAGE_REPO" "$IMAGE_TAG"
  else
    die "set IMAGE (full reference) or IMAGE_TAG"
  fi
}

# render_manifest TEMPLATE IMAGE RELEASE REVISION: prints the manifest with the
# environment's env file, release metadata, and Secret Manager references.
render_manifest() {
  local template="$1" image="$2" release="$3" revision="$4" flag listing
  local flags=()
  # bash 3.2 (macOS) has no mapfile; the listing is captured first so a missing
  # required secret stops the render.
  listing="$(secret_flags)"
  while IFS= read -r flag; do
    [ -n "$flag" ] && flags+=("$flag")
  done <<<"$listing"
  SERVICE_NAME="$SERVICE_NAME" JOB_NAME="$JOB_NAME" ENVIRONMENT="$CORTEX_ENV" \
    RELEASE_LABEL="$(label_value "$release")" IMAGE="$image" \
    MIN_INSTANCES="$MIN_INSTANCES" MAX_INSTANCES="$MAX_INSTANCES" \
    API_CPU="$API_CPU" API_MEMORY="$API_MEMORY" \
    CLOUDSQL_CONNECTION="$CLOUDSQL_CONNECTION" VPC_NETWORK="$VPC_NETWORK" VPC_SUBNET="$VPC_SUBNET" \
    RUNTIME_SERVICE_ACCOUNT="$RUNTIME_SERVICE_ACCOUNT" COLLECTOR_IMAGE="$COLLECTOR_IMAGE" \
    COLLECTOR_CONFIG_SECRET="$SECRET_COLLECTOR_CONFIG" \
    python3 "$INFRA_DIR/cloudrun/render.py" "$template" \
    --env-file "$INFRA_DIR/cloudrun/${CORTEX_ENV}.env.yaml" \
    --set "CORTEX_RELEASE=${release}" \
    --set "CORTEX_REVISION=${revision}" \
    --set "CORTEX_GCP_PROJECT_ID=${GCP_PROJECT_ID}" \
    "${flags[@]}"
}
