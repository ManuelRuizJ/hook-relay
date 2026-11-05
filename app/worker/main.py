import asyncio
import logging
import signal
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.core.queue import WEBHOOK_DELIVERY_QUEUE
from app.core.redis import close_redis_client, get_redis_client
from app.models.event import Event
from app.models.webhook_endpoint import WebhookEndpoint
from app.worker.delivery import HTTP_TIMEOUT, deliver_event
from app.repositories.event_repository import EventRepository
from app.repositories.webhook_endpoint_repository import WebhookEndpointRepository
from app.services.event_service import EventService

logger = logging.getLogger(__name__)

BRPOP_TIMEOUT_SECONDS = 5


async def _fetch_event_and_endpoint(
    session: AsyncSession, event_id: UUID
) -> tuple[Event, WebhookEndpoint] | None:
    stmt = (
        select(Event, WebhookEndpoint)
        .join(WebhookEndpoint, Event.endpoint_id == WebhookEndpoint.id)
        .where(Event.id == event_id)
    )
    result = await session.execute(stmt)
    return result.one_or_none()


async def _handle_event(client: httpx.AsyncClient, event_id: str) -> None:
    try:
        parsed_id = UUID(event_id)
    except ValueError:
        logger.warning("Dropping malformed event id from queue: %r", event_id)
        return

    async with SessionLocal() as session:
        row = await _fetch_event_and_endpoint(session, parsed_id)
        if row is None:
            logger.warning(
                "Event %s not found (endpoint deleted?); dropping", event_id
            )
            return
        event, endpoint = row

        # Claim the event before the network call so a concurrent worker
        # (Phase 5+) cannot also pick it up.
        service = EventService(
            session=session,
            event_repo=EventRepository(session),
            endpoint_repo=WebhookEndpointRepository(session),
            redis=get_redis_client(),
        )
        await service.mark_in_progress(event)

        result = await deliver_event(client, event, endpoint)

        if result.success:
            await service.mark_delivered(event)
        else:
            await service.mark_failed(event, result.error or "unknown error")
            logger.info(
                "Event %s marked failed after %d attempt(s)",
                event.id,
                event.attempts,
            )

async def run_worker(shutdown: asyncio.Event) -> None:
    redis = get_redis_client()
    logger.info("Worker started; waiting on %s", WEBHOOK_DELIVERY_QUEUE)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            while not shutdown.is_set():
                result = await redis.brpop(
                    [WEBHOOK_DELIVERY_QUEUE], timeout=BRPOP_TIMEOUT_SECONDS
                )
                if result is None:
                    continue
                _queue, event_id = result
                await _handle_event(client, event_id)
        finally:
            logger.info("Worker shutting down")
            await close_redis_client()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    shutdown = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    try:
        loop.run_until_complete(run_worker(shutdown))
    finally:
        loop.close()


if __name__ == "__main__":
    main()