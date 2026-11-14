"""Prometheus metrics owned by the worker process.

The worker is a separate process from the API, so it serves its own
/metrics on WORKER_METRICS_PORT (default 9100) via prometheus_client's
built-in HTTP server.
"""

from prometheus_client import Counter, Histogram

DELIVERY_OUTCOMES = ("delivered", "retry", "failed")
REAPER_KINDS = ("orphaned", "stuck_rescheduled", "stuck_failed")
REAPER_PASSES = ("reap_stuck_in_progress", "reap_orphaned_events")

DELIVERIES = Counter(
    "hookrelay_deliveries_total",
    "Delivery attempts by outcome: delivered, retry (scheduled with "
    "backoff) or failed (retries exhausted, dead-lettered).",
    ["outcome"],
)
DELIVERY_DURATION = Histogram(
    "hookrelay_delivery_duration_seconds",
    "Duration of the HTTP POST to the receiver, including failed attempts.",
    # Up to the worst-case HTTP timeout (~25s).
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 25),
)
REAPER_RECOVERED = Counter(
    "hookrelay_reaper_recovered_total",
    "Events recovered by the reaper, by kind.",
    ["kind"],
)
WORKER_LOOP_ERRORS = Counter(
    "hookrelay_worker_loop_errors_total",
    "Main-loop iterations that raised (e.g. Redis or Postgres unreachable) "
    "and were retried with backoff.",
)
REAPER_ERRORS = Counter(
    "hookrelay_reaper_errors_total",
    "Reaper passes that raised and will be retried next cycle.",
    ["pass_name"],
)

# Create every labelled series up front so dashboards and rate() see 0
# instead of a missing series until the first occurrence.
for _outcome in DELIVERY_OUTCOMES:
    DELIVERIES.labels(_outcome)
for _kind in REAPER_KINDS:
    REAPER_RECOVERED.labels(_kind)
for _pass in REAPER_PASSES:
    REAPER_ERRORS.labels(_pass)
