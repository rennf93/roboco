"""ConventionsService — the per-project architectural-conventions standard.

Builds the *effective* conventions map (auto-derived defaults overlaid by the
committed ``.roboco/conventions.yml``), caches it per ``(project, HEAD sha)``,
and renders it for the two carriers (per-task baseline constraints + the
ambient prompt block). Also scaffolds / restores the committed file via a PR.

Resilience: a missing file degrades to auto-derived defaults; an unparseable
file falls back to the last-good cached map (never silently off). DB writes +
git side effects live here (service layer); the schema + classifiers it builds
on are pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from roboco.conventions.scan import derive_from_scan, render_yaml
from roboco.db.tables import ProjectConventionsCacheTable
from roboco.foundation.policy.conventions.effective_map import effective_map
from roboco.foundation.policy.conventions.models import (
    ConventionsParseError,
    ConventionsStandard,
)
from roboco.services.base import BaseService
from roboco.services.git import CONVENTIONS_SCAFFOLD_BRANCH, get_git_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable

_SCAFFOLD_BRANCH = CONVENTIONS_SCAFFOLD_BRANCH
_AMBIENT_CHAR_CAP = 1200


@dataclass(frozen=True)
class ScaffoldResult:
    """Outcome of a scaffold / restore: the branch + PR (if one was opened)."""

    pr_number: int | None
    branch: str
    created: bool


@dataclass(frozen=True)
class ConventionsHealth:
    """Health of a project's standard: current status + last-good SHA."""

    status: str
    head_sha: str
    last_ok_sha: str | None


class ConventionsService(BaseService):
    """Cache, render, scaffold, and restore a project's conventions standard."""

    async def get_map(self, project: ProjectTable) -> ConventionsStandard:
        """Return the effective standard for ``project`` at its current HEAD."""
        pid = self._pid(project)
        head = self._head_sha(project)
        cached = await self._cache_get(pid, head)
        if cached is not None:
            return ConventionsStandard.model_validate(cached.effective_map)

        file_standard, status = self._read_committed_standard(project)
        if status == "degraded":
            last_good = await self._latest_ok_map(pid)
            if last_good is not None:
                await self._cache_put(pid, head, last_good, status)
                return last_good

        mapping = effective_map(self._derive(project), file_standard)
        await self._cache_put(pid, head, mapping, status)
        return mapping

    async def baseline_constraints(self, project: ProjectTable) -> list[str]:
        """Render the project's block rules + module boundaries as constraints."""
        mapping = await self.get_map(project)
        constraints = [
            f"Convention (block): {name.replace('_', ' ')}"
            for name, rule in mapping.rules.items()
            if rule.level == "block"
        ]
        constraints += [
            f"Place code per the map — {m.path} is for {m.purpose} "
            f"(no {', '.join(m.forbidden)} here)"
            for m in mapping.modules
            if m.forbidden
        ]
        return constraints

    async def render_ambient_block(self, project: ProjectTable) -> str:
        """Render a compact, bounded 'Architectural Standard' prompt block."""
        mapping = await self.get_map(project)
        lines = [
            "## Architectural Standard",
            "Place each definition in the module that owns its kind:",
        ]
        for module in mapping.modules:
            suffix = (
                f" — forbidden: {', '.join(module.forbidden)}"
                if module.forbidden
                else ""
            )
            lines.append(f"- `{module.path}`: {module.purpose}{suffix}")
        block = sorted(n for n, r in mapping.rules.items() if r.level == "block")
        if block:
            lines.append("Block-level rules: " + ", ".join(block) + ".")
        text = "\n".join(lines)
        if len(text) > _AMBIENT_CHAR_CAP:
            return text[: _AMBIENT_CHAR_CAP - 1].rstrip() + "…"
        return text

    async def scaffold(
        self, project: ProjectTable, *, workspace: Path | None = None
    ) -> ScaffoldResult:
        """Open a PR adding the auto-scaffolded ``.roboco/conventions.yml``."""
        mapping = await self.get_map(project)
        return await self._publish(
            project, render_yaml(mapping), restore=False, workspace=workspace
        )

    async def restore(
        self, project: ProjectTable, *, workspace: Path | None = None
    ) -> ScaffoldResult:
        """Open a PR re-committing the file from the last-good map (or a scan)."""
        last_good = await self._latest_ok_map(self._pid(project))
        mapping = last_good if last_good is not None else self._derive(project)
        return await self._publish(
            project, render_yaml(mapping), restore=True, workspace=workspace
        )

    async def commit_standard(
        self,
        project: ProjectTable,
        standard: ConventionsStandard,
        *,
        workspace: Path | None = None,
    ) -> ScaffoldResult:
        """Open a PR committing an externally-edited standard (panel save)."""
        return await self._publish(
            project, render_yaml(standard), restore=False, workspace=workspace
        )

    async def health(self, project: ProjectTable) -> ConventionsHealth:
        """Report the standard's status at HEAD + the last-good commit SHA."""
        pid = self._pid(project)
        head = self._head_sha(project)
        current = await self._cache_get(pid, head)
        last_ok = await self._latest_ok_row(pid)
        return ConventionsHealth(
            status=current.status if current is not None else "unknown",
            head_sha=head,
            last_ok_sha=last_ok.commit_sha if last_ok is not None else None,
        )

    # -- internals ---------------------------------------------------------- #

    @staticmethod
    def _pid(project: ProjectTable) -> UUID:
        # ProjectTable.id is typed as the SQLAlchemy UUID column; normalize to a
        # plain uuid.UUID for the cache-row helpers.
        return UUID(str(project.id))

    @staticmethod
    def _head_sha(project: ProjectTable) -> str:
        return project.head_commit or "HEAD"

    @staticmethod
    def _workspace_root(project: ProjectTable) -> Path | None:
        if not project.workspace_path:
            return None
        path = Path(project.workspace_path)
        return path if path.exists() else None

    def _derive(self, project: ProjectTable) -> ConventionsStandard:
        root = self._workspace_root(project)
        return derive_from_scan(root) if root is not None else ConventionsStandard()

    def _read_committed_standard(
        self, project: ProjectTable
    ) -> tuple[ConventionsStandard | None, str]:
        root = self._workspace_root(project)
        if root is None:
            return None, "missing"
        path = root / ".roboco" / "conventions.yml"
        if not path.is_file():
            return None, "missing"
        try:
            text = path.read_text()
        except OSError:
            return None, "missing"
        try:
            return ConventionsStandard.parse_yaml(text), "ok"
        except ConventionsParseError:
            return None, "degraded"

    async def _publish(
        self,
        project: ProjectTable,
        content: str,
        *,
        restore: bool,
        workspace: Path | None = None,
    ) -> ScaffoldResult:
        action = "restore" if restore else "scaffold"
        title = f"chore(conventions): {action} .roboco/conventions.yml"
        body = (
            "Auto-generated by RoboCo's architectural-conventions standard. "
            "This file is repo-canonical — review, edit, or close as you like."
        )
        git = get_git_service(self.session)
        result = await git.open_conventions_pr(
            project.slug,
            content=content,
            title=title,
            body=body,
            workspace=workspace,
        )
        if result is None:
            return ScaffoldResult(
                pr_number=None, branch=_SCAFFOLD_BRANCH, created=False
            )
        return ScaffoldResult(
            pr_number=result.get("pr_number"),
            branch=result.get("branch", _SCAFFOLD_BRANCH),
            created=True,
        )

    async def _cache_get(
        self, project_id: UUID, commit_sha: str
    ) -> ProjectConventionsCacheTable | None:
        result = await self.session.execute(
            select(ProjectConventionsCacheTable).where(
                ProjectConventionsCacheTable.project_id == project_id,
                ProjectConventionsCacheTable.commit_sha == commit_sha,
            )
        )
        return result.scalar_one_or_none()

    async def _cache_put(
        self,
        project_id: UUID,
        commit_sha: str,
        mapping: ConventionsStandard,
        status: str,
    ) -> None:
        self.session.add(
            ProjectConventionsCacheTable(
                project_id=project_id,
                commit_sha=commit_sha,
                effective_map=mapping.model_dump(mode="json"),
                status=status,
            )
        )
        await self.session.flush()

    async def _latest_ok_row(
        self, project_id: UUID
    ) -> ProjectConventionsCacheTable | None:
        result = await self.session.execute(
            select(ProjectConventionsCacheTable)
            .where(
                ProjectConventionsCacheTable.project_id == project_id,
                ProjectConventionsCacheTable.status == "ok",
            )
            .order_by(ProjectConventionsCacheTable.derived_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _latest_ok_map(self, project_id: UUID) -> ConventionsStandard | None:
        row = await self._latest_ok_row(project_id)
        if row is None:
            return None
        return ConventionsStandard.model_validate(row.effective_map)


def get_conventions_service(session: AsyncSession) -> ConventionsService:
    """Construct a ConventionsService bound to ``session``."""
    return ConventionsService(session)
