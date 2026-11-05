from fastapi import FastAPI

from app.api.v1.router import api_router

app = FastAPI(
    title="HookRelay",
    description="Webhook delivery service with retries and dead-letter queue.",
    version="0.2.0",
)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}