from fastapi import FastAPI

app = FastAPI(
    title="HookRelay",
    description="Webhook delivery service with retries and dead-letter queue.",
    version="0.1.0",
)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}