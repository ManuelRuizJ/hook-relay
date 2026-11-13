"""Prometheus metrics owned by the API process.

Request metrics are recorded by a middleware. Queue depth and events by
status are read from Redis and Postgres at scrape time, so they always
match the source of truth instead of drifting like an in-process counter.
"""

import logging
import time

from prometheus_client import Counter, Gauge, Histogram
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.queue import WEBHOOK_DELAYED_QUEUE, WEBHOOK_DELIVERY_QUEUE
from app.models.event import Event, EventStatus

logger = logging.getLogger(__name__)

# Label for requests that matched no route. Using the raw path instead
# would let any client create unbounded label values (cardinality blowup).
UNMATCHED_ROUTE = "unmatched"

HTTP_REQUESTS = Counter(
    "hookrelay_http_requests_total",
    "HTTP requests handled by the API.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "hookrelay_http_request_duration_seconds",
    "Time spent handling HTTP requests.",
    ["method", "route"],
)
QUEUE_DEPTH = Gauge(
    "hookrelay_queue_depth",
    "Events waiting in each Redis queue (delivery list, delayed ZSET).",
    ["queue"],
)
EVENTS = Gauge(
    "hookrelay_events",
    "Events in Postgres by status. status='failed' is the dead-letter queue.",
    ["status"],
)


class PrometheusMiddleware:
    """Count and time every HTTP request, labelled by route template."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_code = 500  # if the app raises before responding

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # FastAPI stores the matched route in the scope; its path is the
            # template ("/api/v1/events/{event_id}"), not the concrete URL.
            route = getattr(scope.get("route"), "path", UNMATCHED_ROUTE)
            method = scope["method"]
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_LATENCY.labels(method, route).observe(time.perf_counter() - started)


async def refresh_state_gauges(session: AsyncSession, redis: Redis) -> None:
    """Update queue and status gauges right before a scrape.

    A failing backend leaves its gauges at the last known value and logs a
    warning; the scrape itself still succeeds with the other metrics.
    """
    try:
        QUEUE_DEPTH.labels("delivery").set(await redis.llen(WEBHOOK_DELIVERY_QUEUE))
        QUEUE_DEPTH.labels("delayed").set(await redis.zcard(WEBHOOK_DELAYED_QUEUE))
    except (RedisError, OSError):
        logger.warning("Could not read queue depth for metrics", exc_info=True)

    try:
        rows = await session.execute(
            select(Event.status, func.count()).group_by(Event.status)
        )
        counts = dict(rows.all())
    except (SQLAlchemyError, OSError):
        logger.warning("Could not count events for metrics", exc_info=True)
        return
    for status in EventStatus:
        EVENTS.labels(status.value).set(counts.get(status, 0))
