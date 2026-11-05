import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models import Base
from app.core.queue import WEBHOOK_DEAD_LETTER_QUEUE, WEBHOOK_DELIVERY_QUEUE
from app.core.redis import close_redis_client, get_redis_client

TEST_DB_URL = (
    settings.database_url.rsplit("/", 1)[0]
    + f"/{settings.postgres_db}_test"
)

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
    """Real Redis client with queue cleanup, isolated per test."""
    # Each test gets its own event loop; reset the module-level singleton
    # so the client is bound to the current loop.
    await close_redis_client()
    client = get_redis_client()
    await client.delete(WEBHOOK_DELIVERY_QUEUE, WEBHOOK_DEAD_LETTER_QUEUE)
    yield client
    await client.delete(WEBHOOK_DELIVERY_QUEUE, WEBHOOK_DEAD_LETTER_QUEUE)
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