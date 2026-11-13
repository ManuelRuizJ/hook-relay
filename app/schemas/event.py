from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.event import EventStatus


class EventCreate(BaseModel):
    """Payload accepted by POST /endpoints/{id}/events."""

    event_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Free-form label, e.g. 'order.created'.",
        examples=["order.created"],
    )
    payload: dict[str, Any] = Field(
        ...,
        description="Arbitrary JSON object delivered to the endpoint.",
        examples=[{"order_id": 42, "amount": 1999, "currency": "MXN"}],
    )


class EventRead(BaseModel):
    """Public view of an event, returned by POST and GET endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    endpoint_id: UUID
    event_type: str
    payload: dict[str, Any]
    status: EventStatus
    created_at: datetime


class EventStatusRead(BaseModel):
    """Operational view: exposes delivery progress for debugging."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: EventStatus
    attempts: int
    max_attempts: int
    next_attempt_at: datetime
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DeadLetterRead(EventStatusRead):
    """A dead-lettered event: delivery progress plus what it was and where it went."""

    endpoint_id: UUID
    event_type: str
