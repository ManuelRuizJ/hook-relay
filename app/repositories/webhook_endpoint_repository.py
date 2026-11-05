from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_endpoint import WebhookEndpoint


class WebhookEndpointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        self.session.add(endpoint)
        await self.session.flush()
        await self.session.refresh(endpoint)
        return endpoint

    async def get_by_id(self, endpoint_id: UUID) -> WebhookEndpoint | None:
        return await self.session.get(WebhookEndpoint, endpoint_id)

    async def list(self, skip: int = 0, limit: int = 100) -> list[WebhookEndpoint]:
        stmt = (
            select(WebhookEndpoint)
            .order_by(WebhookEndpoint.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        await self.session.flush()
        await self.session.refresh(endpoint)
        return endpoint

    async def delete(self, endpoint: WebhookEndpoint) -> None:
        await self.session.delete(endpoint)
        await self.session.flush()