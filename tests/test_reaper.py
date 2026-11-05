"""Tests for the orphaned-pending reaper.

Focus: the reaper must (a) pick up stale pending events, (b) ignore
events that are still on a valid retry backoff, (c) ignore events the
worker currently claims as in_progress.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.queue import WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event, EventStatus
from app.models.webhook_endpoint import WebhookEndpoint
from app.worker.reaper import reap_orphaned_events


async def _seed_pending_event(
    session,
    *,
    next_attempt_at: datetime,
    status: EventStatus = EventStatus.PENDING,
):
    endpoint = WebhookEndpoint(
        url="http://receiver.example/hooks", secret="x"
    )
    session.add(endpoint)
    await session.flush()
    event = Event(
        endpoint_id=endpoint.id,
        event_type="test",
        payload={},
        status=status,
        next_attempt_at=next_attempt_at,
    )
    session.add(event)
    await session.commit()
    return event


async def test_reaper_requeues_stale_pending_events(db_engine, redis_client):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        # 5 minutes in the past -> orphaned
        event = await _seed_pending_event(
            session,
            next_attempt_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        event_id = event.id

    moved = await reap_orphaned_events(Session, redis_client)
    assert moved == 1

    queue = await redis_client.lrange(WEBHOOK_DELIVERY_QUEUE, 0, -1)
    assert str(event_id) in queue

    async with Session() as session:
        refreshed = await session.get(Event, event_id)
        # Reaper pushed next_attempt_at forward to prevent immediate re-pick.
        assert refreshed.next_attempt_at > datetime.now(timezone.utc)


async def test_reaper_ignores_recent_pending_events(db_engine, redis_client):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        # 5 seconds in the past -> not stale yet
        await _seed_pending_event(
            session,
            next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )

    moved = await reap_orphaned_events(Session, redis_client)
    assert moved == 0
    assert await redis_client.llen(WEBHOOK_DELIVERY_QUEUE) == 0


async def test_reaper_ignores_in_progress_events(db_engine, redis_client):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        # in_progress, even if very old, must not be touched
        await _seed_pending_event(
            session,
            status=EventStatus.IN_PROGRESS,
            next_attempt_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

    moved = await reap_orphaned_events(Session, redis_client)
    assert moved == 0
