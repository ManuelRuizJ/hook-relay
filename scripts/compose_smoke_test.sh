#!/usr/bin/env bash
# Smoke test of the production stack, including a container restart.
#
# Starts docker-compose.yml (no dev override) as a separate project, checks
# the API and worker endpoints, restarts the api container, and checks them
# again. Restarts matter: Docker remounted the metrics tmpfs as root-owned
# 755 on restart, which broke /metrics only after a host reboot, a case unit
# tests and a plain image build never exercise.
#
#   scripts/compose_smoke_test.sh        # used by CI; safe to run locally
#
# Env: SMOKE_PROJECT (default hrsmoke), SMOKE_API_PORT (default 18081).
set -euo pipefail
cd "$(dirname "$0")/.."

project=${SMOKE_PROJECT:-hrsmoke}
export API_PORT=${SMOKE_API_PORT:-18081}
dc() { docker compose -p "$project" -f docker-compose.yml "$@"; }

created_env=0
if [ ! -f .env ]; then
  cp .env.example .env
  created_env=1
fi
cleanup() {
  dc down -v >/dev/null 2>&1 || true
  [ "$created_env" = 1 ] && rm -f .env
}
trap cleanup EXIT

expect_200() {
  local what=$1 url=$2 code
  code=$(curl -s -o /dev/null -w '%{http_code}' "$url")
  if [ "$code" != 200 ]; then
    echo "FAIL $what: $url returned $code" >&2
    dc logs --tail=40 api >&2
    exit 1
  fi
  echo "ok   $what"
}

check_all() {
  local stage=$1 base="http://localhost:$API_PORT"
  expect_200 "$stage /health" "$base/health"
  expect_200 "$stage /health/ready" "$base/health/ready"
  expect_200 "$stage /metrics" "$base/metrics"
  dc exec -T worker python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:9100/metrics')" \
    && echo "ok   $stage worker /metrics"
}

dc up -d --build --wait
dc exec -T api alembic upgrade head
check_all "after start:"

dc restart api
dc up -d --wait
check_all "after restart:"

echo "smoke test passed"
