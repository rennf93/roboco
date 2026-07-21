"""PitchService — Board proposals and CEO decision tracking.

Repository auto-provisioning was removed with the GitHub App integration; the
approve path now raises a clear error so the CEO knows provisioning is
unavailable. The service keeps CRUD + reject flows unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from roboco.db.tables import PitchTable
from roboco.foundation.identity import Team
from roboco.models.pitch import PitchCreate, PitchStatus
from roboco.services.base import (
    BaseService,
    ConflictError,
    NotFoundError,
    ValidationError,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class ProvisioningDisabledError(ValidationError):
    """Raised when a pitch is approved but repository provisioning is unavailable."""


_DESCRIPTION_CAP = 500


class PitchService(BaseService):
    """CRUD + approve/reject for Board pitches."""

    service_name = "pitch"

    async def get(self, pitch_id: UUID) -> PitchTable | None:
        result = await self.session.execute(
            select(PitchTable).where(PitchTable.id == pitch_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> PitchTable | None:
        result = await self.session.execute(
            select(PitchTable).where(PitchTable.slug == slug)
        )
        return result.scalar_one_or_none()

    async def list_pitches(self, status: PitchStatus | None = None) -> list[PitchTable]:
        stmt = select(PitchTable).order_by(PitchTable.created_at.desc())
        if status is not None:
            stmt = stmt.where(PitchTable.status == status.value)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, data: PitchCreate, created_by: UUID) -> PitchTable:
        if await self.get_by_slug(data.slug):
            raise ConflictError(
                f"Pitch with slug '{data.slug}' already exists",
                resource_type="pitch",
            )
        pitch = PitchTable(
            title=data.title,
            slug=data.slug,
            problem=data.problem,
            proposed_solution=data.proposed_solution,
            target_cells=[
                c.value if isinstance(c, Team) else str(c) for c in data.target_cells
            ],
            status=PitchStatus.PROPOSED.value,
            created_by=created_by,
        )
        self.session.add(pitch)
        await self.session.flush()
        return pitch

    async def reject(self, pitch_id: UUID, notes: str, decided_by: UUID) -> PitchTable:
        pitch = await self._proposed_or_raise(pitch_id)
        pitch.status = PitchStatus.REJECTED.value
        pitch.decided_by = decided_by
        pitch.decision_notes = notes
        await self.session.flush()
        return pitch

    async def approve(
        self,
        pitch_id: UUID,
        _notes: str,
        _decided_by: UUID,
    ) -> PitchTable:
        """Approve a pitch.

        Repository auto-provisioning was removed with the GitHub App integration;
        approval now raises a clear error so the CEO knows provisioning is
        unavailable.
        """
        await self._proposed_or_raise(pitch_id)
        raise ProvisioningDisabledError(
            "GitHub repository provisioning is not available; pitch approval "
            "cannot create repos or seed delivery tasks."
        )

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    async def _proposed_or_raise(self, pitch_id: UUID) -> PitchTable:
        pitch = await self.get(pitch_id)
        if pitch is None:
            raise NotFoundError("pitch", str(pitch_id))
        if pitch.status != PitchStatus.PROPOSED.value:
            raise ConflictError(
                f"Pitch is '{pitch.status}', not 'proposed'; cannot decide it again",
                resource_type="pitch",
            )
        return pitch


def get_pitch_service(session: AsyncSession) -> PitchService:
    """Construct a PitchService bound to ``session``."""
    return PitchService(session)
