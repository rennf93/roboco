"""Company-goals service — CRUD for the singleton company charter.

The charter (north star + objectives + constraints + operating policy) is a
single row. It is read by every agent (injected into the context_briefing) and
written only by the CEO via the API. Code here is layer-pure: business logic +
DB access, no HTTP concerns; the caller owns the transaction commit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from roboco.db.tables import CompanyGoalsTable
from roboco.services.base import BaseService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Canonical single-row marker — the charter is a singleton.
SINGLETON_ID = UUID("00000000-0000-0000-0000-000000000000")

_EMPTY: dict[str, Any] = {
    "north_star": "",
    "objectives": [],
    "constraints": [],
    "operating_policy": {},
    "updated_at": None,
    "updated_by": None,
}


class CompanyGoalsService(BaseService):
    """CRUD for the singleton ``company_goals`` row."""

    async def get(self) -> dict[str, Any]:
        """Return the charter as a primitive dict, or empty defaults if unset."""
        result = await self.session.execute(select(CompanyGoalsTable).limit(1))
        row = result.scalar_one_or_none()
        return self._to_dict(row) if row is not None else dict(_EMPTY)

    async def upsert(
        self, data: dict[str, Any], updated_by: UUID | None = None
    ) -> dict[str, Any]:
        """Create or update the singleton charter. Caller commits.

        Only the keys present in ``data`` are written (partial update); the rest
        keep their current values.
        """
        row = await self.session.get(CompanyGoalsTable, SINGLETON_ID)
        if row is None:
            row = CompanyGoalsTable(id=SINGLETON_ID)
            self.session.add(row)
        if "north_star" in data:
            row.north_star = data["north_star"]
        if "objectives" in data:
            row.objectives = data["objectives"]
        if "constraints" in data:
            row.constraints = data["constraints"]
        if "operating_policy" in data:
            row.operating_policy = data["operating_policy"]
        if updated_by is not None:
            row.updated_by = updated_by
        await self.session.flush()
        return self._to_dict(row)

    @staticmethod
    def _to_dict(row: CompanyGoalsTable) -> dict[str, Any]:
        return {
            "north_star": row.north_star,
            "objectives": row.objectives or [],
            "constraints": row.constraints or [],
            "operating_policy": row.operating_policy or {},
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "updated_by": str(row.updated_by) if row.updated_by else None,
        }


def get_company_goals_service(session: AsyncSession) -> CompanyGoalsService:
    """Construct a CompanyGoalsService bound to ``session``."""
    return CompanyGoalsService(session)
