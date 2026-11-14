import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue import WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event, EventStatus
from app.repositories.event_repository import EventRepository
from app.repositories.webhook_endpoint_repository import WebhookEndpointRepository
from app.schemas.event import EventCreate
from app.services.exceptions import (
    EventNotFound,
    IdempotencyKeyReused,
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


@dataclass(frozen=True)
class IngestResult:
    event: Event
    # True when an Idempotency-Key matched an earlier request and the
    # original event is returned instead of creating a new one.
    replayed: bool


def request_fingerprint(data: EventCreate) -> str:
    """SHA-256 of the request body in canonical form (sorted keys, no spaces).

    Two bodies that differ only in key order or whitespace are the same
    request; any change to event_type or payload is a different one.
    """
    canonical = json.dumps(
        {"event_type": data.event_type, "payload": data.payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


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

    async def create_and_enqueue(
        self,
        endpoint_id: UUID,
        data: EventCreate,
        *,
        idempotency_key: str | None = None,
    ) -> IngestResult:
        """Persist and enqueue an event, at most once per Idempotency-Key.

        With a key, a retried request (same key, same body) gets the
        original event back and nothing new is created or enqueued. The
        replay check runs before the is_active check, so a client retrying
        a request that already succeeded gets the same answer even if the
        endpoint was deactivated in between.
        """
        endpoint = await self.endpoint_repo.get_by_id(endpoint_id)
        if endpoint is None:
            raise WebhookNotFound(endpoint_id)

        fingerprint = request_fingerprint(data) if idempotency_key else None
        if idempotency_key:
            existing = await self.event_repo.get_by_idempotency_key(
                endpoint_id, idempotency_key
            )
            if existing is not None:
                return self._replay(existing, idempotency_key, fingerprint)

        if not endpoint.is_active:
            raise WebhookEndpointInactive(endpoint_id)

        event = Event(
            endpoint_id=endpoint_id,
            event_type=data.event_type,
            payload=data.payload,
            status=EventStatus.PENDING,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        try:
            event = await self.event_repo.create(event)
            # DB is the source of truth: commit before touching the queue so
            # a Redis failure cannot create an event that nobody knows about.
            await self.session.commit()
        except IntegrityError:
            # Two requests with the same key raced past the lookup above;
            # the unique constraint let exactly one insert win. Return it.
            if idempotency_key is None:
                raise
            await self.session.rollback()
            existing = await self.event_repo.get_by_idempotency_key(
                endpoint_id, idempotency_key
            )
            if existing is None:
                raise
            return self._replay(existing, idempotency_key, fingerprint)

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

        return IngestResult(event=event, replayed=False)

    def _replay(
        self, existing: Event, idempotency_key: str, fingerprint: str | None
    ) -> IngestResult:
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyKeyReused(existing.endpoint_id, idempotency_key)
        logger.info(
            "Idempotent replay of event %s",
            existing.id,
            extra={
                "event_id": str(existing.id),
                "endpoint_id": str(existing.endpoint_id),
                "idempotency_key": idempotency_key,
            },
        )
        return IngestResult(event=existing, replayed=True)

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
