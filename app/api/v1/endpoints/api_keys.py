from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import ApiKeyServiceDep
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.services.exceptions import ApiKeyNotFound

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
    description="Returns the key **only once**. Only its hash is stored.",
)
async def create_api_key(
    payload: ApiKeyCreate, service: ApiKeyServiceDep
) -> ApiKeyCreated:
    api_key, raw = await service.create(payload.name)
    return ApiKeyCreated(**ApiKeyRead.model_validate(api_key).model_dump(), key=raw)


@router.get(
    "",
    response_model=list[ApiKeyRead],
    summary="List API keys",
    description="Active and revoked keys, newest first. Keys are never returned.",
)
async def list_api_keys(service: ApiKeyServiceDep) -> list[ApiKeyRead]:
    return [ApiKeyRead.model_validate(k) for k in await service.list()]


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
    description=(
        "Takes effect on the next request. Idempotent. Revoking the key "
        "used for this request is allowed; create a new one first, or use "
        "the CLI to recover access."
    ),
    responses={404: {"description": "API key not found"}},
)
async def revoke_api_key(key_id: UUID, service: ApiKeyServiceDep) -> None:
    try:
        await service.revoke(key_id)
    except ApiKeyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
