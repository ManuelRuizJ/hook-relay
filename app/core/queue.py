"""Names of the Redis queues used by HookRelay.

Keep them here so the API (producer) and the worker (consumer) cannot drift.
"""

WEBHOOK_DELIVERY_QUEUE = "webhooks:queue"
WEBHOOK_DEAD_LETTER_QUEUE = "webhooks:dlq"