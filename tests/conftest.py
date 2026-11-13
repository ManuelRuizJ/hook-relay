import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Isolate tests from the developer's running worker (on DB 0). If a worker
# is consuming the delivery queue while tests run, it races the test between
# LPUSH and LRANGE, causing flaky failures. Using a dedicated DB index
# removes the race without requiring "stop the worker before pytest".
# Override with TEST_REDIS_URL in CI if needed.
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://redis:6379/15")

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import close_redis_client, get_redis_client
from app.main import app
from app.models import Base

TEST_DB_URL = settings.database_url.rsplit("/", 1)[0] + f"/{settings.postgres_db}_test"


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(db_engine):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _get_db_override():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def redis_client():
    """Real Redis client bound to the test DB (see os.environ above).

    flushdb() wipes *this* DB only; dev DB 0 is untouched. The module-level
    singleton is reset on both sides so the client binds to the test loop.
    """
    await close_redis_client()
    client = get_redis_client()
    await client.flushdb()
    yield client
    await client.flushdb()
    await close_redis_client()


@pytest_asyncio.fixture(scope="function")
async def failing_redis():
    """Override get_redis_client with a fake whose lpush always fails.

    Used to prove that event ingestion survives a Redis outage: the API
    still returns 201 and the event stays ``pending`` in Postgres.
    """
    from redis.exceptions import RedisError

    class _FailingRedis:
        async def lpush(self, *args, **kwargs):
            raise RedisError("simulated redis failure")

        async def aclose(self):
            pass

    fake = _FailingRedis()
    app.dependency_overrides[get_redis_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_redis_client, None)
