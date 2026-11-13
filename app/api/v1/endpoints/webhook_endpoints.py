from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import WebhookEndpointServiceDep
from app.schemas.webhook_endpoint import (
    WebhookEndpointCreate,
    WebhookEndpointRead,
    WebhookEndpointUpdate,
    WebhookEndpointWithSecret,
)
from app.services.exceptions import WebhookNotFound

router = APIRouter(prefix="/endpoints", tags=["webhook-endpoints"])


@router.post(
    "",
    response_model=WebhookEndpointWithSecret,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook endpoint",
    description="Returns the signing secret **only once**. Store it securely.",
)
async def create_endpoint(
    payload: WebhookEndpointCreate, service: WebhookEndpointServiceDep
) -> WebhookEndpointWithSecret:
    endpoint = await service.create(payload)
    return WebhookEndpointWithSecret.model_validate(endpoint)


@router.get("", response_model=list[WebhookEndpointRead])
async def list_endpoints(
    service: WebhookEndpointServiceDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[WebhookEndpointRead]:
    endpoints = await service.list(skip=skip, limit=limit)
    return [WebhookEndpointRead.model_validate(e) for e in endpoints]


@router.get("/{endpoint_id}", response_model=WebhookEndpointRead)
async def get_endpoint(
    endpoint_id: UUID, service: WebhookEndpointServiceDep
) -> WebhookEndpointRead:
    try:
        endpoint = await service.get(endpoint_id)
    except WebhookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WebhookEndpointRead.model_validate(endpoint)


@router.patch("/{endpoint_id}", response_model=WebhookEndpointRead)
async def update_endpoint(
    endpoint_id: UUID,
    payload: WebhookEndpointUpdate,
    service: WebhookEndpointServiceDep,
) -> WebhookEndpointRead:
    try:
        endpoint = await service.update(endpoint_id, payload)
    except WebhookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WebhookEndpointRead.model_validate(endpoint)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    endpoint_id: UUID, service: WebhookEndpointServiceDep
) -> None:
    try:
        await service.delete(endpoint_id)
    except WebhookNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
