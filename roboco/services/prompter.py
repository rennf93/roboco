"""
PromptService — CRUD for PromptSession and PromptTurn.

Manages the lifecycle of LLM prompt sessions and their turn histories.
Each session holds metadata (status, optional system prompt, optional model)
and an ordered list of turns (role + content pairs).
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.base import get_db
from roboco.db.tables import PromptSessionTable, PromptTurnTable
from roboco.models.base import PromptSessionStatus
from roboco.services.base import BaseService, NotFoundError


class PromptService(BaseService):
    """Service for managing PromptSession and PromptTurn records."""

    service_name: ClassVar[str] = "prompt"

    # =========================================================================
    # SESSION CRUD
    # =========================================================================

    async def create_session(
        self,
        *,
        created_by: UUID | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> PromptSessionTable:
        """Create a new prompt session in DRAFT status.

        Args:
            created_by: Optional UUID of the agent/user creating the session.
            system_prompt: Optional system-level prompt to initialise the session.
            model: Optional model identifier (e.g. ``"claude-opus-4"``) to use.

        Returns:
            The newly created :class:`PromptSessionTable` row.
        """
        session_row = PromptSessionTable(
            created_by=created_by,
            status=PromptSessionStatus.DRAFT,
            system_prompt=system_prompt,
            model=model,
        )
        self.session.add(session_row)
        await self.session.commit()
        await self.session.refresh(session_row)

        self.log.info(
            "Created prompt session",
            session_id=str(session_row.id),
            created_by=str(created_by) if created_by else None,
        )
        return session_row

    async def get_session(self, session_id: UUID) -> PromptSessionTable:
        """Fetch a prompt session by primary key.

        Args:
            session_id: UUID of the session to retrieve.

        Returns:
            The :class:`PromptSessionTable` row.

        Raises:
            :class:`~roboco.services.base.NotFoundError`: If no session with
                ``session_id`` exists.
        """
        result = await self.session.execute(
            select(PromptSessionTable).where(PromptSessionTable.id == session_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("PromptSession", str(session_id))
        return row

    async def list_sessions(
        self,
        *,
        created_by: UUID | None = None,
        status: PromptSessionStatus | str | None = None,
    ) -> list[PromptSessionTable]:
        """List prompt sessions, optionally filtered.

        Args:
            created_by: Only return sessions whose ``created_by`` matches.
            status: Only return sessions in this status (accepts enum or
                lowercase string value).

        Returns:
            Ordered list of matching :class:`PromptSessionTable` rows
            (newest first by ``created_at``).
        """
        query = select(PromptSessionTable)

        if created_by is not None:
            query = query.where(PromptSessionTable.created_by == created_by)

        if status is not None:
            # Accept both PromptSessionStatus enum and raw string values.
            status_val = (
                status.value if isinstance(status, PromptSessionStatus) else status
            )
            query = query.where(PromptSessionTable.status == status_val)

        query = query.order_by(PromptSessionTable.created_at.desc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_session_status(
        self,
        session_id: UUID,
        status: PromptSessionStatus | str,
    ) -> PromptSessionTable:
        """Transition a session to a new status.

        Args:
            session_id: UUID of the session to update.
            status: Target status (``PromptSessionStatus`` enum or lowercase
                string value such as ``"launched"``).

        Returns:
            The updated :class:`PromptSessionTable` row.

        Raises:
            :class:`~roboco.services.base.NotFoundError`: If no session with
                ``session_id`` exists.
        """
        row = await self.get_session(session_id)
        new_status = (
            status if isinstance(status, PromptSessionStatus)
            else PromptSessionStatus(status)
        )
        row.status = new_status.value
        await self.session.commit()
        await self.session.refresh(row)

        self.log.info(
            "Updated prompt session status",
            session_id=str(session_id),
            new_status=new_status.value,
        )
        return row

    async def delete_session(self, session_id: UUID) -> bool:
        """Delete a prompt session and all its turns (via cascade).

        Args:
            session_id: UUID of the session to delete.

        Returns:
            ``True`` if the session existed and was deleted, ``False`` if it
            was not found.
        """
        result = await self.session.execute(
            select(PromptSessionTable).where(PromptSessionTable.id == session_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False

        await self.session.delete(row)
        await self.session.commit()

        self.log.info("Deleted prompt session", session_id=str(session_id))
        return True

    # =========================================================================
    # TURN CRUD
    # =========================================================================

    async def create_turn(
        self,
        session_id: UUID,
        role: str,
        content: str,
        *,
        turn_index: int = 0,
    ) -> PromptTurnTable:
        """Append a new turn to a prompt session.

        Args:
            session_id: UUID of the parent session.
            role: Speaker role, e.g. ``"user"``, ``"assistant"``, ``"system"``.
            content: Text content of the turn.
            turn_index: Ordering index within the session (default 0; callers
                are responsible for passing monotonically increasing values).

        Returns:
            The newly created :class:`PromptTurnTable` row.

        Raises:
            :class:`~roboco.services.base.NotFoundError`: If no session with
                ``session_id`` exists (FK guard).
        """
        # Validate parent exists before inserting to give a useful error.
        await self.get_session(session_id)

        turn_row = PromptTurnTable(
            session_id=session_id,
            role=role,
            content=content,
            turn_index=turn_index,
        )
        self.session.add(turn_row)
        await self.session.commit()
        await self.session.refresh(turn_row)

        self.log.info(
            "Created prompt turn",
            turn_id=str(turn_row.id),
            session_id=str(session_id),
            role=role,
        )
        return turn_row

    async def list_turns(self, session_id: UUID) -> list[PromptTurnTable]:
        """Return all turns for a session, ordered by ``turn_index`` ascending.

        Args:
            session_id: UUID of the parent session.

        Returns:
            Ordered list of :class:`PromptTurnTable` rows.
        """
        result = await self.session.execute(
            select(PromptTurnTable)
            .where(PromptTurnTable.session_id == session_id)
            .order_by(PromptTurnTable.turn_index)
        )
        return list(result.scalars().all())


# =============================================================================
# FASTAPI DEPENDENCY
# =============================================================================


def get_prompt_service(
    session: AsyncSession = Depends(get_db),
) -> PromptService:
    """FastAPI dependency that returns a :class:`PromptService` wired to the
    current request's async DB session.

    Usage::

        @router.get("/sessions/{session_id}")
        async def get_session(
            session_id: UUID,
            svc: PromptService = Depends(get_prompt_service),
        ) -> ...:
            return await svc.get_session(session_id)
    """
    return PromptService(session)
