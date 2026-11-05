from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class WebhookEndpointCreate(BaseModel):
    url: HttpUrl = Field(..., max_length=2048)
    description: str | None = Field(default=None, max_length=500)


class WebhookEndpointUpdate(BaseModel):
    url: HttpUrl | None = Field(default=None, max_length=2048)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class WebhookEndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WebhookEndpointWithSecret(WebhookEndpointRead):
    """Returned only on creation — the caller needs to store the secret."""
    secret: str