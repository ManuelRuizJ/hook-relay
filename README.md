# HookRelay

A webhook delivery service built with FastAPI, PostgreSQL and Redis.

> Portfolio project for backend/infrastructure roles. Demonstrates multi-stage Docker builds,
> healthchecks, background workers, retries with exponential backoff, idempotency
> and a dead-letter queue.

## Status

. . . 

## Roadmap

- [x] Repository bootstrap
- [x] PostgreSQL + Redis in Docker Compose
- [x] Minimal FastAPI with `/health`
- [x] Multi-stage Dockerfile
- [x] Webhook endpoint CRUD
- [ ] Event ingestion
- [ ] Delivery worker with retries
- [ ] Dead-letter queue
- [ ] Observability
- [ ] CI


## API
### `POST /api/v1/endpoints`
Create a webhook endpoint. Returns the signing **secret once** — store it.

```bash
curl -X POST http://localhost:8000/api/v1/endpoints \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/hooks","description":"demo"}'

## How to run
GET /api/v1/endpoints?skip=0&limit=100

# List endpoints (secret is never returned).

GET /api/v1/endpoints/{id}
PATCH /api/v1/endpoints/{id}
DELETE /api/v1/endpoints/{id}
```