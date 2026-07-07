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

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import delete, select

from roboco.conventions.scan import derive_from_scan, render_yaml
from roboco.db.tables import (
    ProjectConventionFindingTable,
    ProjectConventionsCacheTable,
)
from roboco.foundation.policy.conventions.effective_map import effective_map
from roboco.foundation.policy.conventions.models import (
    ConventionsParseError,
    ConventionsStandard,
)
from roboco.services.base import BaseService
from roboco.services.git import CONVENTIONS_SCAFFOLD_BRANCH, get_git_service

if TYPE_CHECKING:
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable

_SCAFFOLD_BRANCH = CONVENTIONS_SCAFFOLD_BRANCH
_AMBIENT_CHAR_CAP = 2000


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


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Whether ``exc`` is a UNIQUE constraint violation (SQLSTATE 23505).

    asyncpg exposes ``sqlstate`` on the wrapped error; psycopg exposes
    ``pgcode`` / ``sqlstate``. The class-name fallback covers a driver whose
    orig lacks a code attribute. Anything else (FK / NOT NULL / check) is a
    real bug, not a benign concurrent duplicate (#130).
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if code == "23505":
        return True
    return "UniqueViolation" in type(orig).__name__


class ConventionsService(BaseService):
    """Cache, render, scaffold, and restore a project's conventions standard."""

    async def get_map(
        self, project: ProjectTable, *, workspace: Path | None = None
    ) -> ConventionsStandard:
        """Return the effective standard for ``project`` at its current HEAD."""
        pid = self._pid(project)
        # _resolve runs `git rev-parse` and _read_committed_standard/_derive
        # walk the filesystem + parse yaml — all sync I/O. Offload to a thread
        # so the shared API event loop stays responsive during conventions reads
        # (reachable from GET /api/projects/{id}/conventions and the spawn path).
        root, sha = await asyncio.to_thread(self._resolve, project, workspace)
        if root is not None:
            project.workspace_path = str(root)
            if sha is not None:
                project.head_commit = sha
        head = sha or self._head_sha(project)
        cached = await self._cache_get(pid, head)
        # A cached ``degraded`` row is not trusted: a degraded file may have
        # been repaired in place at the same (stale) head key, and serving the
        # cached last-good map would hide the repair. Re-derive instead (#132).
        if cached is not None and cached.status != "degraded":
            return ConventionsStandard.model_validate(cached.effective_map)

        file_standard, status = await asyncio.to_thread(
            self._read_committed_standard, root
        )
        if status == "degraded":
            last_good = await self._latest_ok_map(pid)
            if last_good is not None:
                # Not cached: a degraded row is unstable (the file may be
                # repaired in place), so never pin it — re-derive next call.
                return last_good

        mapping = effective_map(
            await asyncio.to_thread(self._derive, root), file_standard
        )
        if status != "degraded":
            await self._cache_put(pid, head, mapping, status)
        return mapping

    async def baseline_constraints(
        self, project: ProjectTable, *, workspace: Path | None = None
    ) -> list[str]:
        """Render the project's block rules + module boundaries as constraints."""
        mapping = await self.get_map(project, workspace=workspace)
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

    async def render_ambient_block(
        self, project: ProjectTable, *, workspace: Path | None = None
    ) -> str:
        """Render a compact, bounded 'Architectural Standard' prompt block.

        Only modules that actually forbid a kind are listed — an unconstrained
        module adds no signal. If the module list would exceed the budget it is
        truncated at a line boundary with a ``+N more`` pointer (never cut
        mid-line), and the block-level rule summary is always kept.
        """
        mapping = await self.get_map(project, workspace=workspace)
        header = [
            "## Architectural Standard",
            "Place each definition in the module that owns its kind:",
        ]
        constrained = [m for m in mapping.modules if m.forbidden]
        block = sorted(n for n, r in mapping.rules.items() if r.level == "block")
        footer = ["Block-level rules: " + ", ".join(block) + "."] if block else []

        # Reserve room for the fixed header/footer plus a possible '+N more' line
        # so the budget is spent on whole module lines.
        reserve = len("\n".join(header + footer)) + 80
        budget = max(0, _AMBIENT_CHAR_CAP - reserve)
        kept: list[str] = []
        used = 0
        for module in constrained:
            line = (
                f"- `{module.path}`: {module.purpose} "
                f"— forbidden: {', '.join(module.forbidden)}"
            )
            if used + len(line) + 1 > budget:
                break
            kept.append(line)
            used += len(line) + 1
        if len(kept) < len(constrained):
            extra = len(constrained) - len(kept)
            plural = "s" if extra != 1 else ""
            kept.append(
                f"- (+{extra} more module{plural} — see .roboco/conventions.yml)"
            )
        return "\n".join(header + kept + footer)

    async def scaffold(
        self, project: ProjectTable, *, workspace: Path | None = None
    ) -> ScaffoldResult:
        """Open a PR adding the auto-scaffolded ``.roboco/conventions.yml``."""
        mapping = await self.get_map(project, workspace=workspace)
        return await self._publish(
            project, render_yaml(mapping), restore=False, workspace=workspace
        )

    async def restore(
        self, project: ProjectTable, *, workspace: Path | None = None
    ) -> ScaffoldResult:
        """Open a PR re-committing the file from the last-good map (or a scan)."""
        last_good = await self._latest_ok_map(self._pid(project))
        if last_good is not None:
            mapping = last_good
        else:
            root, _sha = await asyncio.to_thread(self._resolve, project, workspace)
            if root is not None:
                project.workspace_path = str(root)
                if _sha is not None:
                    project.head_commit = _sha
            mapping = await asyncio.to_thread(self._derive, root)
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

    async def health(
        self, project: ProjectTable, *, workspace: Path | None = None
    ) -> ConventionsHealth:
        """Report the standard's status at HEAD + the last-good commit SHA.

        The status is the LIVE file state, not a cached row: a cached
        ``degraded`` can hide an in-place repair at the same (stale) head key
        (#132). The map scan is expensive (cached); a single file parse is
        cheap (re-read). ``unknown`` is reserved for a project with no
        resolvable workspace at all.
        """
        pid = self._pid(project)
        root, sha = await asyncio.to_thread(self._resolve, project, workspace)
        if root is not None:
            project.workspace_path = str(root)
            if sha is not None:
                project.head_commit = sha
        head = sha or self._head_sha(project)
        if root is None:
            status = "unknown"
        else:
            _file_standard, status = await asyncio.to_thread(
                self._read_committed_standard, root
            )
        last_ok = await self._latest_ok_row(pid)
        return ConventionsHealth(
            status=status,
            head_sha=head,
            last_ok_sha=last_ok.commit_sha if last_ok is not None else None,
        )

    async def record_findings(
        self, project_id: UUID, task_id: UUID, findings: list[dict[str, Any]]
    ) -> None:
        """Replace a task's recorded findings with the latest set. Caller commits."""
        await self.session.execute(
            delete(ProjectConventionFindingTable).where(
                ProjectConventionFindingTable.project_id == project_id,
                ProjectConventionFindingTable.task_id == task_id,
            )
        )
        for finding in findings:
            if not finding.get("file") or not finding.get("rule"):
                continue
            self.session.add(
                ProjectConventionFindingTable(
                    project_id=project_id,
                    task_id=task_id,
                    file=str(finding.get("file", "")),
                    line=int(finding.get("line", 0)),
                    rule=str(finding.get("rule", "")),
                    level=str(finding.get("level", "")),
                    kind=finding.get("kind"),
                    message=str(finding.get("message", "")),
                )
            )
        await self.session.flush()

    async def recent_findings(
        self, project_id: UUID, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Recent findings across the project, newest first (for the panel feed)."""
        result = await self.session.execute(
            select(ProjectConventionFindingTable)
            .where(ProjectConventionFindingTable.project_id == project_id)
            .order_by(ProjectConventionFindingTable.detected_at.desc())
            .limit(limit)
        )
        return [
            {
                "file": row.file,
                "line": row.line,
                "rule": row.rule,
                "level": row.level,
                "kind": row.kind,
                "message": row.message,
                "task_id": str(row.task_id) if row.task_id is not None else None,
                "detected_at": row.detected_at.isoformat(),
            }
            for row in result.scalars().all()
        ]

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

    async def resolve_workspace(self, project: ProjectTable) -> Path | None:
        """Ensure (clone / refresh) the project's read clone; None if unavailable."""
        from roboco.services.workspace import get_workspace_service

        try:
            return await get_workspace_service(self.session).ensure_read_clone(
                project.slug, force=True
            )
        except Exception as exc:
            self.log.warning(
                "conventions: read-clone unavailable; falling back to workspace_path",
                project=getattr(project, "slug", None),
                error=str(exc),
            )
            return None

    def _resolve(
        self, project: ProjectTable, workspace: Path | None
    ) -> tuple[Path | None, str | None]:
        """Resolve the repo root to read the standard from, plus its raw HEAD sha.

        Returns ``(root, sha_raw)`` with no side effects: the caller mutates
        ``project.workspace_path`` / ``project.head_commit`` on the event loop
        (mutating ORM state off the event loop is unsafe under SQLAlchemy 2
        async). ``sha_raw`` is the real rev-parse, or None for a non-git legacy
        path — the caller computes the ``sha or self._head_sha(project)``
        fallback for cache key / health head_sha.
        """
        root: Path | None = None
        if workspace is not None and Path(workspace).exists():
            root = Path(workspace)
        else:
            root = self._workspace_root(project)
        if root is None:
            return None, None
        sha = self._head_sha_at(root)
        return root, sha

    @staticmethod
    def _head_sha_at(root: Path) -> str | None:
        """The clone's HEAD sha, or None if ``root`` is not a readable git repo."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

    def _derive(self, root: Path | None) -> ConventionsStandard:
        return derive_from_scan(root) if root is not None else ConventionsStandard()

    def _read_committed_standard(
        self, root: Path | None
    ) -> tuple[ConventionsStandard | None, str]:
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
        """Persist the effective map for ``(project_id, commit_sha)``.

        Runs the INSERT in a savepoint so a concurrent duplicate (two task
        creates for the same project/HEAD racing to populate the cache) rolls
        back ONLY the savepoint on IntegrityError — the loser's insert is
        dropped, the winner's row satisfies the next ``_cache_get``, and the
        shared session is not poisoned. A bare ``add`` + ``flush`` here would
        leave the session in error state and crash the rest of task creation
        (the task-create transaction rides the same session) — F042.
        """
        from sqlalchemy.exc import IntegrityError

        try:
            async with self.session.begin_nested():
                self.session.add(
                    ProjectConventionsCacheTable(
                        project_id=project_id,
                        commit_sha=commit_sha,
                        effective_map=mapping.model_dump(mode="json"),
                        status=status,
                    )
                )
        except IntegrityError as exc:
            # Only a UNIQUE violation (23505) is the benign concurrent-duplicate
            # case the savepoint is for. A FK / NOT NULL / check violation is a
            # real bug — silently misattributing it as "concurrent put" would
            # hide the failure (#130), so log-error and re-raise instead. The
            # savepoint was rolled back (only the failed insert), leaving the
            # outer task-create transaction usable.
            if not _is_unique_violation(exc):
                self.log.error(
                    "conventions cache insert failed (non-unique integrity error)",
                    project_id=str(project_id),
                    commit_sha=commit_sha,
                    error=str(exc),
                )
                raise
            self.log.debug(
                "conventions cache row already present (concurrent put)",
                project_id=str(project_id),
                commit_sha=commit_sha,
            )

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
