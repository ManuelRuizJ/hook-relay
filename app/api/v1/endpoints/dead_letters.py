from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import EventServiceDep
from app.schemas.event import DeadLetterRead, EventStatusRead
from app.services.exceptions import (
    EventNotFound,
    InvalidEventTransition,
    WebhookEndpointInactive,
)

router = APIRouter(tags=["dead-letters"])


@router.get(
    "/dead-letters",
    response_model=list[DeadLetterRead],
    summary="List events whose retries are exhausted",
    description=(
        "Returns events with status `failed`, most recently failed first. "
        "Each one can be replayed or discarded."
    ),
)
async def list_dead_letters(
    service: EventServiceDep,
    endpoint_id: UUID | None = Query(None, description="Only this endpoint."),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[DeadLetterRead]:
    events = await service.list_dead_letters(
        endpoint_id=endpoint_id, skip=skip, limit=limit
    )
    return [DeadLetterRead.model_validate(e) for e in events]


@router.post(
    "/events/{event_id}/replay",
    response_model=EventStatusRead,
    summary="Replay a dead-lettered event",
    description=(
        "Moves a `failed` or `dead` event back to `pending` with a fresh "
        "retry budget (`attempts` = 0) and re-enqueues it."
    ),
    responses={
        404: {"description": "Event not found"},
        409: {"description": "Event is not dead-lettered, or its endpoint is inactive"},
    },
)
async def replay_event(event_id: UUID, service: EventServiceDep) -> EventStatusRead:
    try:
        event = await service.replay(event_id)
    except EventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (InvalidEventTransition, WebhookEndpointInactive) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return EventStatusRead.model_validate(event)


@router.post(
    "/events/{event_id}/discard",
    response_model=EventStatusRead,
    summary="Archive a dead-lettered event",
    description=(
        "Moves a `failed` event to `dead`: kept for audit, removed from the "
        "dead-letter listing. It can still be replayed later."
    ),
    responses={
        404: {"description": "Event not found"},
        409: {"description": "Event is not in status `failed`"},
    },
)
async def discard_event(event_id: UUID, service: EventServiceDep) -> EventStatusRead:
    try:
        event = await service.discard(event_id)
    except EventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidEventTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return EventStatusRead.model_validate(event)
