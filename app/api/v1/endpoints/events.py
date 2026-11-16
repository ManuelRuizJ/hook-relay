from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Response, status

from app.api.deps import EventServiceDep
from app.schemas.event import EventCreate, EventRead, EventStatusRead
from app.services.exceptions import (
    EventNotFound,
    IdempotencyKeyReused,
    WebhookEndpointInactive,
    WebhookNotFound,
)

router = APIRouter(tags=["events"])


IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description=(
            "Optional. Retrying with the same key and body returns the "
            "original event instead of creating a duplicate. Keys are unique "
            "per endpoint."
        ),
    ),
]


@router.post(
    "/endpoints/{endpoint_id}/events",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest an event for a webhook endpoint",
    description=(
        "Persists the event with status `pending` and pushes its id onto "
        "the delivery queue. Enqueue is best-effort: if Redis is down the "
        "event stays `pending` and the reaper re-enqueues it.\n\n"
        "Send an `Idempotency-Key` header to make retries safe: a repeated "
        "key with the same body returns the original event with `200` and "
        "`Idempotent-Replayed: true`."
    ),
    responses={
        200: {
            "model": EventRead,
            "description": "Idempotent replay: the event created by an earlier "
            "request with the same Idempotency-Key",
        },
        404: {"description": "Webhook endpoint not found"},
        409: {"description": "Webhook endpoint is inactive"},
        422: {"description": "Idempotency-Key reused with a different body"},
    },
)
async def ingest_event(
    endpoint_id: UUID,
    payload: EventCreate,
    service: EventServiceDep,
    response: Response,
    idempotency_key: IdempotencyKeyHeader = None,
) -> EventRead:
    try:
        result = await service.create_and_enqueue(
            endpoint_id, payload, idempotency_key=idempotency_key
        )
    except WebhookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WebhookEndpointInactive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdempotencyKeyReused as exc:
        # As in the IETF Idempotency-Key draft: same key, different payload.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return EventRead.model_validate(result.event)


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
