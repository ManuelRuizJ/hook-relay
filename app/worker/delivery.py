import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass

import httpx

from app.models.event import Event
from app.models.webhook_endpoint import WebhookEndpoint

logger = logging.getLogger(__name__)

# A slow receiver is a broken receiver. Keep the worker responsive.
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
USER_AGENT = "HookRelay/0.4"


@dataclass
class DeliveryResult:
    success: bool
    status_code: int | None
    error: str | None


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    """HMAC-SHA256 over '{timestamp}.{body}'.

    The timestamp is part of the signed message so a receiver can reject
    old deliveries (replay protection). This is the same contract used by
    GitHub and Stripe webhooks.
    """
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


async def deliver_event(
    client: httpx.AsyncClient, event: Event, endpoint: WebhookEndpoint
) -> DeliveryResult:
    # Canonical JSON: no whitespace, stable key order not required because
    # we sign the exact bytes we send.
    body = json.dumps(
        {
            "id": str(event.id),
            "event_type": event.event_type,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        },
        separators=(",", ":"),
    ).encode()

    timestamp = int(time.time())
    signature = sign_payload(endpoint.secret, timestamp, body)

    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-HookRelay-Id": str(event.id),
        "X-HookRelay-Event": event.event_type,
        "X-HookRelay-Timestamp": str(timestamp),
        "X-HookRelay-Signature": f"sha256={signature}",
    }

    try:
        resp = await client.post(endpoint.url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Delivery failed for event %s: %s", event.id, exc)
        return DeliveryResult(success=False, status_code=None, error=str(exc))

    if 200 <= resp.status_code < 300:
        logger.info(
            "Delivered event %s to %s (HTTP %s)",
            event.id,
            endpoint.url,
            resp.status_code,
        )
        return DeliveryResult(success=True, status_code=resp.status_code, error=None)

    logger.warning(
        "Delivery rejected for event %s: HTTP %s", event.id, resp.status_code
    )
    return DeliveryResult(
        success=False,
        status_code=resp.status_code,
        error=f"HTTP {resp.status_code}: {resp.text[:200]}",
    )