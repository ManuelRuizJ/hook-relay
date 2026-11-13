"""Tests for the reaper's two recovery passes.

Orphaned pending: the reaper must (a) pick up stale pending events,
(b) ignore events that are still on a valid retry backoff, (c) ignore
events the worker currently claims as in_progress.

Stuck in_progress: a claim older than the timeout counts as a failed
attempt (rescheduled with backoff, or failed when the budget is spent);
a recent claim belongs to a live worker and is left alone.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.queue import WEBHOOK_DELAYED_QUEUE, WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event, EventStatus
from app.models.webhook_endpoint import WebhookEndpoint
from app.worker.reaper import (
    STUCK_IN_PROGRESS_ERROR,
    reap_orphaned_events,
    reap_stuck_in_progress,
)


async def _seed_pending_event(
    session,
    *,
    next_attempt_at: datetime,
    status: EventStatus = EventStatus.PENDING,
):
    endpoint = WebhookEndpoint(url="http://receiver.example/hooks", secret="x")
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
            next_attempt_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        event_id = event.id

    moved = await reap_orphaned_events(Session, redis_client)
    assert moved == 1

    queue = await redis_client.lrange(WEBHOOK_DELIVERY_QUEUE, 0, -1)
    assert str(event_id) in queue

    async with Session() as session:
        refreshed = await session.get(Event, event_id)
        # Reaper pushed next_attempt_at forward to prevent immediate re-pick.
        assert refreshed.next_attempt_at > datetime.now(UTC)


async def test_reaper_ignores_recent_pending_events(db_engine, redis_client):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        # 5 seconds in the past -> not stale yet
        await _seed_pending_event(
            session,
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=5),
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
            next_attempt_at=datetime.now(UTC) - timedelta(hours=1),
        )

    moved = await reap_orphaned_events(Session, redis_client)
    assert moved == 0


async def _seed_in_progress_event(
    session,
    *,
    claimed_ago: timedelta,
    attempts: int = 1,
    max_attempts: int = 5,
):
    """Seed an in_progress event whose claim (updated_at) is `claimed_ago` old."""
    endpoint = WebhookEndpoint(url="http://receiver.example/hooks", secret="x")
    session.add(endpoint)
    await session.flush()
    event = Event(
        endpoint_id=endpoint.id,
        event_type="test",
        payload={},
        status=EventStatus.IN_PROGRESS,
        attempts=attempts,
        max_attempts=max_attempts,
        updated_at=datetime.now(UTC) - claimed_ago,
    )
    session.add(event)
    await session.commit()
    return event


async def test_stuck_in_progress_is_rescheduled_with_backoff(db_engine, redis_client):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        event = await _seed_in_progress_event(
            session, claimed_ago=timedelta(minutes=10), attempts=2
        )
        event_id = event.id

    released = await reap_stuck_in_progress(Session, redis_client)
    assert released == 1

    async with Session() as session:
        refreshed = await session.get(Event, event_id)
        assert refreshed.status == EventStatus.PENDING
        # The lost claim already consumed an attempt; it is not refunded.
        assert refreshed.attempts == 2
        assert refreshed.last_error == STUCK_IN_PROGRESS_ERROR
        assert refreshed.next_attempt_at > datetime.now(UTC)

    score = await redis_client.zscore(WEBHOOK_DELAYED_QUEUE, str(event_id))
    assert score == refreshed.next_attempt_at.timestamp()
    # Retries go through the delayed ZSET, never straight to the queue.
    assert await redis_client.llen(WEBHOOK_DELIVERY_QUEUE) == 0


async def test_stuck_in_progress_with_no_budget_left_fails(db_engine, redis_client):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        event = await _seed_in_progress_event(
            session,
            claimed_ago=timedelta(minutes=10),
            attempts=5,
            max_attempts=5,
        )
        event_id = event.id

    released = await reap_stuck_in_progress(Session, redis_client)
    assert released == 1

    async with Session() as session:
        refreshed = await session.get(Event, event_id)
        assert refreshed.status == EventStatus.FAILED
        assert refreshed.last_error == STUCK_IN_PROGRESS_ERROR

    assert await redis_client.zscore(WEBHOOK_DELAYED_QUEUE, str(event_id)) is None


async def test_recent_in_progress_is_left_to_its_worker(db_engine, redis_client):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        # Claimed 10s ago: a live worker may still be mid-delivery.
        event = await _seed_in_progress_event(
            session, claimed_ago=timedelta(seconds=10)
        )
        event_id = event.id

    released = await reap_stuck_in_progress(Session, redis_client)
    assert released == 0

    async with Session() as session:
        refreshed = await session.get(Event, event_id)
        assert refreshed.status == EventStatus.IN_PROGRESS
    assert await redis_client.zcard(WEBHOOK_DELAYED_QUEUE) == 0
