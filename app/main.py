from contextlib import asynccontextmanager

from fastapi import FastAPI

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
    version="0.2.1",
    lifespan=lifespan,
)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
