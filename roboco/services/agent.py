"""
Agent Service

Lookup + filter helpers over AgentTable. Keeps route modules and other
services free of raw AgentTable queries.
"""

from typing import ClassVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import AgentTable
from roboco.models import AgentRole, Team
from roboco.services.base import BaseService, NotFoundError


class AgentService(BaseService):
    """Thin read-side service for agent records."""

    service_name: ClassVar[str] = "agent"

    async def list_agents(
        self,
        *,
        slug: str | None = None,
        role: AgentRole | None = None,
        team: Team | None = None,
    ) -> list[AgentTable]:
        """Return agents filtered by optional slug / role / team."""
        query = select(AgentTable)
        if slug:
            query = query.where(AgentTable.slug == slug)
        if role:
            query = query.where(AgentTable.role == role)
        if team:
            query = query.where(AgentTable.team == team)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_uuid(self, agent_id: UUID) -> AgentTable | None:
        """Fetch an agent by primary key."""
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> AgentTable | None:
        """Fetch an agent by slug."""
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_by_uuid_or_slug_or_raise(self, identifier: str) -> AgentTable:
        """Fetch by UUID-string first, then slug; raise NotFoundError on miss.

        Mirrors the old route-inline "try UUID parse, else slug" pattern so
        callers only deal with agent-found / raise.
        """
        try:
            agent = await self.get_by_uuid(UUID(identifier))
        except ValueError:
            agent = await self.get_by_slug(identifier)

        if agent is None:
            raise NotFoundError(resource_type="Agent", resource_id=identifier)
        return agent


def get_agent_service(session: AsyncSession) -> AgentService:
    """Factory function to create an AgentService instance."""
    return AgentService(session)
