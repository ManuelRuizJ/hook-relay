from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import EventServiceDep
from app.schemas.event import EventCreate, EventRead, EventStatusRead
from app.services.exceptions import (
    EventNotFound,
    WebhookEndpointInactive,
    WebhookNotFound,
)

router = APIRouter(tags=["events"])


@router.post(
    "/endpoints/{endpoint_id}/events",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest an event for a webhook endpoint",
    description=(
        "Persists the event with status `pending` and pushes its id onto "
        "the delivery queue. Enqueue is best-effort: if Redis is down the "
        "event stays `pending` and the reaper re-enqueues it."
    ),
    responses={
        404: {"description": "Webhook endpoint not found"},
        409: {"description": "Webhook endpoint is inactive"},
    },
)
async def ingest_event(
    endpoint_id: UUID,
    payload: EventCreate,
    service: EventServiceDep,
) -> EventRead:
    try:
        event = await service.create_and_enqueue(endpoint_id, payload)
    except WebhookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WebhookEndpointInactive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return EventRead.model_validate(event)


@router.get(
    "/events/{event_id}",
    response_model=EventStatusRead,
    summary="Get the delivery status of an event",
    responses={404: {"description": "Event not found"}},
)
async def get_event(event_id: UUID, service: EventServiceDep) -> EventStatusRead:
    try:
        event = await service.get(event_id)
    except EventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return EventStatusRead.model_validate(event)