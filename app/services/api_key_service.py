from datetime import UTC, datetime
from uuid import UUID

from app.core.security import display_prefix, generate_api_key, hash_api_key
from app.models.api_key import ApiKey
from app.repositories.api_key_repository import ApiKeyRepository
from app.services.exceptions import ApiKeyNotFound


class ApiKeyService:
    def __init__(self, repo: ApiKeyRepository) -> None:
        self.repo = repo

    async def create(self, name: str) -> tuple[ApiKey, str]:
        """Create a key and return it with its plaintext, the only time
        the plaintext exists outside the caller."""
        raw = generate_api_key()
        api_key = await self.repo.create(
            ApiKey(name=name, prefix=display_prefix(raw), key_hash=hash_api_key(raw))
        )
        return api_key, raw

    async def list(self) -> list[ApiKey]:
        return await self.repo.list()

    async def revoke(self, key_id: UUID) -> ApiKey:
        """Revoke a key; revoking an already revoked key is a no-op."""
        api_key = await self.repo.get_by_id(key_id)
        if api_key is None:
            raise ApiKeyNotFound(key_id)
        if api_key.revoked_at is None:
            api_key.revoked_at = datetime.now(UTC)
            api_key = await self.repo.update(api_key)
        return api_key
