"""
Project Service

Provides CRUD operations and git workflow management for projects.
Projects represent git repositories that agents work on.
"""

from typing import ClassVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import ProjectTable
from roboco.models.base import Team
from roboco.models.project import ProjectCreate, ProjectUpdate
from roboco.services.base import BaseService, ConflictError, NotFoundError
from roboco.utils.crypto import EncryptionError, decrypt_token, encrypt_token


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
            default_branch=data.default_branch,
            protected_branches=data.protected_branches,
            assigned_cell=data.assigned_cell,
            git_token_encrypted=encrypted_token,
            test_command=data.test_command,
            lint_command=data.lint_command,
            format_command=data.format_command,
            typecheck_command=data.typecheck_command,
            build_command=data.build_command,
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
        return project

    async def get(self, project_id: UUID) -> ProjectTable | None:
        """Get a project by ID."""
        result = await self.session.execute(
            select(ProjectTable).where(ProjectTable.id == project_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> ProjectTable | None:
        """Get a project by its URL-safe slug."""
        result = await self.session.execute(
            select(ProjectTable).where(ProjectTable.slug == slug)
        )
        return result.scalar_one_or_none()

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

        # Apply updates for non-None fields (excluding git_token which we handled)
        update_data = data.model_dump(
            exclude_unset=True, exclude_none=True, exclude={"git_token"}
        )
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
