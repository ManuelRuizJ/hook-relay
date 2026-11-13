from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event, EventStatus


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

    async def get_by_id_for_update(self, event_id: UUID) -> Event | None:
        """Load an event with a row lock held until the transaction ends.

        Used by state transitions triggered from the API, so two concurrent
        requests (e.g. a double-clicked replay) serialize instead of both
        passing the status check.
        """
        stmt = (
            select(Event)
            .where(Event.id == event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_status(
        self,
        status: EventStatus,
        *,
        endpoint_id: UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Event]:
        stmt = select(Event).where(Event.status == status)
        if endpoint_id is not None:
            stmt = stmt.where(Event.endpoint_id == endpoint_id)
        stmt = (
            stmt.order_by(Event.updated_at.desc(), Event.id)
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, event: Event) -> Event:
        await self.session.flush()
        await self.session.refresh(event)
        return event
