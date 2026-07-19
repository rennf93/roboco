"""
Project Service

Provides CRUD operations and git workflow management for projects.
Projects represent git repositories that agents work on.
"""

from typing import ClassVar
from typing import cast as typing_cast
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.config import settings
from roboco.db.tables import ProjectTable, TaskTable
from roboco.exceptions import ValidationError
from roboco.foundation.policy.forge import detect_provider, validate_project_forge
from roboco.models.base import TaskStatus, Team
from roboco.models.project import ProjectCreate, ProjectUpdate
from roboco.services.base import BaseService, ConflictError, NotFoundError
from roboco.services.forge import register_project_forge
from roboco.utils.crypto import EncryptionError, decrypt_token, encrypt_token

# Statuses that are NOT active progress: completed (done), cancelled
# (abandoned), blocked (its own bucket). Everything else counts as active.
_INACTIVE_STATUSES = (
    TaskStatus.COMPLETED,
    TaskStatus.CANCELLED,
    TaskStatus.BLOCKED,
)


class ProjectService(BaseService):
    """
    Service for managing projects (git repositories).

    Provides:
    - CRUD operations
    - Slug-based lookups
    - Project listing by cell/team
    - Workspace path management
    """

    service_name: ClassVar[str] = "project"

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    def _assert_git_url_allowed(self, git_url: str | None) -> None:
        """Reject a project repo URL that matches a protected (denylisted) repo.

        Keeps agent commits/merges out of a repository that must not receive
        them — e.g. the roboco source repo during a smoke run.
        """
        if not git_url:
            return
        for protected in settings.protected_git_urls:
            if protected and protected in git_url:
                raise ValidationError(
                    "Project git_url may not point at a protected repository "
                    f"('{protected}').",
                    field="git_url",
                )

    def _assert_forge_supported(
        self, git_url: str | None, git_provider: str | None
    ) -> None:
        """Reject an unsupported/unrecognized forge (Phase 0 of the forge-
        providers spec) — turns today's silent multi-step-deep GitLab/Gitea
        failure into a loud registration-time error."""
        error = validate_project_forge(git_url, git_provider)
        if error:
            raise ValidationError(
                error, field="git_provider" if git_provider is not None else "git_url"
            )

    async def create(
        self,
        data: ProjectCreate,
        created_by: UUID,
    ) -> ProjectTable:
        """
        Create/register a new project.

        Args:
            data: Project creation data
            created_by: PM who is registering the project

        Returns:
            The created project

        Raises:
            ConflictError: If project with same slug already exists
        """
        # Check for duplicate slug
        existing = await self.get_by_slug(data.slug)
        if existing:
            raise ConflictError(
                f"Project with slug '{data.slug}' already exists",
                resource_type="project",
            )

        self._assert_git_url_allowed(data.git_url)
        self._assert_forge_supported(data.git_url, data.git_provider)

        # Null + a github.com git_url auto-stamps "github" so the column
        # reflects reality without forcing every caller to set it explicitly.
        git_provider = data.git_provider
        if git_provider is None and detect_provider(data.git_url) == "github":
            git_provider = "github"

        # Encrypt git token if provided
        encrypted_token = None
        if data.git_token:
            try:
                encrypted_token = encrypt_token(data.git_token)
            except EncryptionError as e:
                self.log.error("Failed to encrypt git token", error=str(e))
                raise

        project = ProjectTable(
            name=data.name,
            slug=data.slug,
            git_url=data.git_url,
            git_provider=git_provider,
            default_branch=data.default_branch,
            protected_branches=data.protected_branches,
            environments=data.environments,
            assigned_cell=data.assigned_cell,
            git_token_encrypted=encrypted_token,
            test_command=data.test_command,
            lint_command=data.lint_command,
            format_command=data.format_command,
            typecheck_command=data.typecheck_command,
            build_command=data.build_command,
            quality_command=data.quality_command,
            created_by=created_by,
        )

        self.session.add(project)
        await self.session.flush()

        self.log.info(
            "Project created",
            project_id=str(project.id),
            slug=data.slug,
            git_url=data.git_url,
            has_git_token=bool(encrypted_token),
            cell=data.assigned_cell.value
            if isinstance(data.assigned_cell, Team)
            else data.assigned_cell,
        )
        await self._maybe_scaffold_conventions(project)
        return project

    async def _maybe_scaffold_conventions(self, project: ProjectTable) -> None:
        """Best-effort: open the conventions scaffold PR (flag-gated, never fatal).

        Imported lazily to avoid the project -> conventions -> git -> project
        import cycle. A scaffold hiccup must never fail project registration.
        """
        if not settings.conventions_enabled:
            return
        from roboco.services.conventions import get_conventions_service

        try:
            await get_conventions_service(self.session).scaffold(project)
        except Exception as exc:
            self.log.warning(
                "Conventions scaffold failed (non-fatal)",
                project_id=str(project.id),
                error=str(exc),
            )

    @staticmethod
    def _register_forge(project: ProjectTable | None) -> ProjectTable | None:
        """Record a loaded project's host→provider mapping for the forge
        router (in-memory, per-process). Riding the getters makes the map
        self-healing: any flow that touches a project re-registers it, so a
        restart never leaves a gitea host unroutable past the first read."""
        if project is not None:
            register_project_forge(project.git_url, project.git_provider)
        return project

    async def get(self, project_id: UUID) -> ProjectTable | None:
        """Get a project by ID."""
        result = await self.session.execute(
            select(ProjectTable).where(ProjectTable.id == project_id)
        )
        return self._register_forge(result.scalar_one_or_none())

    async def get_by_slug(self, slug: str) -> ProjectTable | None:
        """Get a project by its URL-safe slug."""
        result = await self.session.execute(
            select(ProjectTable).where(ProjectTable.slug == slug)
        )
        return self._register_forge(result.scalar_one_or_none())

    async def get_or_raise(self, project_id: UUID) -> ProjectTable:
        """Get a project by ID or raise NotFoundError."""
        project = await self.get(project_id)
        if not project:
            raise NotFoundError("Project", str(project_id))
        return project

    async def update(
        self,
        project_id: UUID,
        data: ProjectUpdate,
    ) -> ProjectTable | None:
        """
        Update a project.

        Args:
            project_id: Project to update
            data: Update data (only non-None fields are applied)

        Returns:
            The updated project or None if not found
        """
        project = await self.get(project_id)
        if not project:
            return None

        self._assert_git_url_allowed(data.git_url)

        # Apply updates for explicitly-set fields (excluding git_token which we
        # handle separately below). exclude_unset keeps UNSET fields out; we do
        # NOT also exclude_none, so a field the caller explicitly set to None
        # clears the stored value (distinct from unset = leave unchanged) — #197.
        update_data = data.model_dump(exclude_unset=True, exclude={"git_token"})

        # Re-validate the forge only when git_url or git_provider is actually
        # changing. An unrelated rename (neither field touched) skips this
        # entirely. When git_provider ISN'T explicitly part of THIS call, it
        # is NOT carried forward from the stored row as if re-declared — a
        # stored "github" may be a Phase-0 auto-stamp from the *old* git_url,
        # and trusting it across a host swap would silently smuggle the GHE
        # escape hatch through exactly the case Phase 0 exists to catch (a
        # git_url edited onto gitlab.com/gitea while git_provider is left
        # alone). Changing the host while keeping an explicit override
        # requires restating git_provider in the same call.
        if "git_url" in update_data or "git_provider" in update_data:
            new_git_url = update_data.get("git_url", project.git_url)
            new_git_provider = update_data.get("git_provider")
            self._assert_forge_supported(new_git_url, new_git_provider)

        # Handle git_token specially (empty string clears, None leaves unchanged)
        token_updated = False
        if data.git_token is not None:
            if data.git_token == "":
                # Clear the token
                project.git_token_encrypted = None
                token_updated = True
                self.log.info("Git token cleared", project_id=str(project_id))
            else:
                # Encrypt and set new token
                try:
                    project.git_token_encrypted = encrypt_token(data.git_token)
                    token_updated = True
                    self.log.info("Git token updated", project_id=str(project_id))
                except EncryptionError as e:
                    self.log.error("Failed to encrypt git token", error=str(e))
                    raise

        for key, value in update_data.items():
            if hasattr(project, key):
                setattr(project, key, value)

        await self.session.flush()

        updated_fields = list(update_data.keys())
        if token_updated:
            updated_fields.append("git_token")

        self.log.info(
            "Project updated",
            project_id=str(project_id),
            updates=updated_fields,
        )
        return project

    async def delete(
        self,
        project_id: UUID,
        *,
        delete_workspaces: bool = False,
    ) -> bool:
        """
        Delete a project.

        Before the delete, abandon any ACTIVE work sessions tied to this
        project so they don't remain ACTIVE with no owning project.
        Optionally remove cloned workspaces from disk (caller opt-in; safer
        default is to leave them for recovery).

        The DB-level cascade on tasks is `RESTRICT`, so if any tasks
        reference this project the delete still fails at the DB layer —
        callers should cancel those tasks first.

        Args:
            project_id: Project to delete
            delete_workspaces: If True, remove on-disk workspaces for this
                project. Default False so disk cleanup is explicit.

        Returns:
            True if deleted, False if not found
        """
        project = await self.get(project_id)
        if not project:
            return False

        from roboco.db.tables import WorkSessionTable
        from roboco.models.work_session import WorkSessionStatus
        from roboco.services.work_session import get_work_session_service

        active_sessions = await self.session.execute(
            select(WorkSessionTable).where(
                WorkSessionTable.project_id == project_id,
                WorkSessionTable.status == WorkSessionStatus.ACTIVE,
            )
        )
        ws_service = get_work_session_service(self.session)
        abandoned = 0
        for ws in active_sessions.scalars().all():
            from roboco.utils.converters import require_uuid

            await ws_service.abandon(require_uuid(ws.id), reason="project deleted")
            abandoned += 1
        if abandoned:
            self.log.info(
                "Abandoned active work sessions before project delete",
                project_id=str(project_id),
                count=abandoned,
            )

        project_slug = project.slug

        await self.session.delete(project)
        await self.session.flush()

        if delete_workspaces:
            from roboco.services.workspace import get_workspace_service

            ws_svc = get_workspace_service(self.session)
            try:
                workspaces = await ws_svc.list_workspaces(project_slug)
                for ws_info in workspaces:
                    from pathlib import Path

                    path = Path(ws_info["path"])
                    if path.exists():
                        import shutil

                        shutil.rmtree(path)
                self.log.info(
                    "Deleted workspaces for project",
                    project_slug=project_slug,
                    count=len(workspaces),
                )
            except Exception as e:
                self.log.warning(
                    "Failed to clean up some workspaces after project delete",
                    project_slug=project_slug,
                    error=str(e),
                )

        self.log.info("Project deleted", project_id=str(project_id))
        return True

    # =========================================================================
    # QUERIES
    # =========================================================================

    async def list_all(
        self,
        active_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectTable]:
        """
        List all projects with pagination.

        Args:
            active_only: If True, only return active projects
            limit: Maximum number of projects to return
            offset: Number of projects to skip

        Returns:
            List of projects
        """
        query = select(ProjectTable)

        if active_only:
            query = query.where(ProjectTable.is_active.is_(True))

        query = query.order_by(ProjectTable.name).limit(limit).offset(offset)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def task_counts_for_projects(
        self, projects: list[ProjectTable]
    ) -> dict[UUID, dict[str, int]]:
        """Per-project task progress (done/active/blocked) — one grouped query.

        One ``GROUP BY project_id`` over tasks for every distinct project_id
        in the list. Returns {project_id: {done, active, blocked}}; a project
        with no tasks is absent (the caller falls back to zeros).
        """
        project_ids = [typing_cast("UUID", p.id) for p in projects if p.id is not None]
        if not project_ids:
            return {}

        result = await self.session.execute(
            select(
                TaskTable.project_id,
                func.coalesce(
                    func.sum(
                        case((TaskTable.status == TaskStatus.COMPLETED, 1), else_=0)
                    ),
                    0,
                ).label("done"),
                func.coalesce(
                    func.sum(
                        case((TaskTable.status == TaskStatus.BLOCKED, 1), else_=0)
                    ),
                    0,
                ).label("blocked"),
                func.coalesce(
                    func.sum(
                        case((TaskTable.status.in_(_INACTIVE_STATUSES), 0), else_=1)
                    ),
                    0,
                ).label("active"),
            )
            .where(TaskTable.project_id.in_(project_ids))
            .group_by(TaskTable.project_id)
        )
        out: dict[UUID, dict[str, int]] = {}
        for row in result.fetchall():
            out[typing_cast("UUID", row.project_id)] = {
                "done": int(row.done or 0),
                "active": int(row.active or 0),
                "blocked": int(row.blocked or 0),
            }
        return out

    async def list_by_cell(
        self,
        cell: Team,
        active_only: bool = True,
    ) -> list[ProjectTable]:
        """
        List projects assigned to a specific cell.

        Args:
            cell: The team/cell to filter by
            active_only: If True, only return active projects

        Returns:
            List of projects for the cell
        """
        query = select(ProjectTable).where(ProjectTable.assigned_cell == cell)

        if active_only:
            query = query.where(ProjectTable.is_active.is_(True))

        query = query.order_by(ProjectTable.name)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    # =========================================================================
    # WORKSPACE MANAGEMENT
    # =========================================================================

    async def set_workspace_path(
        self,
        project_id: UUID,
        workspace_path: str,
    ) -> ProjectTable | None:
        """
        Set the local workspace path for a project.

        Called after cloning/syncing the repository.

        Args:
            project_id: Project to update
            workspace_path: Path to the local workspace directory

        Returns:
            The updated project or None if not found
        """
        project = await self.get(project_id)
        if not project:
            return None

        project.workspace_path = workspace_path
        await self.session.flush()

        self.log.info(
            "Workspace path set",
            project_id=str(project_id),
            workspace_path=workspace_path,
        )
        return project

    async def update_sync_state(
        self,
        project_id: UUID,
        head_commit: str,
    ) -> ProjectTable | None:
        """
        Update the sync state after a git pull/fetch.

        Args:
            project_id: Project to update
            head_commit: Current HEAD commit SHA

        Returns:
            The updated project or None if not found
        """
        from datetime import UTC, datetime

        project = await self.get(project_id)
        if not project:
            return None

        project.head_commit = head_commit
        project.last_synced_at = datetime.now(UTC)
        await self.session.flush()

        self.log.info(
            "Sync state updated",
            project_id=str(project_id),
            head_commit=head_commit[:8],  # Short SHA
        )
        return project

    # =========================================================================
    # GIT TOKEN MANAGEMENT
    # =========================================================================

    async def get_decrypted_token(self, project_id: UUID) -> str | None:
        """
        Get the decrypted git token for a project.

        Used by WorkspaceService and GitService for git operations.
        The token is decrypted on-demand and should not be cached.

        Args:
            project_id: Project to get token for

        Returns:
            Decrypted token or None if no token is set

        Raises:
            EncryptionError: If decryption fails (key mismatch, corrupted data)
        """
        project = await self.get(project_id)
        if not project or not project.git_token_encrypted:
            return None

        try:
            return decrypt_token(project.git_token_encrypted)
        except EncryptionError:
            self.log.error(
                "Failed to decrypt git token",
                project_id=str(project_id),
                error="encryption_key_mismatch_or_corrupted",
            )
            raise

    async def get_decrypted_token_by_slug(self, slug: str) -> str | None:
        """
        Get the decrypted git token for a project by slug.

        Convenience method for services that work with project slugs.

        Args:
            slug: Project slug

        Returns:
            Decrypted token or None if no token is set

        Raises:
            EncryptionError: If decryption fails
        """
        project = await self.get_by_slug(slug)
        if not project or not project.git_token_encrypted:
            return None

        try:
            return decrypt_token(project.git_token_encrypted)
        except EncryptionError:
            self.log.error(
                "Failed to decrypt git token",
                project_slug=slug,
                error="encryption_key_mismatch_or_corrupted",
            )
            raise

    # =========================================================================
    # ACCESS CONTROL
    # =========================================================================

    async def add_allowed_agent(
        self,
        project_id: UUID,
        agent_id: UUID,
    ) -> ProjectTable | None:
        """
        Add an agent to the allowed list for a project.

        Args:
            project_id: Project to update
            agent_id: Agent to allow

        Returns:
            The updated project or None if not found
        """
        project = await self.get(project_id)
        if not project:
            return None

        # Initialize list if None (means all agents in cell allowed)
        if project.allowed_agents is None:
            project.allowed_agents = [agent_id]
        elif agent_id not in project.allowed_agents:
            project.allowed_agents = [*project.allowed_agents, agent_id]

        await self.session.flush()
        return project

    async def remove_allowed_agent(
        self,
        project_id: UUID,
        agent_id: UUID,
    ) -> ProjectTable | None:
        """
        Remove an agent from the allowed list for a project.

        Args:
            project_id: Project to update
            agent_id: Agent to remove

        Returns:
            The updated project or None if not found
        """
        project = await self.get(project_id)
        if not project or project.allowed_agents is None:
            return None

        if agent_id in project.allowed_agents:
            project.allowed_agents = [
                a for a in project.allowed_agents if a != agent_id
            ]

        await self.session.flush()
        return project

    async def check_agent_access(
        self,
        project_id: UUID,
        agent_id: UUID,
        agent_team: Team,
    ) -> bool:
        """
        Check if an agent has access to a project.

        Access rules:
        1. Agent must be in the project's assigned cell
        2. If allowed_agents is set, agent must be in the list
        3. If allowed_agents is None, all agents in the cell have access

        Args:
            project_id: Project to check
            agent_id: Agent to check
            agent_team: Agent's team/cell

        Returns:
            True if agent has access, False otherwise
        """
        project = await self.get(project_id)
        if not project:
            return False

        # Must be in the assigned cell
        if project.assigned_cell != agent_team:
            return False

        # If no specific allowed list, all cell members have access
        if project.allowed_agents is None:
            return True

        # Check the allowed list
        return agent_id in project.allowed_agents


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_project_service(session: AsyncSession) -> ProjectService:
    """Get a ProjectService instance."""
    return ProjectService(session)
