from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.core.database import get_db
from app.core.redis import get_redis_client
from app.repositories.webhook_endpoint_repository import WebhookEndpointRepository
from app.services.webhook_endpoint_service import WebhookEndpointService

DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis_client)]

def get_webhook_endpoint_service(session: DbSession) -> WebhookEndpointService:
    return WebhookEndpointService(WebhookEndpointRepository(session))


WebhookEndpointServiceDep = Annotated[
    WebhookEndpointService, Depends(get_webhook_endpoint_service)
]