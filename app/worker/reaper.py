import asyncio
import logging
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.queue import WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event, EventStatus

logger = logging.getLogger(__name__)

# How long a pending event can be "due" before we consider it orphaned.
# Long enough for a live worker to claim it (BRPOP + mark_in_progress is
# milliseconds) without racing a healthy delivery.
STALE_THRESHOLD_SECONDS = 30

# Batch size: avoid saturating Redis if thousands are orphaned at once.
BATCH_SIZE = 100


async def reap_orphaned_events(
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    *,
    stale_threshold_seconds: int = STALE_THRESHOLD_SECONDS,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Re-enqueue events that are pending and *not* waiting on a backoff.

    An event is orphaned when it is `pending`, its `next_attempt_at` is
    already in the past (by more than the threshold) and no worker has
    claimed it. This happens if a worker died between BRPOP and
    mark_in_progress, or if LPUSH failed after the DB commit.

    Atomically: mark `next_attempt_at = now + threshold` so the next
    reaper cycle does not re-pick it, then LPUSH. If the worker still
    does not pick it up, the next cycle will catch it again.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=stale_threshold_seconds
    )
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        stmt = (
            select(Event)
            .where(
                Event.status == EventStatus.PENDING,
                Event.next_attempt_at <= cutoff,
            )
            .order_by(Event.next_attempt_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        events = result.scalars().all()

        if not events:
            return 0

        new_next_attempt = now + timedelta(seconds=stale_threshold_seconds)
        for event in events:
            event.next_attempt_at = new_next_attempt
        await session.commit()

    # LPUSH after commit: if LPUSH fails, the event is still pending in DB
    # and the next reaper cycle will pick it up again. Same guarantee as
    # the ingestion path.
    for event in events:
        await redis.lpush(WEBHOOK_DELIVERY_QUEUE, str(event.id))

    logger.info("Re-queued %d orphaned event(s)", len(events))
    return len(events)


async def reaper_loop(
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    *,
    interval_seconds: int = 30,
) -> None:
    """Run reap_orphaned_events forever, sleeping `interval_seconds` between cycles.

    Designed to run as a sibling task of the BRPOP loop. Cancelled by the
    worker's shutdown signal.
    """
    while True:
        try:
            await reap_orphaned_events(session_factory, redis)
        except Exception:
            # The reaper must not kill the worker. A transient DB/Redis
            # error is logged and retried next cycle.
            logger.exception("Reaper cycle failed; will retry")
        await asyncio_sleep(interval_seconds)


# Small indirection so tests can patch it without touching asyncio.
async def asyncio_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
