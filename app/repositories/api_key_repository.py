from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, api_key: ApiKey) -> ApiKey:
        self.session.add(api_key)
        await self.session.flush()
        await self.session.refresh(api_key)
        return api_key

    async def get_by_id(self, key_id: UUID) -> ApiKey | None:
        return await self.session.get(ApiKey, key_id)

    async def get_active_by_hash(self, key_hash: str) -> ApiKey | None:
        stmt = select(ApiKey).where(
            ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self) -> list[ApiKey]:
        stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, api_key: ApiKey) -> ApiKey:
        await self.session.flush()
        await self.session.refresh(api_key)
        return api_key
