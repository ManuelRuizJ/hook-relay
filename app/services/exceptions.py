from uuid import UUID


class WebhookNotFound(Exception):
    def __init__(self, endpoint_id: UUID) -> None:
        self.endpoint_id = endpoint_id
        super().__init__(f"Webhook endpoint {endpoint_id} not found")