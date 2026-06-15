"""PitchService — Board proposals and the CEO approve -> auto-provision flow.

On approval the service provisions one GitHub repo per target cell, registers
each as a Project (and a Product when the pitch spans multiple cells), and seeds
a single Main-PM delivery task. It reuses the existing Product / coordination-
task machinery wholesale, so the produced work flows through the normal delivery
lifecycle unchanged.

Partial-failure note: GitHub repo creation is an external side effect that
cannot be rolled back with the DB transaction. If provisioning fails partway,
the DB writes roll back (the route does not commit) but any repos already
created on GitHub remain; re-approval will collide on the repo name. The CEO
resolves such a rare case manually.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import PitchTable
from roboco.foundation.identity import Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.pitch import PitchCreate, PitchStatus
from roboco.models.product import ProductCellMapping, ProductCreate
from roboco.models.project import ProjectCreate
from roboco.models.task import TaskCreateRequest
from roboco.services.agent import get_agent_service
from roboco.services.base import (
    BaseService,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from roboco.services.github_provisioning import (
    ProvisioningDisabledError,
    get_github_provisioning_service,
)
from roboco.services.product import get_product_service
from roboco.services.project import get_project_service
from roboco.services.task import get_task_service
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.services.github_provisioning import GitHubProvisioningService

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
        notes: str,
        decided_by: UUID,
        *,
        provisioning: GitHubProvisioningService | None = None,
    ) -> PitchTable:
        """Provision repos + Projects (+ Product) and seed a Main-PM task."""
        pitch = await self._proposed_or_raise(pitch_id)
        prov = provisioning or get_github_provisioning_service()
        if not prov.enabled:
            raise ProvisioningDisabledError(
                "GitHub provisioning is not configured; cannot approve this "
                "pitch. Set ROBOCO_PROVISIONING_TOKEN and ROBOCO_PROVISIONING_ORG."
            )
        try:
            project_ids, cell_mappings = await self._provision_repos(
                pitch, decided_by, prov
            )
        finally:
            await prov.close()

        seed_project_id, seed_product_id = await self._register_topology(
            pitch, decided_by, project_ids, cell_mappings
        )
        seed_task_id = await self._seed_main_pm_task(
            pitch, decided_by, seed_project_id, seed_product_id
        )

        pitch.status = PitchStatus.PROVISIONED.value
        pitch.decided_by = decided_by
        pitch.decision_notes = notes
        pitch.provisioned_product_id = seed_product_id
        pitch.provisioned_project_ids = [str(pid) for pid in project_ids]
        pitch.seed_task_id = seed_task_id
        await self.session.flush()
        return pitch

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

    async def _provision_repos(
        self,
        pitch: PitchTable,
        decided_by: UUID,
        prov: GitHubProvisioningService,
    ) -> tuple[list[UUID], list[ProductCellMapping]]:
        cells = [Team(c) for c in pitch.target_cells]
        multi = len(cells) > 1
        project_svc = get_project_service(self.session)
        project_ids: list[UUID] = []
        cell_mappings: list[ProductCellMapping] = []
        for cell in cells:
            repo_name = f"{pitch.slug}-{cell.value}" if multi else pitch.slug
            repo = await prov.create_repo(
                repo_name, pitch.problem, private=settings.provisioning_repo_private
            )
            project = await project_svc.create(
                ProjectCreate(
                    name=repo_name,
                    slug=repo_name,
                    git_url=repo.clone_url,
                    assigned_cell=cell,
                    git_token=settings.provisioning_token or None,
                ),
                created_by=decided_by,
            )
            project_id = require_uuid(project.id)
            project_ids.append(project_id)
            cell_mappings.append(ProductCellMapping(team=cell, project_id=project_id))
        return project_ids, cell_mappings

    async def _register_topology(
        self,
        pitch: PitchTable,
        decided_by: UUID,
        project_ids: list[UUID],
        cell_mappings: list[ProductCellMapping],
    ) -> tuple[UUID | None, UUID | None]:
        """Return (seed_project_id, seed_product_id). Multi-cell -> a Product."""
        if len(cell_mappings) > 1:
            product = await get_product_service(self.session).create(
                ProductCreate(
                    name=pitch.title,
                    slug=pitch.slug,
                    description=pitch.proposed_solution[:_DESCRIPTION_CAP],
                    cells=cell_mappings,
                ),
                created_by=decided_by,
            )
            return None, require_uuid(product.id)
        return project_ids[0], None

    async def _seed_main_pm_task(
        self,
        pitch: PitchTable,
        decided_by: UUID,
        seed_project_id: UUID | None,
        seed_product_id: UUID | None,
    ) -> UUID:
        main_pm = await get_agent_service(self.session).get_by_slug("main-pm")
        if main_pm is None:
            raise ValidationError("main-pm agent not found; cannot seed delivery task")
        description = (
            f"{pitch.problem}\n\nProposed solution:\n{pitch.proposed_solution}\n\n"
            "(Originated from an approved Board pitch.)"
        )
        seed = await get_task_service(self.session).create(
            TaskCreateRequest(
                title=f"Build: {pitch.title}",
                description=description,
                acceptance_criteria=[
                    "Main PM scopes and decomposes the initiative across cells",
                    "Working software merged for each target cell",
                ],
                team=Team.MAIN_PM,
                created_by=decided_by,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.HIGH,
                assigned_to=require_uuid(main_pm.id),
                project_id=seed_project_id,
                product_id=seed_product_id,
                status=TaskStatus.PENDING,
                source="pitch",
                confirmed_by_human=True,
            )
        )
        return require_uuid(seed.id)


def get_pitch_service(session: AsyncSession) -> PitchService:
    """Construct a PitchService bound to ``session``."""
    return PitchService(session)
