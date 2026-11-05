from redis.asyncio import Redis, from_url

from app.core.config import settings

_client: Redis | None = None


def get_redis_client() -> Redis:
    """Return a process-wide async Redis client.

    The client owns an internal connection pool, so callers must not
    create new instances per request. ``decode_responses=True`` returns
    ``str`` instead of ``bytes``, which is what Pydantic and JSON expect.
    """
    global _client
    if _client is None:
        _client = from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis_client() -> None:
    """Close the shared client. Called from FastAPI's lifespan on shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None