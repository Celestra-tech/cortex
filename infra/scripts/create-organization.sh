#!/usr/bin/env bash
# Create a tenant organization with its first admin API key.
#
#   CORTEX_ENV=production GCP_PROJECT_ID=... infra/scripts/create-organization.sh "Acme Inc" acme
#
# The admin token is read from Secret Manager. For a local API, skip GCP entirely:
#   CORTEX_API_URL=http://localhost:8000 CORTEX_ADMIN_TOKEN=... infra/scripts/create-organization.sh "Acme" acme
#
# The API key secret is printed once on stdout; progress goes to stderr.
set -euo pipefail

[ "$#" -eq 2 ] || {
  echo "usage: $0 NAME SLUG" >&2
  exit 2
}
name="$1" slug="$2"

if [ -z "${CORTEX_API_URL:-}" ] || [ -z "${CORTEX_ADMIN_TOKEN:-}" ]; then
  # shellcheck source=infra/scripts/lib.sh
  source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
  require_cmd gcloud
  CORTEX_API_URL="${CORTEX_API_URL:-$API_URL}"
  CORTEX_ADMIN_TOKEN="$(gcloud_project secrets versions access latest --secret "$SECRET_ADMIN_TOKEN")"
fi
command -v jq >/dev/null 2>&1 || {
  echo "jq is required" >&2
  exit 1
}

body="$(jq -n --arg name "$name" --arg slug "$slug" '{name: $name, slug: $slug}')"
response="$(mktemp)"
trap 'rm -f "$response"' EXIT
status="$(curl -sS -o "$response" -w '%{http_code}' --max-time 30 \
  -X POST "${CORTEX_API_URL%/}/v1/admin/organizations" \
  -H "Authorization: Bearer ${CORTEX_ADMIN_TOKEN}" -H 'Content-Type: application/json' -d "$body")"

if [ "$status" != 201 ]; then
  echo "error: HTTP $status: $(cat "$response")" >&2
  exit 1
fi

jq -r '"created organization \(.organization.slug) (\(.organization.id))\nadmin key \(.api_key.prefix)… — store the secret below now; it is not shown again"' "$response" >&2
jq -r '.api_key.secret' "$response"
