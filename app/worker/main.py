import asyncio
import logging
import signal

from app.core.queue import WEBHOOK_DELIVERY_QUEUE
from app.core.redis import close_redis_client, get_redis_client

logger = logging.getLogger(__name__)

# Timeout for BRPOP so the loop can check the shutdown flag every few
# seconds instead of blocking forever. With `0` (infinite) a SIGTERM
# would only be noticed after the next message arrives.
BRPOP_TIMEOUT_SECONDS = 5


async def run_worker(shutdown: asyncio.Event) -> None:
    redis = get_redis_client()
    logger.info("Worker started; waiting on %s", WEBHOOK_DELIVERY_QUEUE)
    try:
        while not shutdown.is_set():
            result = await redis.brpop(
                [WEBHOOK_DELIVERY_QUEUE], timeout=BRPOP_TIMEOUT_SECONDS
            )
            if result is None:
                continue
            _queue, event_id = result
            # Delivery is implemented in the next commit. For now, prove
            # the loop consumes the queue and logs what it picked up.
            logger.info("Picked up event %s", event_id)
    finally:
        logger.info("Worker shutting down")
        await close_redis_client()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    shutdown = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    try:
        loop.run_until_complete(run_worker(shutdown))
    finally:
        loop.close()


if __name__ == "__main__":
    main()