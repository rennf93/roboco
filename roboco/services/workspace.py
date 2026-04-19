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
import re
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from roboco.config import settings
from roboco.db.tables import AgentTable
from roboco.logging import get_logger
from roboco.models.base import Team

logger = get_logger(__name__)

# Per (project_slug, agent_slug) async lock to serialize concurrent
# ensure_workspace calls in the same orchestrator process. Prevents two
# coroutines from both passing the ".git exists?" check and then both
# trying to clone into the same directory.
_ENSURE_WORKSPACE_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _ensure_lock_for(project_slug: str, agent_slug: str) -> asyncio.Lock:
    """Return the asyncio.Lock for a (project, agent) pair, creating lazily."""
    key = (project_slug, agent_slug)
    lock = _ENSURE_WORKSPACE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ENSURE_WORKSPACE_LOCKS[key] = lock
    return lock


def _inject_token_into_url(git_url: str, token: str | None) -> str:
    """
    Inject GitHub PAT into HTTPS git URL for authentication.

    Args:
        git_url: Original git URL (SSH or HTTPS)
        token: GitHub PAT (if None, returns original URL)

    Returns:
        URL with embedded token for HTTPS, or original URL for SSH

    Example:
        https://github.com/org/repo.git -> https://TOKEN@github.com/org/repo.git
    """
    if not token:
        return git_url

    # Only inject for HTTPS URLs
    if not git_url.startswith("https://"):
        return git_url

    # Check if token already present
    if "@" in git_url.split("//")[1].split("/", maxsplit=1)[0]:
        return git_url

    # Inject token: https://github.com -> https://TOKEN@github.com
    return re.sub(r"^https://", f"https://{token}@", git_url)


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

        Protects against:
        - Partial clones (directory exists but `.git` does not) — cleans up
          the incomplete directory before re-cloning.
        - Concurrent callers — per (project, agent) asyncio.Lock serializes
          ensure_workspace calls so two coroutines can't both try to clone
          into the same directory.

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
        from sqlalchemy import select

        from roboco.services.project import get_project_service

        # Look up agent for workspace path and git identity
        agent_id_str = str(agent_id)
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

        # Compute workspace path
        team = agent.team if agent.team else Team.BACKEND
        workspace = self.get_workspace_path(project_slug, team, agent.slug)

        lock = _ensure_lock_for(project_slug, agent.slug)
        async with lock:
            # Healthy clone — nothing to do.
            if (workspace / ".git").exists():
                logger.debug(
                    "Workspace already exists",
                    workspace=str(workspace),
                    project=project_slug,
                )
                return workspace

            # Partial clone: directory exists but no `.git`. git clone
            # refuses to clone into a non-empty directory, so remove it
            # first instead of letting the next clone fail.
            if workspace.exists():
                logger.warning(
                    "Removing partial workspace before re-clone",
                    workspace=str(workspace),
                    project=project_slug,
                )
                shutil.rmtree(workspace)

            # Get git URL and token from project
            project_service = get_project_service(self.session)
            project = await project_service.get_by_slug(project_slug)
            if not project:
                raise WorkspaceError(f"Project not found: {project_slug}")

            if not git_url:
                git_url = project.git_url
                default_branch = project.default_branch or default_branch

            # Get decrypted token from project (per-project token, no global fallback).
            # Convert cryptographic failures into a clear WorkspaceError — callers
            # would otherwise see an opaque 500.
            from roboco.utils.crypto import EncryptionError

            try:
                git_token = await project_service.get_decrypted_token_by_slug(
                    project_slug
                )
            except EncryptionError as e:
                raise WorkspaceError(
                    f"Failed to decrypt git token for project '{project_slug}'. "
                    "The ROBOCO_ENCRYPTION_KEY may have been rotated or the "
                    "stored token is corrupted. Re-set the project token."
                ) from e

            # Validate token is set for HTTPS URLs (no global fallback)
            if git_url.startswith("https://") and not git_token:
                raise WorkspaceError(
                    f"Project '{project_slug}' requires a git token for HTTPS clone. "
                    "Configure a GitHub PAT in the project settings."
                )

            # Clone the repository with agent identity
            await self._clone_repo(
                workspace,
                git_url,
                default_branch,
                git_token,
                agent=agent,
            )
            return workspace

    async def _clone_repo(
        self,
        workspace: Path,
        git_url: str,
        default_branch: str,
        git_token: str | None = None,
        agent: AgentTable | None = None,
    ) -> None:
        """
        Clone a git repository to the workspace.

        Args:
            workspace: Target directory
            git_url: Git URL to clone
            default_branch: Branch to checkout
            git_token: GitHub PAT for authentication (per-project)
            agent: Agent for git identity (name/email in commits)

        Raises:
            WorkspaceError: If clone fails
        """
        # Create parent directories
        workspace.parent.mkdir(parents=True, exist_ok=True)

        # Inject project-specific token for HTTPS URLs
        auth_url = _inject_token_into_url(git_url, git_token)

        # Log without exposing token
        logger.info(
            "Cloning repository",
            workspace=str(workspace),
            git_url=git_url,  # Log original URL, not auth URL
            branch=default_branch,
            using_token=bool(git_token and auth_url != git_url),
        )

        def _do_clone() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "git",
                    "clone",
                    "--branch",
                    default_branch,
                    "--single-branch",
                    auth_url,
                    str(workspace),
                ],
                capture_output=True,
                text=True,
                timeout=settings.workspace_clone_timeout,
                check=True,
            )

        def _configure_git() -> None:
            """Configure git author info based on agent identity."""
            name = agent.name if agent else "RoboCo Agent"
            slug = agent.slug if agent else "agent"

            subprocess.run(
                ["git", "config", "user.name", name],
                cwd=str(workspace),
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", f"{slug}@agents.roboco.dev"],
                cwd=str(workspace),
                check=True,
                capture_output=True,
            )

        try:
            await asyncio.to_thread(_do_clone)
            await asyncio.to_thread(_configure_git)
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
