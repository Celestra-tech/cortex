#!/usr/bin/env bash
# Apply database migrations for an environment with the given image, as a Cloud Run job.
#
#   CORTEX_ENV=staging GCP_PROJECT_ID=... IMAGE_TAG=<git sha> infra/scripts/migrate.sh
#
# Run before deploy-api.sh: migrations are backwards compatible with the running
# revision, so the old revision keeps serving while they apply.
# shellcheck source=infra/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud python3

image="$(resolve_image)"
release="${RELEASE:-${image##*:}}"
revision="${REVISION:-${image##*:}}"
manifest="$(mktemp)"
trap 'rm -f "$manifest"' EXIT

render_manifest "$INFRA_DIR/cloudrun/migrate-job.yaml" "$image" "$release" "$revision" >"$manifest"

log "updating job $JOB_NAME to $image"
gcloud_project run jobs replace "$manifest" --region "$GCP_REGION" >/dev/null

log "running migrations"
if ! gcloud_project run jobs execute "$JOB_NAME" --region "$GCP_REGION" --wait; then
  die "migrations failed; logs: gcloud logging read 'resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${JOB_NAME}\"' --project ${GCP_PROJECT_ID} --freshness 1h --limit 100"
fi
log "migrations applied"
