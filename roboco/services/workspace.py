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
import os
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

# Agent container runs the `agent` user created in agent-base.Dockerfile.
# Debian's `useradd -m` defaults to uid 1000 when that uid is free.
# Overridable via env so operators can customize if they rebuild agent-base
# with a different id.
_AGENT_UID = int(os.environ.get("ROBOCO_AGENT_UID", "1000"))
_AGENT_GID = int(os.environ.get("ROBOCO_AGENT_GID", "1000"))


def _ensure_agent_owned(workspace: Path) -> None:
    """Recursively chown + group-write a workspace for the agent user.

    Orchestrator runs as root so anything it clones or writes is root-owned.
    Agent containers run as uid 1000 and must be able to create
    .git/index.lock, refs, packed-refs, and new source files — otherwise
    every git operation (and even plain file writes) fails with
    "Permission denied". Called after clone and on every ensure_workspace
    so legacy (pre-fix) workspaces get repaired.

    Two defenses (both cheap, both idempotent):
    1. chown every entry to (AGENT_UID, AGENT_GID). On setups where user
       namespaces silently remap or reject the chown (some NAS / rootless
       docker configs), we log the failure instead of swallowing it — so
       when writes still fail from the agent, we can actually see why.
    2. chmod g+w on every file/dir. If chown doesn't take effect, having
       the group writable (and with AGENT_GID) is enough for uid 1000 to
       write, provided agent is in that group. Belt + suspenders.

    The previous fast-path (skip walk if top-level stat already matches)
    was unsafe: a root-owned file deep under an agent-owned top dir would
    not get repaired, which is exactly how README.md ends up root:root
    even after ensure_workspace runs.
    """
    import stat as _stat

    failed_chowns = 0
    for root, dirs, files in os.walk(workspace):
        for entry in (root, *[str(Path(root) / d) for d in dirs],
                      *[str(Path(root) / f) for f in files]):
            try:
                st = Path(entry).stat()
                if st.st_uid != _AGENT_UID or st.st_gid != _AGENT_GID:
                    os.chown(entry, _AGENT_UID, _AGENT_GID)
            except OSError:
                failed_chowns += 1
            try:
                st = Path(entry).stat()
                # Add group read + write (and execute for dirs). chmod
                # always respects the caller's capabilities — if chown
                # failed because we're not root, we still can't chmod
                # files we don't own.
                new_mode = st.st_mode | _stat.S_IRGRP | _stat.S_IWGRP
                if _stat.S_ISDIR(st.st_mode):
                    new_mode |= _stat.S_IXGRP
                if new_mode != st.st_mode:
                    Path(entry).chmod(new_mode)
            except OSError:
                pass

    if failed_chowns:
        logger.warning(
            "Some chowns failed during ensure_agent_owned — "
            "agent writes may still fail. Check docker user-namespace "
            "config or run agents as root on this host.",
            workspace=str(workspace),
            failures=failed_chowns,
        )

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
            # Healthy clone — nothing to do except make sure it's still
            # owned by the agent user. Orchestrator restarts or older
            # clones (pre-ownership-fix) may leave root-owned trees that
            # break every subsequent write from inside the agent container.
            #
            # `.git` existing is necessary but NOT sufficient — a failed
            # clone can leave behind a stub .git/ with only FETCH_HEAD and
            # no HEAD/objects, which looks "healthy" to a naive check but
            # breaks every subsequent fetch/checkout ("origin/<branch> is
            # not a commit"). Require HEAD + objects/ as the real signal.
            git_dir = workspace / ".git"
            if (
                git_dir.exists()
                and (git_dir / "HEAD").exists()
                and (git_dir / "objects").exists()
            ):
                await asyncio.to_thread(_ensure_agent_owned, workspace)
                logger.debug(
                    "Workspace already exists",
                    workspace=str(workspace),
                    project=project_slug,
                )
                return workspace

            # Partial clone: directory exists but `.git` is missing or
            # a stub. git clone refuses to clone into a non-empty
            # directory, so remove it first instead of letting the next
            # clone fail.
            if workspace.exists():
                logger.warning(
                    "Removing partial/stub workspace before re-clone",
                    workspace=str(workspace),
                    project=project_slug,
                    had_git_dir=git_dir.exists(),
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
            # Do NOT pass --single-branch. Agents work on feature branches
            # pushed by peers; QA and documenter need to `git fetch` those
            # branches after a dev pushes. --single-branch locks the remote
            # refspec to `+refs/heads/{default_branch}:refs/remotes/origin/
            # {default_branch}`, so subsequent fetches silently ignore every
            # other branch. The symptom is QA doing `checkout origin/
            # feature/...` and seeing "not a commit" even though the branch
            # is on GitHub. --no-tags keeps the clone light.
            return subprocess.run(
                [
                    "git",
                    "clone",
                    "--branch",
                    default_branch,
                    "--no-tags",
                    auth_url,
                    str(workspace),
                ],
                capture_output=True,
                text=True,
                timeout=settings.workspace_clone_timeout,
                check=True,
            )

        def _configure_git() -> None:
            """Configure git author info + scrub embedded PAT from remote URL.

            The clone URL carries the PAT for authentication (`https://TOKEN@
            github.com/...`), which `git clone` then writes into
            `.git/config`. Leaving it there lets anyone with read access to
            the workspace — including the agent inside its container — read
            the token and exfiltrate or use it directly against GitHub,
            bypassing the orchestrator's git service.

            We keep push/fetch working by letting the orchestrator inject
            the token just-in-time at the subprocess level (`-c
            http.extraheader='Authorization: bearer TOKEN'`) when it needs
            to hit origin; see GitService.
            """
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

            # Scrub embedded credentials from the remote URL.
            if git_token and auth_url != git_url:
                subprocess.run(
                    ["git", "remote", "set-url", "origin", git_url],
                    cwd=str(workspace),
                    check=True,
                    capture_output=True,
                )

        try:
            await asyncio.to_thread(_do_clone)
            await asyncio.to_thread(_configure_git)
            # Transfer ownership to the agent user so the agent can write
            # into .git/ and the working tree from inside its container.
            await asyncio.to_thread(_ensure_agent_owned, workspace)
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
