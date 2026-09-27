#!/bin/sh
# Container entrypoint for the Cortex API image.
#
#   serve            Run the HTTP server on $PORT (default).
#   migrate          Apply database migrations and exit.
#   cli <args...>    Operator CLI: create-organization, create-api-key, ...
#   <anything else>  Executed as-is, e.g. `python -c ...`.
#
# Set CORTEX_RUN_MIGRATIONS=true to migrate before serving. That suits a single
# container (docker compose); multi-instance platforms such as Cloud Run should
# run `migrate` as a separate job so instances never race on schema changes.
set -eu

migrate() {
  echo "cortex-entrypoint: applying database migrations" >&2
  alembic -c /app/alembic.ini upgrade head
}

command="${1:-serve}"
[ "$#" -gt 0 ] && shift

case "$command" in
  serve)
    if [ "${CORTEX_RUN_MIGRATIONS:-false}" = "true" ]; then
      migrate
    fi
    # One process per container: Cloud Run and Kubernetes scale by instances.
    # The graceful timeout stays under Cloud Run's 10s SIGTERM window.
    exec uvicorn cortex_api.main:app \
      --host 0.0.0.0 \
      --port "${PORT:-8000}" \
      --proxy-headers \
      --forwarded-allow-ips "${CORTEX_FORWARDED_ALLOW_IPS:-*}" \
      --no-server-header \
      --no-access-log \
      --timeout-keep-alive "${CORTEX_KEEPALIVE_SECONDS:-75}" \
      --timeout-graceful-shutdown "${CORTEX_GRACEFUL_SHUTDOWN_SECONDS:-8}" \
      "$@"
    ;;
  migrate)
    migrate
    ;;
  cli)
    exec python -m cortex_api.cli "$@"
    ;;
  *)
    exec "$command" "$@"
    ;;
esac
