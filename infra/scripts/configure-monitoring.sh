#!/usr/bin/env bash
# Create or update the environment's uptime check, alert policies, and (optionally) an
# email notification channel in Cloud Monitoring. Idempotent.
#
#   CORTEX_ENV=production GCP_PROJECT_ID=... ALERT_EMAIL=oncall@celestra.ai \
#     infra/scripts/configure-monitoring.sh
#
# Policies live in infra/monitoring/gcp/policies/ and mirror infra/monitoring/alerts.yml.
# shellcheck source=infra/scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud python3

uptime_name="cortex-api-${CORTEX_ENV}-health"
log "uptime check $uptime_name -> ${API_URL}/health/database"
uptime_id="$(gcloud_project monitoring uptime list-configs \
  --filter "displayName=\"${uptime_name}\"" --format 'value(name.basename())' | head -n1)"
if [ -z "$uptime_id" ]; then
  gcloud_project monitoring uptime create "$uptime_name" \
    --resource-type uptime-url \
    --resource-labels "host=${API_HOST},project_id=${GCP_PROJECT_ID}" \
    --protocol https --path /health/database --port 443 \
    --period 1 --timeout 10 \
    --regions usa-oregon,usa-iowa,usa-virginia,europe >/dev/null
  uptime_id="$(gcloud_project monitoring uptime list-configs \
    --filter "displayName=\"${uptime_name}\"" --format 'value(name.basename())' | head -n1)"
fi
[ -n "$uptime_id" ] || die "uptime check $uptime_name was not created"

channel=""
if [ -n "${ALERT_EMAIL:-}" ]; then
  channel_name="Cortex on-call (${ALERT_EMAIL})"
  channel="$(gcloud_project alpha monitoring channels list \
    --filter "displayName=\"${channel_name}\"" --format 'value(name)' | head -n1)"
  if [ -z "$channel" ]; then
    channel="$(gcloud_project alpha monitoring channels create --type email \
      --display-name "$channel_name" --channel-labels "email_address=${ALERT_EMAIL}" \
      --format 'value(name)')"
    log "created notification channel for $ALERT_EMAIL"
  fi
else
  warn "ALERT_EMAIL is not set; policies will open incidents without notifying anyone"
fi

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
for policy in "$INFRA_DIR"/monitoring/gcp/policies/*.yaml; do
  ENVIRONMENT="$CORTEX_ENV" SERVICE_NAME="$SERVICE_NAME" API_HOST="$API_HOST" UPTIME_CHECK_ID="$uptime_id" \
    python3 "$INFRA_DIR/cloudrun/render.py" "$policy" >"$rendered"
  if [ -n "$channel" ]; then
    printf 'notificationChannels:\n  - %s\n' "$channel" >>"$rendered"
  fi
  display_name="$(sed -n 's/^displayName: "\(.*\)"$/\1/p' "$rendered")"
  existing="$(gcloud_project alpha monitoring policies list \
    --filter "displayName=\"${display_name}\"" --format 'value(name)' | head -n1)"
  if [ -n "$existing" ]; then
    gcloud_project alpha monitoring policies update "$existing" --policy-from-file "$rendered" >/dev/null
    log "updated policy: $display_name"
  else
    gcloud_project alpha monitoring policies create --policy-from-file "$rendered" >/dev/null
    log "created policy: $display_name"
  fi
done
