"""Tests for the dead-letter queue API.

The DLQ is the set of events with status `failed` in Postgres. These tests
go through the HTTP layer (client fixture) and set event status directly
in the DB, because reaching `failed` through real deliveries would mean
waiting out the whole backoff schedule.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.queue import WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event, EventStatus

MISSING_ID = "00000000-0000-0000-0000-000000000000"


async def _create_endpoint(client) -> str:
    resp = await client.post(
        "/api/v1/endpoints", json={"url": "https://example.com/hooks"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _ingest(client, endpoint_id: str, event_type: str = "order.created") -> str:
    resp = await client.post(
        f"/api/v1/endpoints/{endpoint_id}/events",
        json={"event_type": event_type, "payload": {"n": 1}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _set_status(
    db_engine,
    event_id: str,
    status: EventStatus,
    *,
    attempts: int = 5,
    last_error: str | None = "HTTP 500: boom",
) -> None:
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        event = await session.get(Event, UUID(event_id))
        event.status = status
        event.attempts = attempts
        event.last_error = last_error
        await session.commit()


async def _dead_lettered_event(client, db_engine, redis_client) -> tuple[str, str]:
    """Create an endpoint with one failed event; start with an empty queue."""
    endpoint_id = await _create_endpoint(client)
    event_id = await _ingest(client, endpoint_id)
    await _set_status(db_engine, event_id, EventStatus.FAILED)
    await redis_client.delete(WEBHOOK_DELIVERY_QUEUE)
    return endpoint_id, event_id


# --- listing ---------------------------------------------------------------


async def test_list_returns_only_failed_events(client, db_engine, redis_client):
    endpoint_id = await _create_endpoint(client)
    failed_id = await _ingest(client, endpoint_id, "order.failed")
    dead_id = await _ingest(client, endpoint_id)
    await _ingest(client, endpoint_id)  # stays pending
    await _set_status(db_engine, failed_id, EventStatus.FAILED)
    await _set_status(db_engine, dead_id, EventStatus.DEAD)

    resp = await client.get("/api/v1/dead-letters")
    assert resp.status_code == 200
    body = resp.json()
    assert [e["id"] for e in body] == [failed_id]
    item = body[0]
    assert item["status"] == "failed"
    assert item["endpoint_id"] == endpoint_id
    assert item["event_type"] == "order.failed"
    assert item["attempts"] == 5
    assert item["last_error"] == "HTTP 500: boom"


async def test_list_filters_by_endpoint(client, db_engine, redis_client):
    endpoint_a = await _create_endpoint(client)
    endpoint_b = await _create_endpoint(client)
    event_a = await _ingest(client, endpoint_a)
    event_b = await _ingest(client, endpoint_b)
    await _set_status(db_engine, event_a, EventStatus.FAILED)
    await _set_status(db_engine, event_b, EventStatus.FAILED)

    resp = await client.get("/api/v1/dead-letters", params={"endpoint_id": endpoint_b})
    assert resp.status_code == 200
    assert [e["id"] for e in resp.json()] == [event_b]


# --- replay ----------------------------------------------------------------


async def test_replay_requeues_with_fresh_budget(client, db_engine, redis_client):
    _, event_id = await _dead_lettered_event(client, db_engine, redis_client)

    resp = await client.post(f"/api/v1/events/{event_id}/replay")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["attempts"] == 0
    # Kept for inspection; the next claim clears it.
    assert body["last_error"] == "HTTP 500: boom"

    assert await redis_client.lrange(WEBHOOK_DELIVERY_QUEUE, 0, -1) == [event_id]
    listing = await client.get("/api/v1/dead-letters")
    assert listing.json() == []


async def test_replay_accepts_discarded_events(client, db_engine, redis_client):
    _, event_id = await _dead_lettered_event(client, db_engine, redis_client)
    await _set_status(db_engine, event_id, EventStatus.DEAD)

    resp = await client.post(f"/api/v1/events/{event_id}/replay")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"


async def test_replay_rejects_events_not_dead_lettered(client, redis_client):
    endpoint_id = await _create_endpoint(client)
    event_id = await _ingest(client, endpoint_id)  # pending

    resp = await client.post(f"/api/v1/events/{event_id}/replay")
    assert resp.status_code == 409
    assert "pending" in resp.json()["detail"]


async def test_replay_rejects_inactive_endpoint(client, db_engine, redis_client):
    endpoint_id, event_id = await _dead_lettered_event(client, db_engine, redis_client)
    await client.patch(f"/api/v1/endpoints/{endpoint_id}", json={"is_active": False})

    resp = await client.post(f"/api/v1/events/{event_id}/replay")
    assert resp.status_code == 409

    status_resp = await client.get(f"/api/v1/events/{event_id}")
    assert status_resp.json()["status"] == "failed"
    assert await redis_client.llen(WEBHOOK_DELIVERY_QUEUE) == 0


async def test_replay_returns_404_when_missing(client, redis_client):
    resp = await client.post(f"/api/v1/events/{MISSING_ID}/replay")
    assert resp.status_code == 404


async def test_replay_survives_redis_failure(
    client, db_engine, redis_client, failing_redis
):
    """Same contract as ingestion: committed first, enqueue is best-effort."""
    _, event_id = await _dead_lettered_event(client, db_engine, redis_client)

    resp = await client.post(f"/api/v1/events/{event_id}/replay")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"


# --- discard ---------------------------------------------------------------


async def test_discard_archives_failed_event(client, db_engine, redis_client):
    _, event_id = await _dead_lettered_event(client, db_engine, redis_client)

    resp = await client.post(f"/api/v1/events/{event_id}/discard")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "dead"
    assert body["last_error"] == "HTTP 500: boom"

    listing = await client.get("/api/v1/dead-letters")
    assert listing.json() == []
    assert await redis_client.llen(WEBHOOK_DELIVERY_QUEUE) == 0


async def test_discard_rejects_events_not_failed(client, db_engine, redis_client):
    endpoint_id = await _create_endpoint(client)
    pending_id = await _ingest(client, endpoint_id)
    dead_id = await _ingest(client, endpoint_id)
    await _set_status(db_engine, dead_id, EventStatus.DEAD)

    for event_id in (pending_id, dead_id):
        resp = await client.post(f"/api/v1/events/{event_id}/discard")
        assert resp.status_code == 409, event_id


async def test_discard_returns_404_when_missing(client, redis_client):
    resp = await client.post(f"/api/v1/events/{MISSING_ID}/discard")
    assert resp.status_code == 404
