"""Tests for idempotent ingestion with the Idempotency-Key header."""

import json
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.queue import WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event
from app.repositories.event_repository import EventRepository
from app.schemas.event import EventCreate
from app.services.event_service import request_fingerprint

BODY = {"event_type": "order.created", "payload": {"order_id": 42, "total": 10}}


async def _create_endpoint(client) -> str:
    resp = await client.post(
        "/api/v1/endpoints", json={"url": "https://example.com/hooks"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _ingest(client, endpoint_id: str, key: str | None = None, body=BODY):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return await client.post(
        f"/api/v1/endpoints/{endpoint_id}/events", json=body, headers=headers
    )


async def _count_events(db_engine, endpoint_id: str) -> int:
    Session = async_sessionmaker(db_engine)
    async with Session() as session:
        stmt = select(func.count()).where(Event.endpoint_id == UUID(endpoint_id))
        return (await session.execute(stmt)).scalar_one()


# --- fingerprint -------------------------------------------------------------


def test_fingerprint_ignores_key_order():
    a = EventCreate(event_type="x", payload={"a": 1, "b": {"c": 2, "d": 3}})
    b = EventCreate(event_type="x", payload={"b": {"d": 3, "c": 2}, "a": 1})
    assert request_fingerprint(a) == request_fingerprint(b)


@pytest.mark.parametrize(
    "other",
    [
        EventCreate(event_type="y", payload={"a": 1}),
        EventCreate(event_type="x", payload={"a": 2}),
        EventCreate(event_type="x", payload={"a": "1"}),
    ],
)
def test_fingerprint_changes_with_type_or_payload(other):
    base = EventCreate(event_type="x", payload={"a": 1})
    assert request_fingerprint(base) != request_fingerprint(other)


# --- API ---------------------------------------------------------------------


async def test_without_key_each_request_creates_an_event(
    client, db_engine, redis_client
):
    endpoint_id = await _create_endpoint(client)

    first = await _ingest(client, endpoint_id)
    second = await _ingest(client, endpoint_id)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert await _count_events(db_engine, endpoint_id) == 2


async def test_retry_with_same_key_returns_original_event(
    client, db_engine, redis_client
):
    endpoint_id = await _create_endpoint(client)

    first = await _ingest(client, endpoint_id, key="order-42")
    retry = await _ingest(client, endpoint_id, key="order-42")

    assert first.status_code == 201
    assert "Idempotent-Replayed" not in first.headers
    assert retry.status_code == 200
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json() == first.json()

    # Nothing new was created or enqueued by the retry.
    assert await _count_events(db_engine, endpoint_id) == 1
    queue = await redis_client.lrange(WEBHOOK_DELIVERY_QUEUE, 0, -1)
    assert queue == [first.json()["id"]]


async def test_retry_with_reordered_json_is_still_a_replay(client, redis_client):
    endpoint_id = await _create_endpoint(client)
    await _ingest(client, endpoint_id, key="k1")

    reordered = json.dumps(
        {"payload": {"total": 10, "order_id": 42}, "event_type": "order.created"},
        indent=2,
    )
    retry = await client.post(
        f"/api/v1/endpoints/{endpoint_id}/events",
        content=reordered,
        headers={"Idempotency-Key": "k1", "Content-Type": "application/json"},
    )
    assert retry.status_code == 200


async def test_same_key_with_different_body_is_rejected(
    client, db_engine, redis_client
):
    endpoint_id = await _create_endpoint(client)
    await _ingest(client, endpoint_id, key="k1")

    other = {"event_type": "order.created", "payload": {"order_id": 43}}
    resp = await _ingest(client, endpoint_id, key="k1", body=other)

    assert resp.status_code == 422
    assert "k1" in resp.json()["detail"]
    assert await _count_events(db_engine, endpoint_id) == 1


async def test_keys_are_scoped_per_endpoint(client, redis_client):
    endpoint_a = await _create_endpoint(client)
    endpoint_b = await _create_endpoint(client)

    resp_a = await _ingest(client, endpoint_a, key="shared")
    resp_b = await _ingest(client, endpoint_b, key="shared")

    assert resp_a.status_code == resp_b.status_code == 201
    assert resp_a.json()["id"] != resp_b.json()["id"]


async def test_replay_still_works_after_endpoint_is_deactivated(client, redis_client):
    endpoint_id = await _create_endpoint(client)
    first = await _ingest(client, endpoint_id, key="k1")
    await client.patch(f"/api/v1/endpoints/{endpoint_id}", json={"is_active": False})

    # The client is retrying a request that already succeeded: same answer.
    retry = await _ingest(client, endpoint_id, key="k1")
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]

    # A new key on an inactive endpoint is still refused.
    fresh = await _ingest(client, endpoint_id, key="k2")
    assert fresh.status_code == 409


async def test_concurrent_duplicate_is_resolved_by_unique_constraint(
    client, db_engine, redis_client, monkeypatch
):
    """Simulate two requests racing past the lookup.

    The lookup misses once, as if the other request had not committed yet,
    so the insert hits the unique constraint. The service must recover and
    return the winning event instead of failing with a 500.
    """
    endpoint_id = await _create_endpoint(client)
    first = await _ingest(client, endpoint_id, key="race")

    real_lookup = EventRepository.get_by_idempotency_key
    calls = 0

    async def lookup_misses_once(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return await real_lookup(self, *args, **kwargs)

    monkeypatch.setattr(EventRepository, "get_by_idempotency_key", lookup_misses_once)

    retry = await _ingest(client, endpoint_id, key="race")

    assert calls == 2  # the pre-check, then the lookup after IntegrityError
    assert retry.status_code == 200
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json()["id"] == first.json()["id"]
    assert await _count_events(db_engine, endpoint_id) == 1


@pytest.mark.parametrize("key", ["", "x" * 256])
async def test_invalid_key_is_rejected(client, redis_client, key):
    endpoint_id = await _create_endpoint(client)
    resp = await _ingest(client, endpoint_id, key=key)
    assert resp.status_code == 422
