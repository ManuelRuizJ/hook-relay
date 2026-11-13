import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue import WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event, EventStatus
from app.repositories.event_repository import EventRepository
from app.repositories.webhook_endpoint_repository import WebhookEndpointRepository
from app.schemas.event import EventCreate
from app.services.exceptions import (
    EventNotFound,
    InvalidEventTransition,
    WebhookEndpointInactive,
    WebhookNotFound,
)
from app.worker.backoff import compute_backoff

logger = logging.getLogger(__name__)

# The dead-letter queue is not a separate store: an event whose retries are
# exhausted stays in Postgres with status `failed`. `dead` means an operator
# inspected it and chose to archive it instead of replaying.
REPLAYABLE_STATUSES = frozenset({EventStatus.FAILED, EventStatus.DEAD})


class EventService:
    def __init__(
        self,
        session: AsyncSession,
        event_repo: EventRepository,
        endpoint_repo: WebhookEndpointRepository,
        redis: Redis,
    ) -> None:
        self.session = session
        self.event_repo = event_repo
        self.endpoint_repo = endpoint_repo
        self.redis = redis

    async def create_and_enqueue(self, endpoint_id: UUID, data: EventCreate) -> Event:
        endpoint = await self.endpoint_repo.get_by_id(endpoint_id)
        if endpoint is None:
            raise WebhookNotFound(endpoint_id)
        if not endpoint.is_active:
            raise WebhookEndpointInactive(endpoint_id)

        event = Event(
            endpoint_id=endpoint.id,
            event_type=data.event_type,
            payload=data.payload,
            status=EventStatus.PENDING,
        )
        event = await self.event_repo.create(event)

        # DB is the source of truth: commit before touching the queue so a
        # Redis failure cannot create an event that nobody knows about.
        await self.session.commit()

        # Best-effort enqueue. If this fails, the event stays pending in the
        # DB and the Phase 5 reaper will re-enqueue it. Callers still get 201
        # because the resource was created; delivery is a separate concern.
        try:
            await self.redis.lpush(WEBHOOK_DELIVERY_QUEUE, str(event.id))
        except RedisError:
            logger.exception(
                "Failed to enqueue event %s; reaper will recover",
                event.id,
                extra={"event_id": str(event.id)},
            )

        return event

    async def list_dead_letters(
        self,
        *,
        endpoint_id: UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Event]:
        """Events whose retries are exhausted, most recently failed first."""
        return await self.event_repo.list_by_status(
            EventStatus.FAILED, endpoint_id=endpoint_id, skip=skip, limit=limit
        )

    async def _lock(self, event_id: UUID) -> Event:
        event = await self.event_repo.get_by_id_for_update(event_id)
        if event is None:
            raise EventNotFound(event_id)
        return event

    async def replay(self, event_id: UUID) -> Event:
        """Send a dead-lettered event back through delivery with a fresh budget.

        attempts restarts at 0 so the event gets the full max_attempts again.
        last_error is kept for inspection until the next claim clears it.
        Same commit-then-enqueue contract as ingestion: if LPUSH fails, the
        event is pending in the DB and the orphan reaper re-enqueues it.
        """
        event = await self._lock(event_id)
        if event.status not in REPLAYABLE_STATUSES:
            raise InvalidEventTransition(event_id, event.status, "replay")

        endpoint = await self.endpoint_repo.get_by_id(event.endpoint_id)
        if endpoint is None or not endpoint.is_active:
            # Replaying into an inactive endpoint would just fail again.
            raise WebhookEndpointInactive(event.endpoint_id)

        event.status = EventStatus.PENDING
        event.attempts = 0
        event.next_attempt_at = datetime.now(UTC)
        updated = await self.event_repo.update(event)
        await self.session.commit()

        try:
            await self.redis.lpush(WEBHOOK_DELIVERY_QUEUE, str(updated.id))
        except RedisError:
            logger.exception(
                "Failed to enqueue replayed event %s; reaper will recover",
                updated.id,
                extra={"event_id": str(updated.id)},
            )
        logger.info(
            "Event %s replayed from the dead-letter queue",
            updated.id,
            extra={"event_id": str(updated.id)},
        )
        return updated

    async def discard(self, event_id: UUID) -> Event:
        """Archive a dead-lettered event: failed -> dead.

        The event and its last_error are kept for audit, but it no longer
        shows up in the dead-letter listing. It can still be replayed later.
        """
        event = await self._lock(event_id)
        if event.status != EventStatus.FAILED:
            raise InvalidEventTransition(event_id, event.status, "discard")

        event.status = EventStatus.DEAD
        updated = await self.event_repo.update(event)
        await self.session.commit()
        logger.info(
            "Event %s discarded from the dead-letter queue",
            updated.id,
            extra={"event_id": str(updated.id)},
        )
        return updated

    async def get(self, event_id: UUID) -> Event:
        event = await self.event_repo.get_by_id(event_id)
        if event is None:
            raise EventNotFound(event_id)
        return event

    async def _transition(
        self,
        event: Event,
        *,
        new_status: EventStatus,
        attempts_delta: int,
        last_error: str | None = None,
        delivered_at: datetime | None = None,
    ) -> Event:
        event.status = new_status
        event.attempts += attempts_delta
        event.last_error = last_error
        if delivered_at is not None:
            event.delivered_at = delivered_at
        updated = await self.event_repo.update(event)
        await self.session.commit()
        return updated

    async def mark_in_progress(self, event: Event) -> Event:
        return await self._transition(
            event, new_status=EventStatus.IN_PROGRESS, attempts_delta=1
        )

    async def mark_delivered(self, event: Event) -> Event:
        return await self._transition(
            event,
            new_status=EventStatus.DELIVERED,
            attempts_delta=0,
            delivered_at=datetime.now(UTC),
        )

    async def mark_failed(self, event: Event, error: str) -> Event:
        event.last_error = error
        if event.attempts >= event.max_attempts:
            event.status = EventStatus.FAILED
        else:
            backoff_seconds = compute_backoff(event.attempts)
            event.status = EventStatus.PENDING
            event.next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=backoff_seconds
            )
        updated = await self.event_repo.update(event)
        await self.session.commit()
        return updated
