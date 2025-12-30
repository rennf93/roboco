"""
Workspace Service

Manages multi-agent workspaces for git operations.

Each agent gets their own workspace (git clone) for a project, allowing
parallel development without conflicts:

    /data/workspaces/
    └── {project-slug}/
        └── {team}/
            └── {agent-slug}/
                └── [git repo files]

Example:
    /data/workspaces/roboco/backend/be-dev-1/
    /data/workspaces/roboco/backend/be-dev-2/
    /data/workspaces/roboco/frontend/fe-dev-1/
"""

import asyncio
import subprocess
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable
from roboco.logging import get_logger
from roboco.models.base import Team

logger = get_logger(__name__)


class WorkspaceError(Exception):
    """Raised when workspace operations fail."""

    pass


class WorkspaceService:
    """
    Service for managing agent workspaces.

    Workspaces follow the structure:
        {workspaces_root}/{project_slug}/{team}/{agent_slug}/

    This allows:
    - Multiple agents to work on the same project in parallel
    - Each agent has their own git working tree
    - Agents can be on different branches simultaneously
    - No file locking conflicts between agents
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.root = Path(settings.workspaces_root)

    def get_workspace_path(
        self,
        project_slug: str,
        team: Team | str,
        agent_slug: str,
    ) -> Path:
        """
        Compute the workspace path for an agent on a project.

        Args:
            project_slug: Project identifier (e.g., 'roboco')
            team: Agent's team (e.g., Team.BACKEND or 'backend')
            agent_slug: Agent identifier (e.g., 'be-dev-1')

        Returns:
            Path to the workspace directory

        Example:
            >>> get_workspace_path('roboco', Team.BACKEND, 'be-dev-1')
            Path('/data/workspaces/roboco/backend/be-dev-1')
        """
        team_str = team.value if isinstance(team, Team) else str(team)
        return self.root / project_slug / team_str / agent_slug

    async def resolve_workspace(
        self,
        project_slug: str,
        agent_id: UUID | str,
    ) -> Path:
        """
        Resolve workspace path from project slug and agent ID.

        Looks up the agent to get team and slug, then computes path.

        Args:
            project_slug: Project identifier
            agent_id: Agent UUID or slug

        Returns:
            Path to the workspace directory

        Raises:
            WorkspaceError: If agent not found
        """
        from sqlalchemy import select

        # Look up agent
        agent_id_str = str(agent_id)

        # Try by UUID first, then by slug
        query = select(AgentTable)
        try:
            agent_uuid = UUID(agent_id_str)
            query = query.where(AgentTable.id == agent_uuid)
        except ValueError:
            query = query.where(AgentTable.slug == agent_id_str)

        result = await self.session.execute(query)
        agent = result.scalar_one_or_none()

        if not agent:
            raise WorkspaceError(f"Agent not found: {agent_id}")

        team = agent.team if agent.team else Team.BACKEND
        return self.get_workspace_path(project_slug, team, agent.slug)

    async def ensure_workspace(
        self,
        project_slug: str,
        agent_id: UUID | str,
        git_url: str | None = None,
        default_branch: str = "main",
    ) -> Path:
        """
        Ensure workspace exists, cloning if necessary.

        Args:
            project_slug: Project identifier
            agent_id: Agent UUID or slug
            git_url: Git URL to clone (fetched from project if not provided)
            default_branch: Default branch to checkout

        Returns:
            Path to the workspace directory

        Raises:
            WorkspaceError: If workspace creation fails
        """
        workspace = await self.resolve_workspace(project_slug, agent_id)

        # Check if already exists
        if (workspace / ".git").exists():
            logger.debug(
                "Workspace already exists",
                workspace=str(workspace),
                project=project_slug,
            )
            return workspace

        # Get git URL if not provided
        if not git_url:
            from sqlalchemy import select

            result = await self.session.execute(
                select(ProjectTable).where(ProjectTable.slug == project_slug)
            )
            project = result.scalar_one_or_none()
            if not project:
                raise WorkspaceError(f"Project not found: {project_slug}")
            git_url = project.git_url
            default_branch = project.default_branch or default_branch

        # Clone the repository
        await self._clone_repo(workspace, git_url, default_branch)
        return workspace

    async def _clone_repo(
        self,
        workspace: Path,
        git_url: str,
        default_branch: str,
    ) -> None:
        """
        Clone a git repository to the workspace.

        Args:
            workspace: Target directory
            git_url: Git URL to clone
            default_branch: Branch to checkout

        Raises:
            WorkspaceError: If clone fails
        """
        # Create parent directories
        workspace.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Cloning repository",
            workspace=str(workspace),
            git_url=git_url,
            branch=default_branch,
        )

        def _do_clone() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "git",
                    "clone",
                    "--branch",
                    default_branch,
                    "--single-branch",
                    git_url,
                    str(workspace),
                ],
                capture_output=True,
                text=True,
                timeout=settings.workspace_clone_timeout,
                check=True,
            )

        try:
            await asyncio.to_thread(_do_clone)
            logger.info(
                "Repository cloned successfully",
                workspace=str(workspace),
            )
        except subprocess.CalledProcessError as e:
            raise WorkspaceError(
                f"Failed to clone repository: {e.stderr or e.stdout}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise WorkspaceError(
                f"Clone timed out after {settings.workspace_clone_timeout}s"
            ) from e

    async def workspace_exists(
        self,
        project_slug: str,
        agent_id: UUID | str,
    ) -> bool:
        """Check if a workspace exists for the given project and agent."""
        try:
            workspace = await self.resolve_workspace(project_slug, agent_id)
            return (workspace / ".git").exists()
        except WorkspaceError:
            return False

    async def list_workspaces(self, project_slug: str) -> list[dict]:
        """
        List all workspaces for a project.

        Returns:
            List of workspace info dicts with team, agent, and path
        """
        project_dir = self.root / project_slug
        if not project_dir.exists():
            return []

        workspaces = []
        for team_dir in project_dir.iterdir():
            if not team_dir.is_dir():
                continue
            for agent_dir in team_dir.iterdir():
                if not agent_dir.is_dir():
                    continue
                if (agent_dir / ".git").exists():
                    workspaces.append(
                        {
                            "team": team_dir.name,
                            "agent": agent_dir.name,
                            "path": str(agent_dir),
                            "exists": True,
                        }
                    )
        return workspaces

    async def delete_workspace(
        self,
        project_slug: str,
        agent_id: UUID | str,
    ) -> bool:
        """
        Delete a workspace (use with caution).

        Args:
            project_slug: Project identifier
            agent_id: Agent UUID or slug

        Returns:
            True if deleted, False if didn't exist
        """
        import shutil

        workspace = await self.resolve_workspace(project_slug, agent_id)
        if not workspace.exists():
            return False

        logger.warning(
            "Deleting workspace",
            workspace=str(workspace),
        )

        def _do_delete() -> None:
            shutil.rmtree(workspace)

        await asyncio.to_thread(_do_delete)
        return True


def get_workspace_service(session: AsyncSession) -> WorkspaceService:
    """Factory function to get workspace service."""
    return WorkspaceService(session)
