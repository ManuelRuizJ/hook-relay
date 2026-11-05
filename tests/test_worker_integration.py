"""Integration tests for the worker's event processing.

These exercise _handle_event end-to-end (session + Redis + delivery),
using httpx.MockTransport to avoid network I/O. Deliberately do NOT
mock EventService or the repositories: the point is to catch wiring
bugs (missing imports, wrong deps) that a unit test of the service
would miss.
"""
import pytest_asyncio
import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.queue import WEBHOOK_DELAYED_QUEUE, WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event, EventStatus
from app.models.webhook_endpoint import WebhookEndpoint
from app.repositories.event_repository import EventRepository
from app.repositories.webhook_endpoint_repository import WebhookEndpointRepository
from app.worker.main import _handle_event

@pytest_asyncio.fixture(autouse=True)
async def worker_session_factory(db_engine, monkeypatch):
    """Bind app.worker.main.SessionLocal to the test DB engine.

    _handle_event opens its own session via SessionLocal (the app's global
    engine). Without this patch, tests seed data into the test DB but the
    worker reads from the dev DB -> 'event not found', no transitions, and
    asyncpg connections bleed across event loops. Pinning SessionLocal to
    the per-test engine fixes both symptoms with one line.
    """
    import app.worker.main as worker_main

    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr(worker_main, "SessionLocal", Session)
    yield Session

async def _seed_event(session, *, max_attempts: int = 5, url: str):
    ep_repo = WebhookEndpointRepository(session)
    endpoint = await ep_repo.create(
        WebhookEndpoint(url=url, secret="test-secret")
    )
    await session.flush()
    event_repo = EventRepository(session)
    event = await event_repo.create(
        Event(
            endpoint_id=endpoint.id,
            event_type="test.event",
            payload={"n": 1},
            status=EventStatus.PENDING,
            max_attempts=max_attempts,
        )
    )
    await session.commit()
    return event


async def _fetch_event(db_engine, event_id):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        return await session.get(Event, event_id)


async def test_handle_event_delivers_on_success(db_engine, redis_client):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        event = await _seed_event(
            session, url="http://receiver.example/hooks"
        )
        event_id = event.id

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-HookRelay-Id"] == str(event_id)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await _handle_event(client, str(event_id))

    refreshed = await _fetch_event(db_engine, event_id)
    assert refreshed.status == EventStatus.DELIVERED
    assert refreshed.attempts == 1
    assert refreshed.delivered_at is not None
    assert refreshed.last_error is None
    assert await redis_client.zcard(WEBHOOK_DELAYED_QUEUE) == 0


async def test_handle_event_marks_failed_when_max_attempts_exhausted(
    db_engine, redis_client
):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        event = await _seed_event(
            session, max_attempts=1, url="http://receiver.example/hooks"
        )
        event_id = event.id

    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        await _handle_event(client, str(event_id))

    refreshed = await _fetch_event(db_engine, event_id)
    assert refreshed.status == EventStatus.FAILED
    assert refreshed.attempts == 1
    assert refreshed.delivered_at is None
    assert refreshed.last_error is not None
    assert await redis_client.zcard(WEBHOOK_DELAYED_QUEUE) == 0


async def test_handle_event_schedules_retry_when_budget_remains(
    db_engine, redis_client
):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        event = await _seed_event(
            session, max_attempts=3, url="http://receiver.example/hooks"
        )
        event_id = event.id

    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        await _handle_event(client, str(event_id))

    refreshed = await _fetch_event(db_engine, event_id)
    assert refreshed.status == EventStatus.PENDING
    assert refreshed.attempts == 1
    assert refreshed.delivered_at is None
    assert refreshed.last_error is not None
    assert refreshed.next_attempt_at > refreshed.created_at

    # Event is now waiting in the delayed ZSET, not the delivery queue.
    assert await redis_client.zcard(WEBHOOK_DELAYED_QUEUE) == 1
    assert await redis_client.llen(WEBHOOK_DELIVERY_QUEUE) == 0
    members = await redis_client.zrange(WEBHOOK_DELAYED_QUEUE, 0, -1)
    assert members == [str(event_id)]