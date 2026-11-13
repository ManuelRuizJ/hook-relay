async def _create_endpoint(client, **overrides) -> str:
    payload = {"url": "https://example.com/hooks", **overrides}
    resp = await client.post("/api/v1/endpoints", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_ingest_event_returns_201_with_pending_status(client, redis_client):
    endpoint_id = await _create_endpoint(client)

    resp = await client.post(
        f"/api/v1/endpoints/{endpoint_id}/events",
        json={"event_type": "order.created", "payload": {"order_id": 42}},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["endpoint_id"] == endpoint_id
    assert body["event_type"] == "order.created"
    assert body["payload"] == {"order_id": 42}
    assert body["status"] == "pending"
    assert "id" in body


async def test_ingest_event_enqueues_to_redis(client, redis_client):
    from app.core.queue import WEBHOOK_DELIVERY_QUEUE

    endpoint_id = await _create_endpoint(client)
    resp = await client.post(
        f"/api/v1/endpoints/{endpoint_id}/events",
        json={"event_type": "x", "payload": {}},
    )
    event_id = resp.json()["id"]

    queue = await redis_client.lrange(WEBHOOK_DELIVERY_QUEUE, 0, -1)
    assert event_id in queue


async def test_ingest_event_returns_404_when_endpoint_missing(client, redis_client):
    resp = await client.post(
        "/api/v1/endpoints/00000000-0000-0000-0000-000000000000/events",
        json={"event_type": "x", "payload": {}},
    )
    assert resp.status_code == 404


async def test_ingest_event_returns_409_when_endpoint_inactive(client, redis_client):
    endpoint_id = await _create_endpoint(client)
    await client.patch(f"/api/v1/endpoints/{endpoint_id}", json={"is_active": False})

    resp = await client.post(
        f"/api/v1/endpoints/{endpoint_id}/events",
        json={"event_type": "x", "payload": {}},
    )
    assert resp.status_code == 409


async def test_ingest_event_returns_422_when_body_invalid(client, redis_client):
    endpoint_id = await _create_endpoint(client)
    resp = await client.post(
        f"/api/v1/endpoints/{endpoint_id}/events",
        json={"event_type": "", "payload": {}},  # min_length=1
    )
    assert resp.status_code == 422


async def test_get_event_returns_status(client, redis_client):
    endpoint_id = await _create_endpoint(client)
    ingest = await client.post(
        f"/api/v1/endpoints/{endpoint_id}/events",
        json={"event_type": "x", "payload": {}},
    )
    event_id = ingest.json()["id"]

    resp = await client.get(f"/api/v1/events/{event_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == event_id
    assert body["status"] == "pending"
    assert body["attempts"] == 0
    assert body["max_attempts"] == 5
    assert body["last_error"] is None
    assert body["delivered_at"] is None


async def test_get_event_returns_404_when_missing(client):
    resp = await client.get("/api/v1/events/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


async def test_ingest_event_survives_redis_failure(client, failing_redis):
    """The event must persist even if Redis is down (at-least-once guarantee).

    A Redis outage must not turn into a lost event nor a 5xx. The API
    returns 201 because the resource was created; delivery is a separate
    concern handled by the reaper (Phase 5).
    """
    endpoint_id = await _create_endpoint(client)

    resp = await client.post(
        f"/api/v1/endpoints/{endpoint_id}/events",
        json={"event_type": "x", "payload": {}},
    )
    assert resp.status_code == 201, resp.text
    event_id = resp.json()["id"]

    status_resp = await client.get(f"/api/v1/events/{event_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "pending"
