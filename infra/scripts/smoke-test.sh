#!/usr/bin/env bash
# End-to-end check of a running Cortex deployment against the v1.0 success criteria:
# create an organization, issue an API key, upload a document, create a conversation,
# send a chat completion, retrieve memory, and see the execution the dashboard shows.
#
#   CORTEX_API_URL=https://api.staging.cortex.celestra.ai CORTEX_ADMIN_TOKEN=... \
#     CORTEX_DASHBOARD_URL=https://staging.cortex.celestra.ai infra/scripts/smoke-test.sh
#
#   infra/scripts/smoke-test.sh --health-only     # read-only; safe against production
#
# The organization it creates is left behind (tenants are never deleted), but every key
# it issued is revoked except a short-lived admin key that expires within a day.
# Set SMOKE_SKIP_COMPLETION=true where no model provider is configured.
set -euo pipefail

: "${CORTEX_API_URL:?set CORTEX_API_URL}"
API="${CORTEX_API_URL%/}"
DASHBOARD="${CORTEX_DASHBOARD_URL:-}"
DASHBOARD="${DASHBOARD%/}"
SKIP_COMPLETION="${SMOKE_SKIP_COMPLETION:-false}"
HEALTH_ONLY=false
[ "${1:-}" = "--health-only" ] && HEALTH_ONLY=true

for cmd in curl jq; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "smoke-test: '$cmd' is required" >&2
    exit 1
  }
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() {
  printf '  \033[31m✗\033[0m %s\n' "$*" >&2
  if [ -s "$work/body" ]; then
    printf '    response: %s\n' "$(head -c 2000 "$work/body")" >&2
  fi
  exit 1
}
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# request EXPECTED_STATUS METHOD PATH [curl args...]; the body is left in $work/body.
request() {
  local expected="$1" method="$2" path="$3" status
  shift 3
  status="$(curl -sS -o "$work/body" -w '%{http_code}' --max-time 120 \
    -X "$method" -H 'X-Cortex-Client: smoke-test' "$@" "$API$path")" || fail "$method $path: connection failed"
  [ "$status" = "$expected" ] || fail "$method $path: expected HTTP $expected, got $status"
}
# with_key KEY EXPECTED METHOD PATH [curl args...]
with_key() {
  local key="$1"
  shift
  request "$1" "$2" "$3" -H "Authorization: Bearer $key" "${@:4}"
}
json() { jq -er "$1" "$work/body"; }

step "Health ($API)"
request 200 GET /health
pass "api $(json .version)"
request 200 GET /health/database
pass "database connected"
request 200 GET /health/redis
pass "redis connected ($(json .latency_ms) ms)"
providers_status="$(curl -sS -o "$work/body" -w '%{http_code}' --max-time 30 "$API/health/providers")"
if [ "$providers_status" = 200 ]; then
  pass "providers: $(json '[.providers[] | select(.status == "available") | .name] | join(", ")')"
elif [ "$SKIP_COMPLETION" = true ]; then
  pass "no provider available (completions skipped)"
else
  fail "GET /health/providers: HTTP $providers_status"
fi
request 401 GET /v1/organization
pass "unauthenticated requests are rejected"

if [ -n "$DASHBOARD" ]; then
  status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$DASHBOARD/login")"
  [ "$status" = 200 ] || fail "dashboard /login: HTTP $status"
  location="$(curl -sS -o /dev/null -w '%{redirect_url}' --max-time 30 "$DASHBOARD/executions")"
  case "$location" in
    */login\?next=*) pass "dashboard requires sign-in for executions" ;;
    *) fail "dashboard /executions did not redirect to sign-in (got '$location')" ;;
  esac
fi

if [ "$HEALTH_ONLY" = true ]; then
  printf '\nHealth checks passed.\n'
  exit 0
fi

: "${CORTEX_ADMIN_TOKEN:?set CORTEX_ADMIN_TOKEN to create the smoke-test organization}"
suffix="$(date -u +%Y%m%d%H%M%S)-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"

step "1. Create an organization"
request 201 POST /v1/admin/organizations \
  -H "Authorization: Bearer $CORTEX_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg s "smoke-$suffix" '{name: "Smoke test \($s)", slug: $s, key_name: "Smoke bootstrap"}')"
org_id="$(json .organization.id)"
bootstrap_key="$(json .api_key.secret)"
bootstrap_key_id="$(json .api_key.id)"
pass "organization smoke-$suffix ($org_id)"

step "2. Generate an API key"
with_key "$bootstrap_key" 201 POST /v1/api-keys -H 'Content-Type: application/json' \
  -d '{"name": "Smoke member", "role": "member", "expires_in_days": 1}'
key="$(json .secret)"
key_id="$(json .id)"
pass "member key $(json .prefix)… (expires $(json .expires_at))"
with_key "$key" 200 GET /v1/organization
[ "$(json .id)" = "$org_id" ] || fail "the new key resolved to another organization"
with_key "$key" 403 GET /v1/api-keys
pass "member keys cannot manage keys"

step "3. Upload a document"
cat >"$work/runbook.md" <<'EOF'
# Aurora launch runbook

The Aurora launch window opens at 14:00 UTC. The launch commander is Priya Natarajan.
Rollback requires approval from two on-call engineers and completes within 15 minutes.
EOF
with_key "$key" 201 POST /v1/documents/ingest \
  -F "file=@$work/runbook.md;type=text/markdown" -F "title=Aurora launch runbook" -F "source=smoke-test"
document_id="$(json .id)"
chunks="$(json .chunk_count)"
[ "$chunks" -ge 1 ] || fail "document has no chunks"
pass "document $document_id ($chunks chunks)"
with_key "$key" 200 POST /v1/knowledge/search -H 'Content-Type: application/json' \
  -d '{"query": "Who is the launch commander?", "top_k": 3}'
[ "$(json '.results | length')" -ge 1 ] || fail "knowledge search returned nothing"
pass "knowledge search finds it"

step "4. Create a conversation"
with_key "$key" 201 POST /v1/conversations -H 'Content-Type: application/json' \
  -d '{"title": "Smoke test"}'
conversation_id="$(json .id)"
with_key "$key" 201 POST /v1/messages -H 'Content-Type: application/json' \
  -d "$(jq -n --arg c "$conversation_id" '{conversation_id: $c, role: "user", content: "We are preparing the Aurora launch."}')"
pass "conversation $conversation_id with one message"

step "5. Send a chat completion"
if [ "$SKIP_COMPLETION" = true ]; then
  pass "skipped (SMOKE_SKIP_COMPLETION=true)"
  completion_id=""
else
  with_key "$key" 200 POST /v1/chat/completions -H 'Content-Type: application/json' \
    -d "$(jq -n --arg c "$conversation_id" '{
      messages: [{role: "user", content: "Who is the Aurora launch commander? Answer in one sentence."}],
      max_tokens: 100,
      memory: {conversation_id: $c},
      knowledge: {top_k: 3}
    }')"
  completion_id="$(json .id)"
  pass "$(json .provider)/$(json .model) in $(json .latency_ms) ms: $(json .output | tr '\n' ' ' | cut -c1-100)"
fi

step "6. Retrieve memory"
with_key "$key" 201 POST /v1/memories -H 'Content-Type: application/json' \
  -d '{"type": "semantic", "content": "Aurora rollbacks need approval from two on-call engineers.", "importance": 0.9}'
with_key "$key" 200 GET "/v1/memories?query=Aurora%20rollback&limit=5"
[ "$(json '.memories | length')" -ge 1 ] || fail "memory search returned nothing"
pass "memory: $(json '.memories[0].summary')"
with_key "$key" 200 GET "/v1/conversations/$conversation_id"
pass "conversation holds $(json '.messages | length') messages"

step "7. Observe executions"
if [ -n "$completion_id" ]; then
  with_key "$key" 200 GET "/v1/executions?completion_id=$completion_id"
  [ "$(json '.total')" -ge 1 ] || fail "no execution recorded for completion $completion_id"
  pass "$(json '.total') execution(s): $(json '[.items[] | "\(.provider)/\(.model) success=\(.success)"] | join(", ")')"
fi
with_key "$key" 200 GET "/v1/observatory/overview"
pass "observatory overview available (the dashboard's Executions view reads the same data)"

step "Clean up"
with_key "$bootstrap_key" 201 POST /v1/api-keys -H 'Content-Type: application/json' \
  -d '{"name": "Smoke expiring admin", "role": "admin", "expires_in_days": 1}'
cleanup_key="$(json .secret)"
with_key "$cleanup_key" 200 DELETE "/v1/api-keys/$key_id"
with_key "$cleanup_key" 200 DELETE "/v1/api-keys/$bootstrap_key_id"
with_key "$key" 401 GET /v1/organization
pass "revoked the member and bootstrap keys; the remaining admin key expires within a day"

printf '\nAll success criteria passed for organization smoke-%s.\n' "$suffix"
