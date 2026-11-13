"""Operational endpoints: health and metrics. Mounted at the root, not /api/v1."""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.deps import DbSession, RedisDep
from app.api.metrics import refresh_state_gauges

router = APIRouter(tags=["ops"])


@router.get("/health", summary="Liveness: the API process is up")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=Response,
    responses={200: {"content": {CONTENT_TYPE_LATEST: {}}}},
)
async def metrics(session: DbSession, redis: RedisDep) -> Response:
    await refresh_state_gauges(session, redis)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
