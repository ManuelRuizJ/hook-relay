from uuid import UUID

from app.models.event import EventStatus


class WebhookNotFound(Exception):
    def __init__(self, endpoint_id: UUID) -> None:
        self.endpoint_id = endpoint_id
        super().__init__(f"Webhook endpoint {endpoint_id} not found")


class WebhookEndpointInactive(Exception):
    def __init__(self, endpoint_id: UUID) -> None:
        self.endpoint_id = endpoint_id
        super().__init__(f"Webhook endpoint {endpoint_id} is inactive")


class EventNotFound(Exception):
    def __init__(self, event_id: UUID) -> None:
        self.event_id = event_id
        super().__init__(f"Event {event_id} not found")

class InvalidEventTransition(Exception):
    """The requested operation is not allowed from the event's current status."""

    def __init__(self, event_id: UUID, status: EventStatus, action: str) -> None:
        self.event_id = event_id
        self.status = status
        self.action = action
        super().__init__(
            f"Cannot {action} event {event_id}: status is '{status.value}'"
        )
