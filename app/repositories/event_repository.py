from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, event: Event) -> Event:
        self.session.add(event)
        await self.session.flush()
        await self.session.refresh(event)
        return event

    async def get_by_id(self, event_id: UUID) -> Event | None:
        return await self.session.get(Event, event_id)

    async def update(self, event: Event) -> Event:
        await self.session.flush()
        await self.session.refresh(event)
        return event