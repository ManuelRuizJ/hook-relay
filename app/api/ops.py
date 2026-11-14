"""Operational endpoints: health and metrics. Mounted at the root, not /api/v1."""

import asyncio
import logging
from collections.abc import Awaitable

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DbSession, RedisDep
from app.api.metrics import refresh_state_gauges

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])

# A dependency that hangs must not hang the probe: fail it after this long.
READINESS_TIMEOUT_SECONDS = 2.0


class ReadinessRead(BaseModel):
    status: str
    checks: dict[str, str]


async def _check(name: str, probe: Awaitable[object]) -> str:
    try:
        await asyncio.wait_for(probe, timeout=READINESS_TIMEOUT_SECONDS)
    except Exception as exc:  # any failure means "not ready"
        logger.warning(
            "Readiness check %s failed", name, extra={"check": name}, exc_info=True
        )
        # Only the exception type: connection strings can carry credentials.
        return f"error: {type(exc).__name__}"
    return "ok"


@router.get("/health", summary="Liveness: the API process is up")
async def health() -> dict[str, str]:
    """Does not touch dependencies, so a database outage never gets the
    container restarted. Use /health/ready to gate traffic."""
    return {"status": "ok"}


@router.get(
    "/health/ready",
    response_model=ReadinessRead,
    summary="Readiness: Postgres and Redis are reachable",
    responses={503: {"model": ReadinessRead, "description": "A dependency is down"}},
)
async def ready(
    session: DbSession, redis: RedisDep, response: Response
) -> ReadinessRead:
    checks = {
        "postgres": await _check("postgres", session.execute(text("SELECT 1"))),
        "redis": await _check("redis", redis.ping()),
    }
    if checks["postgres"] != "ok":
        # Leave the session clean so get_db's commit does not raise on it.
        await session.rollback()

    is_ready = all(result == "ok" for result in checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessRead(status="ok" if is_ready else "unavailable", checks=checks)


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=Response,
    responses={200: {"content": {CONTENT_TYPE_LATEST: {}}}},
)
async def metrics(session: DbSession, redis: RedisDep) -> Response:
    await refresh_state_gauges(session, redis)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
