import logging
from uuid import UUID
from datetime import datetime, timezone

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
    WebhookEndpointInactive,
    WebhookNotFound,
)

logger = logging.getLogger(__name__)


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
        self, endpoint_id: UUID, data: EventCreate
    ) -> Event:
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
                "Failed to enqueue event %s; reaper will recover", event.id
            )

        return event

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
            delivered_at=datetime.now(timezone.utc),
        )

    async def mark_failed(self, event: Event, error: str) -> Event:
        return await self._transition(
            event,
            new_status=EventStatus.FAILED,
            attempts_delta=0,
            last_error=error,
        )