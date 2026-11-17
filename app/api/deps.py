from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import get_redis_client
from app.repositories.api_key_repository import ApiKeyRepository
from app.repositories.event_repository import EventRepository
from app.repositories.webhook_endpoint_repository import WebhookEndpointRepository
from app.services.api_key_service import ApiKeyService
from app.services.event_service import EventService
from app.services.webhook_endpoint_service import WebhookEndpointService

# Every provider here is `async def` on purpose. FastAPI runs plain `def`
# dependencies in a thread pool, so each one costs a thread hop per request;
# under load those hops, not the application code, dominated the profile.


async def get_redis() -> Redis:
    """The shared Redis client, as an async dependency (no thread hop)."""
    return get_redis_client()


DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]


async def get_webhook_endpoint_service(session: DbSession) -> WebhookEndpointService:
    return WebhookEndpointService(WebhookEndpointRepository(session))


async def get_event_service(session: DbSession, redis: RedisDep) -> EventService:
    return EventService(
        session=session,
        event_repo=EventRepository(session),
        endpoint_repo=WebhookEndpointRepository(session),
        redis=redis,
    )


WebhookEndpointServiceDep = Annotated[
    WebhookEndpointService, Depends(get_webhook_endpoint_service)
]


EventServiceDep = Annotated[EventService, Depends(get_event_service)]


async def get_api_key_service(session: DbSession) -> ApiKeyService:
    return ApiKeyService(ApiKeyRepository(session))


ApiKeyServiceDep = Annotated[ApiKeyService, Depends(get_api_key_service)]
