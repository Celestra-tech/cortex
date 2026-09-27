#!/usr/bin/env bash
# Configure the GitHub repository for the deploy workflows: the staging and production
# environments (deployments from main only; production optionally gated on reviewers),
# their variables and secrets, repository-level registry variables, and protection for
# main requiring the CI checks. Idempotent.
#
#   GITHUB_REPOSITORY=Celestra-tech/cortex CORTEX_ENV=staging \
#     GCP_PROJECT_ID=... GCP_WORKLOAD_IDENTITY_PROVIDER=projects/.../providers/github-actions \
#     GCP_DEPLOYER_SERVICE_ACCOUNT=cortex-deployer@....iam.gserviceaccount.com \
#     VERCEL_ORG_ID=... VERCEL_PROJECT_ID=... VERCEL_TOKEN=... \
#     infra/github/configure-repo.sh
#
# The values are printed by infra/scripts/bootstrap-gcp.sh and infra/vercel/configure-project.sh.
# Optional: GCP_REGION (us-central1), GCP_REGISTRY_PROJECT (GCP_PROJECT_ID),
#           PRODUCTION_REVIEWERS (comma-separated GitHub usernames), REGISTRY_SCOPE=true to
#           also set the repository-level variables the image build job uses.
set -euo pipefail

: "${GITHUB_REPOSITORY:?set GITHUB_REPOSITORY (owner/repo)}"
: "${CORTEX_ENV:?set CORTEX_ENV to staging or production}"
: "${GCP_PROJECT_ID:?}" "${GCP_WORKLOAD_IDENTITY_PROVIDER:?}" "${GCP_DEPLOYER_SERVICE_ACCOUNT:?}"
: "${VERCEL_ORG_ID:?}" "${VERCEL_PROJECT_ID:?}" "${VERCEL_TOKEN:?}"
GCP_REGION="${GCP_REGION:-us-central1}"
GCP_REGISTRY_PROJECT="${GCP_REGISTRY_PROJECT:-$GCP_PROJECT_ID}"
command -v gh >/dev/null 2>&1 || {
  echo "error: the GitHub CLI (gh) is required" >&2
  exit 1
}
case "$CORTEX_ENV" in staging | production) ;; *)
  echo "error: CORTEX_ENV must be staging or production" >&2
  exit 1
  ;;
esac
repo="$GITHUB_REPOSITORY"
log() { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }

log "environment $CORTEX_ENV"
reviewers="[]"
if [ "$CORTEX_ENV" = production ] && [ -n "${PRODUCTION_REVIEWERS:-}" ]; then
  reviewers="$(
    IFS=,
    for login in $PRODUCTION_REVIEWERS; do
      gh api "users/${login}" --jq '{type: "User", id: .id}'
    done | jq -s .
  )"
fi
jq -n --argjson reviewers "$reviewers" '{
    reviewers: $reviewers,
    deployment_branch_policy: {protected_branches: false, custom_branch_policies: true}
  }' | gh api -X PUT "repos/${repo}/environments/${CORTEX_ENV}" --input - >/dev/null
existing_policies="$(gh api "repos/${repo}/environments/${CORTEX_ENV}/deployment-branch-policies" \
  --jq '.branch_policies[] | "\(.type):\(.name)"')"
# Staging follows main; production deploys run from main too (release.yml tags, then deploys).
if ! grep -qxF "branch:main" <<<"$existing_policies"; then
  gh api -X POST "repos/${repo}/environments/${CORTEX_ENV}/deployment-branch-policies" \
    -f name=main -f type=branch >/dev/null
fi
if [ "$CORTEX_ENV" = production ] && ! grep -qxF "tag:v*" <<<"$existing_policies"; then
  gh api -X POST "repos/${repo}/environments/${CORTEX_ENV}/deployment-branch-policies" \
    -f 'name=v*' -f type=tag >/dev/null
fi

log "environment variables and secrets"
set_env_var() { gh variable set "$1" --repo "$repo" --env "$CORTEX_ENV" --body "$2"; }
set_env_var GCP_PROJECT_ID "$GCP_PROJECT_ID"
set_env_var GCP_REGION "$GCP_REGION"
set_env_var GCP_REGISTRY_PROJECT "$GCP_REGISTRY_PROJECT"
set_env_var GCP_WORKLOAD_IDENTITY_PROVIDER "$GCP_WORKLOAD_IDENTITY_PROVIDER"
set_env_var GCP_DEPLOYER_SERVICE_ACCOUNT "$GCP_DEPLOYER_SERVICE_ACCOUNT"
set_env_var VERCEL_ORG_ID "$VERCEL_ORG_ID"
set_env_var VERCEL_PROJECT_ID "$VERCEL_PROJECT_ID"
printf '%s' "$VERCEL_TOKEN" | gh secret set VERCEL_TOKEN --repo "$repo" --env "$CORTEX_ENV"

# The image build job runs outside any environment and pushes to the shared registry.
if [ "${REGISTRY_SCOPE:-false}" = true ] || ! gh variable get GCP_REGISTRY_PROJECT --repo "$repo" >/dev/null 2>&1; then
  log "repository variables for the image build (registry ${GCP_REGISTRY_PROJECT})"
  for name in GCP_REGION GCP_REGISTRY_PROJECT GCP_WORKLOAD_IDENTITY_PROVIDER GCP_DEPLOYER_SERVICE_ACCOUNT; do
    gh variable set "$name" --repo "$repo" --body "${!name}"
  done
fi

log "branch protection for main"
jq -n '{
    required_status_checks: {
      strict: true,
      contexts: ["checks", "tests / suites", "tests / migrations", "docker (api)", "docker (dashboard)", "infra"]
    },
    enforce_admins: false,
    required_pull_request_reviews: {required_approving_review_count: 1, dismiss_stale_reviews: true},
    restrictions: null,
    required_linear_history: true,
    allow_force_pushes: false,
    allow_deletions: false
  }' | gh api -X PUT "repos/${repo}/branches/main/protection" --input - >/dev/null

log "automatic staging deploys"
gh variable set CORTEX_DEPLOY_ENABLED --repo "$repo" --body true

log "$repo is configured for $CORTEX_ENV"
