"""Tests for logging, API metrics and health probes."""

import json
import logging
import sys
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_db
from app.core.logging import JsonFormatter, configure_logging
from app.core.queue import WEBHOOK_DELAYED_QUEUE
from app.main import app
from tests.metrics_helpers import sample

# --- logging ---------------------------------------------------------------


def _record(msg: str = "Event %s delivered", *args, **extra) -> logging.LogRecord:
    record = logging.makeLogRecord(
        {"name": "app.test", "levelname": "INFO", "msg": msg, "args": args}
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_object_with_extra_fields():
    line = JsonFormatter().format(
        _record("Event %s delivered", "abc", event_id="abc", attempt=2)
    )

    entry = json.loads(line)
    assert entry["msg"] == "Event abc delivered"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.test"
    assert entry["event_id"] == "abc"
    assert entry["attempt"] == 2
    assert entry["ts"].endswith("+00:00")
    # Standard LogRecord internals must not leak into the output.
    assert "args" not in entry and "levelno" not in entry
    assert "\n" not in line


def test_json_formatter_keeps_tracebacks_on_one_line():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("failed")
        record.exc_info = sys.exc_info()

    line = JsonFormatter().format(record)

    assert "\n" not in line
    assert "ValueError: boom" in json.loads(line)["exc_info"]


def test_json_formatter_serializes_non_json_values():
    event_id = UUID("12345678-1234-5678-1234-567812345678")
    entry = json.loads(JsonFormatter().format(_record("x", event_id=event_id)))
    assert entry["event_id"] == str(event_id)


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_configure_logging_routes_uvicorn_through_root(fmt):
    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    saved = (root.handlers[:], root.level, access.handlers[:], access.propagate)
    try:
        configure_logging("warning", fmt)

        assert len(root.handlers) == 1
        assert root.level == logging.WARNING
        is_json = isinstance(root.handlers[0].formatter, JsonFormatter)
        assert is_json == (fmt == "json")
        assert access.handlers == [] and access.propagate is True
    finally:
        root.handlers[:], level, access.handlers[:], access.propagate = saved
        root.setLevel(level)


# --- /metrics --------------------------------------------------------------


async def _create_endpoint(client) -> str:
    resp = await client.post(
        "/api/v1/endpoints", json={"url": "https://example.com/hooks"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_metrics_exposes_prometheus_text(client, redis_client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# TYPE hookrelay_http_requests_total counter" in resp.text


async def test_metrics_label_requests_by_route_template(client, redis_client):
    route = "/api/v1/events/{event_id}"
    labels = {"method": "GET", "route": route, "status": "404"}
    before = sample("hookrelay_http_requests_total", **labels)
    unmatched = {"method": "GET", "route": "unmatched", "status": "404"}
    unmatched_before = sample("hookrelay_http_requests_total", **unmatched)

    await client.get("/api/v1/events/00000000-0000-0000-0000-000000000000")
    await client.get("/definitely/not/a/route")

    assert sample("hookrelay_http_requests_total", **labels) == before + 1
    # Unknown paths share one label value instead of creating new series.
    assert sample("hookrelay_http_requests_total", **unmatched) == unmatched_before + 1


async def test_metrics_report_queue_depth_and_events_by_status(client, redis_client):
    endpoint_id = await _create_endpoint(client)
    for _ in range(2):
        await client.post(
            f"/api/v1/endpoints/{endpoint_id}/events",
            json={"event_type": "x", "payload": {}},
        )
    await redis_client.zadd(WEBHOOK_DELAYED_QUEUE, {"some-event": 1.0})

    resp = await client.get("/metrics")
    assert resp.status_code == 200

    assert sample("hookrelay_queue_depth", queue="delivery") == 2
    assert sample("hookrelay_queue_depth", queue="delayed") == 1
    assert sample("hookrelay_events", status="pending") == 2
    assert sample("hookrelay_events", status="failed") == 0


async def test_metrics_still_served_when_redis_is_down(
    client, redis_client, failing_redis
):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "hookrelay_http_requests_total" in resp.text


# --- health ----------------------------------------------------------------


async def test_liveness_does_not_touch_dependencies(client, failing_redis):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_ok_when_dependencies_are_up(client, redis_client):
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "redis": "ok"},
    }


async def test_readiness_503_when_redis_is_down(client, redis_client, failing_redis):
    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"] == {"postgres": "ok", "redis": "error: RedisError"}


async def test_readiness_503_when_postgres_is_down(client, redis_client):
    # Port 1 refuses connections immediately: a deterministic "DB down".
    dead_engine = create_async_engine(
        "postgresql+asyncpg://user:secret-password@127.0.0.1:1/nope"
    )
    Session = async_sessionmaker(dead_engine, expire_on_commit=False)

    async def _dead_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _dead_db
    try:
        resp = await client.get("/health/ready")
    finally:
        await dead_engine.dispose()

    assert resp.status_code == 503
    checks = resp.json()["checks"]
    assert checks["redis"] == "ok"
    assert checks["postgres"].startswith("error: ")
    # Only the exception type is exposed, never connection details.
    assert "secret-password" not in resp.text
    assert "127.0.0.1" not in resp.text


async def test_ops_endpoints_are_not_versioned(client, redis_client):
    """Ops endpoints live at the root so scrapers and probes need no prefix."""
    assert (await client.get("/metrics")).status_code == 200
    assert (await client.get("/api/v1/metrics")).status_code == 404
