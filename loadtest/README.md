# Load testing

How HookRelay behaves under sustained load, how the numbers were measured, and what the measurements changed in the code.

## Reproduce

```bash
# Ingestion ceiling of the API alone (workers stopped)
loadtest/run.sh --api-workers 4 --rate 1000 --duration 30s --ingest-only --reps 3

# Latency at a sustainable rate
loadtest/run.sh --api-workers 4 --rate 400 --duration 60s --ingest-only --reps 2

# End to end: ingest for 60s, then wait until every event is delivered
loadtest/run.sh --api-workers 4 --workers 3 --rate 200 --duration 60s --reps 3
```

Each invocation starts a **fresh, isolated Compose project** (`hrload`) with its own empty database, built from the production compose file (no hot reload) on port 18080. It never touches the development stack, and is removed afterwards unless `--keep` is passed.

| Piece | Role |
|---|---|
| [`ingest.js`](ingest.js) | k6 `constant-arrival-rate` scenario. Every request is authenticated and carries a unique `Idempotency-Key`, like a real client. Thresholds fail the run on >1% errors, p95 > 500 ms, or any dropped iteration (the target rate not being met). The teardown polls `/metrics` until the backlog is delivered. |
| [`receiver.py`](receiver.py) | Webhook receiver that answers `200`, so the numbers measure HookRelay and not a downstream service. |
| [`run.sh`](run.sh) | Starts the stack, runs migrations, creates an API key, runs one unreported 10s warm-up, then the measured runs. |
| [`../docker-compose.loadtest.yml`](../docker-compose.loadtest.yml) | Adds the receiver and k6 (`grafana/k6:0.55.0`) to the stack. |

**Methodology.** Ceilings are measured with a demand far above capacity (1000/s), so the achieved rate is the ceiling. Every configuration is repeated at least three times and reported as median plus range, because single runs on a laptop varied by up to 2x. The achieved ingest rate is `iterations / ingest duration`; k6's own `iterations/s` also counts the drain phase.

**Machine.** Laptop with an AMD Ryzen 7 5700U (8 cores, 16 threads) and 14 GiB RAM. Docker 29.9 on Fedora. k6, the receiver and the whole stack share the same machine, so absolute numbers are conservative. The ratios between configurations are what matter.

## Results

### Ingestion ceiling (API alone)

| API version | Processes | Ceiling (events/s) | vs. before |
|---|---|---|---|
| Before (`main` at `a8a87ab`) | 1 | **193** (189–194) | — |
| Async dependencies + sized pool | 1 | **229** (217–231) | ×1.2 |
| | 2 | **365** (351–385) | ×1.9 |
| | 4 (default) | **575** (570–585) | **×3.0** |

### Latency at a sustainable rate (4 API processes)

| Target | Achieved | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|
| 200/s | 200/s | 8–9 ms | 11–21 ms | 14–64 ms | 0% |
| 400/s | 399/s | 14 ms | 37–42 ms | 148–170 ms | 0% |

Before these changes, a single process at **150/s** already had p95 = **716 ms**, and requests queued behind each other.

### End to end: ingestion plus delivery (200/s for 60s, 4 API processes)

| Delivery workers | Backlog cleared after ingest stops | Delivery capacity* |
|---|---|---|
| 1 | 89–120 s | **~79/s** (67–81) |
| 3 | 4–8 s | **≥ 184/s**: nearly keeps up with ingestion |

\*From the backlog: with offered load L for 60 s and drain time T, capacity C satisfies `C × (60 + T) = L × 60`.

No event was lost or delivered as `failed` in any run.

## What the measurements found

### 1. The test receiver, not HookRelay, was slow

The first runs showed **43 ms per delivery** to a local receiver. Python's `http.server` sends headers and body in separate writes. With Nagle's algorithm on, the second write waits for the client's delayed ACK, which takes about 40 ms. With `TCP_NODELAY`, which every production web server sets, deliveries take **2–3 ms**. Without this fix the published numbers would have been about 10 times too pessimistic.

### 2. One API process is CPU-bound

At its ceiling the API container sat at **~95% CPU** (one core) while PostgreSQL used ~45% and Redis 2%. A Python process uses at most about one core, so the fix is more processes. Two things made that safe:

- **Metrics.** Each process has its own counters, so a scrape would land on one at random. `/metrics` now uses `prometheus_client`'s multiprocess mode, with a tmpfs directory and `mostrecent` gauges.
- **Connections.** Each process has its own pool, and Postgres allows 100 connections by default. The defaults are 4 API processes × 15 plus 3 per worker.

At 4 processes the API container uses ~376% CPU, still the limit. Scaling is ×2.5, not ×4: CPU time per request rises from ~4.1 ms to ~6.5 ms because the laptop lowers its turbo frequency as more cores get busy, and processes 3 and 4 share physical cores through SMT.

### 3. Thread hops and connection churn inside each process

A `cProfile` of the API under load showed two problems that have nothing to do with HookRelay's logic, which was 0.4% of the time:

- **Thread-pool hops.** FastAPI runs plain `def` dependencies in a thread pool. The service factories and the Redis dependency were `def`, so every request made several thread hops. They are now `async def`.
- **Connection churn.** SQLAlchemy's default pool keeps 5 connections and closes up to 10 "overflow" ones after each use. Under concurrency, the API opened about 128 new connections in 30 s, each paying Postgres' SCRAM handshake (4,096 HMAC rounds) in the API's own CPU. The pool is now sized (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`).

Each fix alone was inconclusive. The async dependencies alone even made latency worse: the thread pool had been throttling database access, and removing it made the default pool churn harder. Together they raised the single-process ceiling by ~20% (193 → 229 events/s, three runs each).

### 4. Delivery scales with worker containers

`docker compose up -d --scale worker=3` works without changes. Workers share the queue through `BRPOP`, and the reapers use `SELECT ... FOR UPDATE SKIP LOCKED`. Each worker handles one event at a time, and spends most of that time on database round trips (claim and outcome are separate commits) rather than on the HTTP call (~2.3 ms). PostgreSQL reaches about one core under the combined load.

## Not covered

- Receivers that are slow or failing. Here they answer in ~2 ms, so these are best-case delivery numbers. A slow receiver holds a worker for up to the 25 s timeout.
- Multiple hosts, a managed Postgres, or network latency between services.
- Soak tests longer than a few minutes, and table growth over millions of events.
