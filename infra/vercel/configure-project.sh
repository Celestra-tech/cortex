#!/usr/bin/env bash
# Create or update the environment's Vercel project for the dashboard: build settings
# (infra/vercel/project.json), environment variables, a generated session secret, and
# the custom domain. Idempotent; an existing session secret is never replaced.
#
#   CORTEX_ENV=staging VERCEL_TOKEN=... [VERCEL_TEAM_ID=team_...] infra/vercel/configure-project.sh
#
# One project per environment (cortex-dashboard-staging, cortex-dashboard-production),
# each deployed to its production target by .github/workflows/dashboard.yml. The projects
# are not connected to Git, so only CI deploys them.
set -euo pipefail

: "${CORTEX_ENV:?set CORTEX_ENV to staging or production}"
: "${VERCEL_TOKEN:?set VERCEL_TOKEN}"
for cmd in curl jq python3; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "error: '$cmd' is required" >&2
    exit 1
  }
done

case "$CORTEX_ENV" in
  staging)
    domain="staging.cortex.celestra.ai"
    api_url="https://api.staging.cortex.celestra.ai"
    ;;
  production)
    domain="cortex.celestra.ai"
    api_url="https://api.cortex.celestra.ai"
    ;;
  *)
    echo "error: CORTEX_ENV must be staging or production" >&2
    exit 1
    ;;
esac
project="cortex-dashboard-${CORTEX_ENV}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
team_query="${VERCEL_TEAM_ID:+?teamId=${VERCEL_TEAM_ID}}"
log() { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }

response="$(mktemp)"
trap 'rm -f "$response"' EXIT
# vercel METHOD PATH [JSON]; prints the HTTP status, body in $response.
vercel_api() {
  local method="$1" path="$2" data="${3:-}" sep="?"
  case "$path" in *\?*) sep="&" ;; esac
  local url="https://api.vercel.com${path}"
  [ -n "${VERCEL_TEAM_ID:-}" ] && url="${url}${sep}teamId=${VERCEL_TEAM_ID}"
  curl -sS -o "$response" -w '%{http_code}' -X "$method" "$url" \
    -H "Authorization: Bearer ${VERCEL_TOKEN}" -H 'Content-Type: application/json' \
    ${data:+-d "$data"}
}
expect() {
  local got="$1" what="$2"
  shift 2
  for ok in "$@"; do [ "$got" = "$ok" ] && return 0; done
  echo "error: $what failed with HTTP $got: $(cat "$response")" >&2
  exit 1
}

log "project $project"
status="$(vercel_api GET "/v9/projects/${project}")"
if [ "$status" = 404 ]; then
  status="$(vercel_api POST /v11/projects "$(jq -n --arg n "$project" '{name: $n, framework: "nextjs"}')")"
  expect "$status" "create project" 200 201
  log "created $project"
else
  expect "$status" "read project" 200
fi
project_id="$(jq -r .id "$response")"
status="$(vercel_api PATCH "/v9/projects/${project_id}" "$(cat "$here/project.json")")"
expect "$status" "update project settings" 200

log "environment variables"
status="$(vercel_api GET "/v9/projects/${project_id}/env")"
expect "$status" "list env" 200
existing_keys="$(jq -r '.envs[].key' "$response")"

upsert_env() {
  local key="$1" value="$2" type="$3"
  local body
  body="$(jq -n --arg k "$key" --arg v "$value" --arg t "$type" \
    '{key: $k, value: $v, type: $t, target: ["production", "preview"]}')"
  status="$(vercel_api POST "/v10/projects/${project_id}/env?upsert=true" "$body")"
  expect "$status" "set $key" 200 201
}
upsert_env DASHBOARD_ENV "$CORTEX_ENV" plain
upsert_env CORTEX_API_URL "$api_url" plain
upsert_env CORTEX_API_PUBLIC_URL "$api_url" plain
upsert_env NEXT_TELEMETRY_DISABLED 1 plain
if grep -qx DASHBOARD_SESSION_SECRET <<<"$existing_keys"; then
  log "DASHBOARD_SESSION_SECRET already set; leaving it (rotating it signs everyone out)"
else
  # `encrypted`, not `sensitive`: `vercel pull` in CI must read it for `vercel build`.
  upsert_env DASHBOARD_SESSION_SECRET "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" encrypted
  log "generated DASHBOARD_SESSION_SECRET"
fi

log "domain $domain"
status="$(vercel_api POST "/v10/projects/${project_id}/domains" "$(jq -n --arg d "$domain" '{name: $d}')")"
if [ "$status" = 409 ] && jq -e '.error.code == "domain_already_in_use" or .error.code == "domain_already_exists"' "$response" >/dev/null; then
  log "$domain is already attached"
else
  expect "$status" "add domain" 200 201
fi
verified="$(vercel_api GET "/v9/projects/${project_id}/domains/${domain}" >/dev/null && jq -r '.verified' "$response")"

org_id="${VERCEL_TEAM_ID:-$(vercel_api GET /v2/user >/dev/null && jq -r '.user.id' "$response")}"
cat <<EOF

Vercel project ${project} is configured${team_query:+ (team ${VERCEL_TEAM_ID})}.
GitHub environment '${CORTEX_ENV}' variables (infra/github/configure-repo.sh sets these):

  VERCEL_ORG_ID=${org_id}
  VERCEL_PROJECT_ID=${project_id}

DNS: ${domain} CNAME cname.vercel-dns.com (verified: ${verified})
EOF
