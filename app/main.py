from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app import __version__
from app.api import ops
from app.api.auth import require_api_key
from app.api.metrics import PrometheusMiddleware
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.redis import close_redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs after uvicorn has set up its own logging, so this takes precedence.
    configure_logging(settings.log_level, settings.log_format)
    yield
    await close_redis_client()


app = FastAPI(
    title="HookRelay",
    description="Webhook delivery service with retries and dead-letter queue.",
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(PrometheusMiddleware)
app.include_router(ops.router)
# Every /api/v1 route requires an API key; ops endpoints (health, metrics)
# stay open for probes and Prometheus.
app.include_router(
    api_router, prefix="/api/v1", dependencies=[Depends(require_api_key)]
)
