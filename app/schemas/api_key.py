from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="What the key is for, e.g. 'billing-service'.",
        examples=["billing-service"],
    )


class ApiKeyRead(BaseModel):
    """A key as listed: never includes the key itself."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyRead):
    """Returned once, at creation. `key` cannot be retrieved again."""

    key: str
