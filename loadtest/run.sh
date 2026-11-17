#!/usr/bin/env bash
# Reproducible load-test runner. Starts a fresh, isolated Compose project
# ("hrload": its own empty database, production compose file), creates an
# API key, then runs loadtest/ingest.js with k6 one or more times.
#
#   loadtest/run.sh [options]
#     --api-workers N   uvicorn processes in the API          (default 4)
#     --workers N       delivery worker containers           (default 1)
#     --rate R          target events per second             (default 200)
#     --duration D      ingest duration, k6 format           (default 60s)
#     --ingest-only     stop the workers and skip the drain: measures the API alone
#     --reps N          measured repetitions on the same stack, after an
#                       unreported 10s warm-up run          (default 1)
#     --keep            leave the stack running afterwards
#
# Prints one line per run: achieved ingest rate, latency percentiles,
# error rate and, unless --ingest-only, how long the delivery backlog took
# to clear after ingestion stopped. Needs Docker and python3.
set -euo pipefail

api_workers=4 workers=1 rate=200 duration=60s ingest_only=0 reps=1 keep=0
while [ $# -gt 0 ]; do
  case $1 in
    --api-workers) api_workers=$2; shift 2 ;;
    --workers) workers=$2; shift 2 ;;
    --rate) rate=$2; shift 2 ;;
    --duration) duration=$2; shift 2 ;;
    --ingest-only) ingest_only=1; shift ;;
    --reps) reps=$2; shift 2 ;;
    --keep) keep=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."
export API_PORT=${LOADTEST_API_PORT:-18080} API_WORKERS=$api_workers
dc() { docker compose -p hrload -f docker-compose.yml -f docker-compose.loadtest.yml "$@"; }

dc down -v --remove-orphans >/dev/null 2>&1 || true
dc up -d --build --scale worker="$workers" >/dev/null 2>&1
for _ in $(seq 1 90); do
  dc ps --format '{{.Service}} {{.Health}}' | grep -q '^api healthy' && break
  sleep 1
done
dc exec -T api alembic upgrade head >/dev/null 2>&1
key=$(dc exec -T api python -m app.cli create-api-key --name loadtest | tail -1)

extra=()
if [ "$ingest_only" = 1 ]; then
  dc stop worker >/dev/null 2>&1
  extra=(-e SKIP_DRAIN=1)
fi

seconds=$(python3 -c "import re,sys; m=re.fullmatch(r'(\d+)([smh])','$duration'); print(int(m[1])*{'s':1,'m':60,'h':3600}[m[2]])")
out=$(mktemp)
trap 'rm -f "$out"; [ "$keep" = 1 ] || dc down -v >/dev/null 2>&1' EXIT

# Warm-up, not reported: a fresh stack has no pooled connections and cold
# Postgres caches, which made the first measured run ~30% slower.
dc run --rm -e HOOKRELAY_API_KEY="$key" -e RATE="$rate" -e DURATION=10s -e SKIP_DRAIN=1 \
  k6 run --quiet /scripts/ingest.js >/dev/null 2>&1 || true

for run in $(seq 1 "$reps"); do
  dc run --rm -e HOOKRELAY_API_KEY="$key" -e RATE="$rate" -e DURATION="$duration" \
    "${extra[@]}" k6 run --quiet /scripts/ingest.js >"$out" 2>&1 || true
  python3 - "$out" "$seconds" "$run" "$api_workers" "$workers" "$rate" <<'PY'
import re, sys
text, seconds = open(sys.argv[1]).read(), int(sys.argv[2])
run, api_workers, workers, rate = sys.argv[3:]
iterations = re.search(r"^\s+iterations\.+: (\d+)", text, re.M)
if not iterations:
    sys.exit("k6 produced no summary:\n" + text[-2000:])
lat = re.search(r"\{ name:ingest \}\.+: avg=\S+\s+med=(\S+)\s+p\(95\)=(\S+)\s+p\(99\)=(\S+)", text)
failed = re.findall(r"\{ name:ingest \}\.+: ([0-9.]+%)", text)
drain = re.search(r"backlog_cleared_after_ingest_s=([0-9.]+)", text)
dups = re.search(r"duplicates=(-?\d+|n/a)", text)
n = int(iterations[1])
line = (f"run {run}: api_workers={api_workers} workers={workers} target={rate}/s "
        f"ingest={n / seconds:.1f}/s med={lat[1]} p95={lat[2]} p99={lat[3]} "
        f"errors={failed[0] if failed else '?'}")
if drain:
    line += f" drain={drain[1]}s end_to_end={n / (seconds + float(drain[1])):.1f}/s"
    line += f" duplicates={dups[1] if dups else '?'}"
print(line, flush=True)
PY
done
