#!/usr/bin/env bash
# Deploy the API image to Cloud Run and wait until the new revision serves traffic.
#
#   CORTEX_ENV=staging GCP_PROJECT_ID=... IMAGE_TAG=<git sha> \
#     RELEASE=1.0.0-alpha REVISION=<git sha> infra/scripts/deploy-api.sh
#
# Run infra/scripts/migrate.sh with the same image first.
# shellcheck source=infra/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud python3 curl

image="$(resolve_image)"
release="${RELEASE:-${image##*:}}"
revision="${REVISION:-${image##*:}}"
manifest="$(mktemp)"
trap 'rm -f "$manifest"' EXIT

render_manifest "$INFRA_DIR/cloudrun/service.yaml" "$image" "$release" "$revision" >"$manifest"

log "deploying $SERVICE_NAME ($release) from $image"
gcloud_project run services replace "$manifest" --region "$GCP_REGION" >/dev/null

# Cortex authenticates every /v1 request with an API key; Cloud Run IAM stays open.
if ! gcloud_project run services add-iam-policy-binding "$SERVICE_NAME" --region "$GCP_REGION" \
  --member allUsers --role roles/run.invoker >/dev/null 2>&1; then
  warn "could not grant allUsers run.invoker (organization policy?); the API is not public yet"
fi

service_url="$(gcloud_project run services describe "$SERVICE_NAME" --region "$GCP_REGION" --format 'value(status.url)')"
log "service url: $service_url"

if ! gcloud_project beta run domain-mappings describe --domain "$API_HOST" --region "$GCP_REGION" >/dev/null 2>&1; then
  if gcloud_project beta run domain-mappings create --service "$SERVICE_NAME" --domain "$API_HOST" \
    --region "$GCP_REGION" >/dev/null 2>&1; then
    log "mapped $API_HOST; point its DNS at ghs.googlehosted.com (CNAME)"
  else
    warn "could not map $API_HOST; verify the domain (gcloud domains verify celestra.ai) and re-run"
  fi
fi

log "waiting for $service_url/health"
for attempt in $(seq 1 30); do
  if body="$(curl -fsS --max-time 5 "$service_url/health")"; then
    printf '%s\n' "$body"
    curl -fsS --max-time 10 "$service_url/health/database" >/dev/null ||
      die "revision is up but /health/database is failing"
    log "$SERVICE_NAME is serving $release"
    exit 0
  fi
  sleep $((attempt < 10 ? 2 : 5))
done
die "$SERVICE_NAME did not become healthy"
