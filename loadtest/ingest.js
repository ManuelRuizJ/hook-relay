// Load test: sustained ingestion, then end-to-end delivery drain.
//
// Phase 1 (scenario "ingest"): POST events at a constant arrival rate,
// authenticated and with a unique Idempotency-Key, like a real client.
// Phase 2 (teardown): poll /metrics until every event has been delivered,
// and report how long the worker took to drain the backlog.
//
// Env: HOOKRELAY_API_KEY (required), RATE (events/s, default 200),
// DURATION (default 60s), API_URL, RECEIVER_URL, SKIP_DRAIN (set to any
// value to measure ingestion only and skip waiting for deliveries).

import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';

const API = __ENV.API_URL || 'http://api:8000';
const KEY = __ENV.HOOKRELAY_API_KEY;
const RATE = parseInt(__ENV.RATE || '200', 10);
const DURATION = __ENV.DURATION || '60s';
const RECEIVER = __ENV.RECEIVER_URL || 'http://receiver:8080/hooks';
const RUN_ID = Date.now();

export const options = {
  scenarios: {
    ingest: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 50,
      maxVUs: 400,
    },
  },
  thresholds: {
    'http_req_failed{name:ingest}': ['rate<0.01'],
    'http_req_duration{name:ingest}': ['p(95)<500'],
    // The arrival rate must actually be met, not silently dropped.
    dropped_iterations: ['count<1'],
  },
  setupTimeout: '30s',
  teardownTimeout: '15m',
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};

function headers(extra = {}) {
  return Object.assign(
    { Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' },
    extra,
  );
}

function scrape() {
  const text = http.get(`${API}/metrics`, { tags: { name: 'metrics' } }).body;
  const value = (pattern) => {
    const match = text.match(pattern);
    return match ? parseFloat(match[1]) : 0;
  };
  return {
    delivery: value(/^hookrelay_queue_depth\{queue="delivery"\} (\S+)/m),
    delayed: value(/^hookrelay_queue_depth\{queue="delayed"\} (\S+)/m),
    pending: value(/^hookrelay_events\{status="pending"\} (\S+)/m),
    inProgress: value(/^hookrelay_events\{status="in_progress"\} (\S+)/m),
    delivered: value(/^hookrelay_events\{status="delivered"\} (\S+)/m),
    failed: value(/^hookrelay_events\{status="failed"\} (\S+)/m),
  };
}

// The test receiver counts every POST it gets. Compared with how many events
// ended up delivered, the difference is duplicate deliveries. Returns null
// for a receiver that does not expose /count.
function receiverCount() {
  const res = http.get(RECEIVER.replace(/\/hooks$/, '/count'), { tags: { name: 'receiver' } });
  return res.status === 200 ? parseInt(res.body, 10) : null;
}

function pendingWork(state) {
  return state.delivery + state.delayed + state.pending + state.inProgress;
}

function waitUntilIdle(timeoutSeconds) {
  const deadline = Date.now() + timeoutSeconds * 1000;
  while (pendingWork(scrape()) > 0) {
    if (Date.now() > deadline) throw new Error('system not idle before the run');
    sleep(0.5);
  }
}

export function setup() {
  if (!KEY) throw new Error('HOOKRELAY_API_KEY is required');
  const res = http.post(
    `${API}/api/v1/endpoints`,
    JSON.stringify({ url: RECEIVER, description: `load test ${RUN_ID}` }),
    { headers: headers() },
  );
  if (res.status !== 201) throw new Error(`endpoint create failed: ${res.status} ${res.body}`);
  // Start from an idle system. Leftover work (e.g. the warm-up's backlog)
  // would inflate this run's drain time, and deliveries completing between
  // the two counter reads below would show up as phantom duplicates.
  if (!__ENV.SKIP_DRAIN) waitUntilIdle(120);
  return {
    endpointId: res.json('id'),
    before: { received: receiverCount(), delivered: scrape().delivered },
  };
}

export default function (data) {
  const iteration = exec.scenario.iterationInTest;
  const res = http.post(
    `${API}/api/v1/endpoints/${data.endpointId}/events`,
    JSON.stringify({
      event_type: 'order.created',
      payload: { order_id: iteration, amount: 1999, currency: 'MXN' },
    }),
    {
      headers: headers({ 'Idempotency-Key': `${RUN_ID}-${iteration}` }),
      tags: { name: 'ingest' },
    },
  );
  check(res, { 'ingest returns 201': (r) => r.status === 201 });
}

export function teardown(data) {
  if (__ENV.SKIP_DRAIN) return;
  const started = Date.now();
  let state = scrape();
  while (pendingWork(state) > 0) {
    sleep(0.5);
    state = scrape();
  }
  const drainSeconds = (Date.now() - started) / 1000;
  const received = receiverCount();
  // Every attempt reaches this receiver and succeeds, so each POST beyond
  // one per delivered event is a duplicate delivery.
  const duplicates =
    received === null || data.before.received === null
      ? 'n/a'
      : received - data.before.received - (state.delivered - data.before.delivered);
  console.log(
    `DRAIN backlog_cleared_after_ingest_s=${drainSeconds.toFixed(1)} ` +
      `delivered=${state.delivered} failed=${state.failed} duplicates=${duplicates}`,
  );
}
