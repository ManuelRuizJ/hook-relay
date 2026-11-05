import secrets
from uuid import UUID

from app.models.webhook_endpoint import WebhookEndpoint
from app.repositories.webhook_endpoint_repository import WebhookEndpointRepository
from app.schemas.webhook_endpoint import WebhookEndpointCreate, WebhookEndpointUpdate
from app.services.exceptions import WebhookNotFound


def _generate_secret() -> str:
    return secrets.token_urlsafe(32)


class WebhookEndpointService:
    def __init__(self, repo: WebhookEndpointRepository) -> None:
        self.repo = repo

    async def create(self, data: WebhookEndpointCreate) -> WebhookEndpoint:
        endpoint = WebhookEndpoint(
            url=str(data.url),
            description=data.description,
            secret=_generate_secret(),
        )
        return await self.repo.create(endpoint)

    async def get(self, endpoint_id: UUID) -> WebhookEndpoint:
        endpoint = await self.repo.get_by_id(endpoint_id)
        if endpoint is None:
            raise WebhookNotFound(endpoint_id)
        return endpoint

    async def list(self, skip: int = 0, limit: int = 100) -> list[WebhookEndpoint]:
        return await self.repo.list(skip=skip, limit=limit)

    async def update(
        self, endpoint_id: UUID, data: WebhookEndpointUpdate
    ) -> WebhookEndpoint:
        endpoint = await self.get(endpoint_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "url" and value is not None:
                value = str(value)
            setattr(endpoint, field, value)
        return await self.repo.update(endpoint)

    async def delete(self, endpoint_id: UUID) -> None:
        endpoint = await self.get(endpoint_id)
        await self.repo.delete(endpoint)