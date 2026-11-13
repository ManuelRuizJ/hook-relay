import asyncio
import logging
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.queue import WEBHOOK_DELAYED_QUEUE, WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event, EventStatus
from app.worker.backoff import compute_backoff

logger = logging.getLogger(__name__)

# How long a pending event can be "due" before we consider it orphaned.
# Long enough for a live worker to claim it (BRPOP + mark_in_progress is
# milliseconds) without racing a healthy delivery.
STALE_THRESHOLD_SECONDS = 30

# Batch size: avoid saturating Redis if thousands are orphaned at once.
BATCH_SIZE = 100

# How long an event may stay `in_progress` before we assume the worker that
# claimed it is gone. A delivery is bounded by HTTP_TIMEOUT (~25s worst
# case), so 120s leaves a wide margin and a live worker is never preempted.
IN_PROGRESS_TIMEOUT_SECONDS = 120

STUCK_IN_PROGRESS_ERROR = "worker lost claim: in_progress timeout"


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
    cutoff = datetime.now(UTC) - timedelta(
        seconds=stale_threshold_seconds
    )
    now = datetime.now(UTC)

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


async def reap_stuck_in_progress(
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    *,
    timeout_seconds: int = IN_PROGRESS_TIMEOUT_SECONDS,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Release events whose claim outlived any plausible delivery.

    A worker that dies mid-POST leaves its event `in_progress` forever.
    `updated_at` is the claim time: mark_in_progress is the last write to
    an in_progress row, and nothing else touches it until the outcome.

    The lost claim counts as a failed attempt (attempts was already
    incremented when the event was claimed), so the same rule as
    EventService.mark_failed applies:

    - budget left  -> pending with backoff, scheduled on webhooks:delayed;
    - budget spent -> failed.

    ZADD happens after commit. If it fails, the event is pending in the DB
    and the orphan reaper re-enqueues it once it goes stale.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=timeout_seconds)

    async with session_factory() as session:
        stmt = (
            select(Event)
            .where(
                Event.status == EventStatus.IN_PROGRESS,
                Event.updated_at <= cutoff,
            )
            .order_by(Event.updated_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        events = result.scalars().all()

        if not events:
            return 0

        to_retry: dict[str, float] = {}
        for event in events:
            event.last_error = STUCK_IN_PROGRESS_ERROR
            if event.attempts >= event.max_attempts:
                event.status = EventStatus.FAILED
            else:
                event.status = EventStatus.PENDING
                # attempts >= 1 for any real claim; max() guards hand-edited rows.
                event.next_attempt_at = now + timedelta(
                    seconds=compute_backoff(max(event.attempts, 1))
                )
                to_retry[str(event.id)] = event.next_attempt_at.timestamp()
        await session.commit()

    if to_retry:
        await redis.zadd(WEBHOOK_DELAYED_QUEUE, to_retry)

    logger.info(
        "Released %d stuck in_progress event(s): %d rescheduled, %d failed",
        len(events),
        len(to_retry),
        len(events) - len(to_retry),
    )
    return len(events)


async def reaper_loop(
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    *,
    interval_seconds: int = 30,
) -> None:
    """Run both recovery passes forever, sleeping `interval_seconds` between cycles.

    Designed to run as a sibling task of the BRPOP loop. Cancelled by the
    worker's shutdown signal. Each pass is guarded separately so a failure
    in one does not skip the other.
    """
    while True:
        for reap in (reap_stuck_in_progress, reap_orphaned_events):
            try:
                await reap(session_factory, redis)
            except Exception:
                # The reaper must not kill the worker. A transient DB/Redis
                # error is logged and retried next cycle.
                logger.exception("Reaper cycle failed in %s; will retry", reap.__name__)
        await asyncio_sleep(interval_seconds)


# Small indirection so tests can patch it without touching asyncio.
async def asyncio_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
