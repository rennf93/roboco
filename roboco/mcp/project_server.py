"""
Project MCP Server

Exposes project and workspace management tools to Claude Code agents
with role-based access control.

Tools available to ALL agents:
- roboco_project_list: List available projects
- roboco_project_get: Get project details
- roboco_workspace_ensure: Ensure workspace exists for calling agent
- roboco_workspace_status: Get workspace status

Tools available ONLY to PM/Board/CEO:
- roboco_project_create: Register a new project
- roboco_project_update: Update project settings
- roboco_workspace_list: List all agent workspaces for a project
"""

from typing import Any

from fastapi import status
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import (
    get_agent_role,
    get_agent_team,
    is_ceo,
)
from roboco.mcp.schemas import ProjectCreateInput, ProjectUpdateInput
from roboco.mcp.utils import ApiClient, format_error_response
from roboco.models.base import Team

# =============================================================================
# PERMISSION CONSTANTS
# =============================================================================

# Roles that can create projects (Main PM, Board, CEO)
PROJECT_CREATE_ROLES = frozenset({"main_pm", "product_owner", "head_marketing", "ceo"})

# Roles that can list all workspaces (PMs and privileged)
WORKSPACE_LIST_ROLES = frozenset({"main_pm", "cell_pm", "auditor", "ceo"})

# Roles with full access (bypass cell filtering)
PRIVILEGED_ROLES = frozenset({"ceo", "auditor", "main_pm"})


# =============================================================================
# PERMISSION HELPERS
# =============================================================================


def can_create_projects(agent_id: str) -> bool:
    """Check if agent can create projects."""
    return is_ceo(agent_id) or get_agent_role(agent_id) in PROJECT_CREATE_ROLES


def can_update_project(agent_id: str, project_cell: str | None) -> tuple[bool, str]:
    """Check if agent can update a project. Returns (allowed, reason)."""
    role = get_agent_role(agent_id)

    # CEO always allowed
    if is_ceo(agent_id):
        return True, ""

    # Privileged roles can update any project
    if role in {"main_pm", "product_owner", "head_marketing", "auditor"}:
        return True, ""

    # Cell PM can only update their cell's projects
    if role == "cell_pm":
        agent_team = get_agent_team(agent_id)
        if agent_team == project_cell:
            return True, ""
        return False, f"Cell PM can only update projects in {agent_team}"

    return False, f"Role {role} cannot update projects"


def can_list_workspaces(agent_id: str, project_cell: str | None) -> tuple[bool, str]:
    """Check if agent can list workspaces. Returns (allowed, reason)."""
    role = get_agent_role(agent_id)

    if is_ceo(agent_id) or role in PRIVILEGED_ROLES:
        return True, ""

    if role == "cell_pm":
        agent_team = get_agent_team(agent_id)
        if agent_team == project_cell:
            return True, ""
        return False, f"Cell PM can only list workspaces in {agent_team}"

    return False, "Only PMs can list workspaces"


def _is_privileged_role(agent_id: str) -> bool:
    """Return True when the agent has project-wide visibility."""
    return is_ceo(agent_id) or get_agent_role(agent_id) in PRIVILEGED_ROLES


def filter_projects_by_access(
    agent_id: str, projects: list[dict[str, Any]], cell_filter: str | None
) -> list[dict[str, Any]]:
    """Filter projects based on agent access level."""
    if _is_privileged_role(agent_id):
        if cell_filter:
            return [p for p in projects if p.get("assigned_cell") == cell_filter]
        return projects

    agent_team = get_agent_team(agent_id)
    return [p for p in projects if p.get("assigned_cell") == agent_team]


# =============================================================================
# HANDLER FUNCTIONS
# =============================================================================


async def _handle_project_list(
    client: ApiClient,
    agent_id: str,
    cell: str | None,
    active_only: bool,
) -> dict[str, Any]:
    """Handle project list request."""
    params: dict[str, Any] = {"active_only": active_only}
    if cell:
        params["cell"] = cell

    try:
        resp = await client.get("/projects", params=params)
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR", f"Failed to connect: {type(e).__name__}"
        )

    if not resp.ok:
        return format_error_response(
            "LIST_FAILED", "Failed to list projects", {"status": resp.status_code}
        )

    projects = resp.json()
    filtered = filter_projects_by_access(agent_id, projects, cell)

    return {
        "status": "success",
        "data": {"projects": filtered, "total": len(filtered)},
        "guidance": "Use roboco_project_get(slug) for details.",
    }


async def _handle_project_get(client: ApiClient, slug: str) -> dict[str, Any]:
    """Handle project get request."""
    try:
        resp = await client.get(f"/projects/{slug}")
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR", f"Failed to connect: {type(e).__name__}"
        )

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response(
            "NOT_FOUND",
            f"Project '{slug}' not found",
            {"hint": "Use roboco_project_list() to see available projects."},
        )

    if not resp.ok:
        return format_error_response(
            "GET_FAILED", "Failed to get project", {"status": resp.status_code}
        )

    project = resp.json()
    return {
        "status": "success",
        "data": project,
        "guidance": "Use git tools with this project_slug for development.",
    }


def _validate_project_create_inputs(
    agent_id: str, data: ProjectCreateInput
) -> dict[str, Any] | None:
    """Permission + cell validation for project creation. Returns error or None."""
    if not can_create_projects(agent_id):
        return format_error_response(
            "PERMISSION_DENIED",
            "Only Main PM, Board, and CEO can create projects",
            {"role": get_agent_role(agent_id)},
        )

    try:
        Team(data.assigned_cell)
    except ValueError:
        return format_error_response(
            "INVALID_CELL",
            f"Invalid cell: {data.assigned_cell}. Must be backend, frontend, ux_ui.",
        )
    return None


def _build_project_create_payload(data: ProjectCreateInput) -> dict[str, Any]:
    """Build the /projects POST body from the input model."""
    return {
        "name": data.name,
        "slug": data.slug,
        "git_url": data.git_url,
        "assigned_cell": data.assigned_cell,
        "default_branch": data.default_branch,
        "protected_branches": data.protected_branches,
        "test_command": data.test_command,
        "lint_command": data.lint_command,
        "format_command": data.format_command,
        "typecheck_command": data.typecheck_command,
        "build_command": data.build_command,
    }


async def _handle_project_create(
    client: ApiClient, agent_id: str, data: ProjectCreateInput
) -> dict[str, Any]:
    """Handle project creation."""
    if error := _validate_project_create_inputs(agent_id, data):
        return error

    try:
        resp = await client.post("/projects", json=_build_project_create_payload(data))
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR", f"Failed to connect: {type(e).__name__}"
        )

    if resp.is_status(status.HTTP_409_CONFLICT):
        return format_error_response(
            "ALREADY_EXISTS", f"Project with slug '{data.slug}' already exists"
        )

    if not resp.is_status(status.HTTP_201_CREATED):
        return format_error_response(
            "CREATE_FAILED",
            "Failed to create project",
            {"status": resp.status_code, "detail": resp.text},
        )

    project = resp.json()
    return {
        "status": "created",
        "data": project,
        "guidance": (
            f"Project '{data.slug}' created. Workspaces will be auto-cloned "
            "when agents first need them."
        ),
    }


async def _fetch_project_for_update(
    client: ApiClient, agent_id: str, slug: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch project and validate update permission. Returns (project, error)."""
    try:
        get_resp = await client.get(f"/projects/{slug}")
    except Exception as e:
        return None, format_error_response(
            "CONNECTION_ERROR", f"Failed to connect: {type(e).__name__}"
        )

    if get_resp.is_status(status.HTTP_404_NOT_FOUND):
        return None, format_error_response("NOT_FOUND", f"Project '{slug}' not found")

    if not get_resp.ok:
        return None, format_error_response("GET_FAILED", "Failed to get project")

    project = get_resp.json()
    allowed, reason = can_update_project(agent_id, project.get("assigned_cell"))
    if not allowed:
        return None, format_error_response("PERMISSION_DENIED", reason)

    return project, None


async def _send_project_patch(
    client: ApiClient, slug: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """PATCH /projects/{slug} and format the response."""
    try:
        resp = await client.patch(f"/projects/{slug}", json=payload)
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR", f"Failed to connect: {type(e).__name__}"
        )

    if not resp.ok:
        return format_error_response(
            "UPDATE_FAILED",
            "Failed to update project",
            {"status": resp.status_code, "detail": resp.text},
        )

    return {
        "status": "updated",
        "data": resp.json(),
        "guidance": f"Project '{slug}' updated successfully.",
    }


async def _handle_project_update(
    client: ApiClient, agent_id: str, slug: str, data: ProjectUpdateInput
) -> dict[str, Any]:
    """Handle project update."""
    _, error = await _fetch_project_for_update(client, agent_id, slug)
    if error:
        return error

    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    if not payload:
        return format_error_response("NO_CHANGES", "No fields to update provided")

    return await _send_project_patch(client, slug, payload)


async def _verify_project_exists(
    client: ApiClient, project_slug: str
) -> dict[str, Any] | None:
    """Return an error dict if the project cannot be fetched, else None."""
    try:
        project_resp = await client.get(f"/projects/{project_slug}")
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR", f"Failed to connect: {type(e).__name__}"
        )

    if project_resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response(
            "PROJECT_NOT_FOUND",
            f"Project '{project_slug}' not found",
            {"hint": "Use roboco_project_list() to see available projects."},
        )

    if not project_resp.ok:
        return format_error_response("GET_FAILED", "Failed to get project")
    return None


async def _fetch_git_status(
    client: ApiClient, project_slug: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (git_status_json, error) from GET /git/status."""
    try:
        git_resp = await client.get(f"/git/status?project_slug={project_slug}")
    except Exception as e:
        return None, format_error_response(
            "CONNECTION_ERROR", f"Failed to connect: {type(e).__name__}"
        )

    if not git_resp.ok:
        return None, format_error_response(
            "WORKSPACE_FAILED",
            "Failed to ensure workspace",
            {"status": git_resp.status_code, "detail": git_resp.text},
        )
    return git_resp.json(), None


async def _handle_workspace_ensure(
    client: ApiClient, project_slug: str, agent_id: str
) -> dict[str, Any]:
    """Handle workspace ensure request."""
    if error := await _verify_project_exists(client, project_slug):
        return error

    git_status, error = await _fetch_git_status(client, project_slug)
    if error or git_status is None:
        return error or format_error_response("WORKSPACE_FAILED", "No git status")

    agent_team = get_agent_team(agent_id)
    workspace_path = f"/data/workspaces/{project_slug}/{agent_team}/{agent_id}"
    guidance = (
        f"Workspace ready at {workspace_path}. "
        "Use roboco_git_* MCP tools for git operations."
    )
    return {
        "status": "success",
        "data": {
            "exists": True,
            "branch": git_status.get("current_branch"),
            "has_uncommitted": git_status.get("has_changes", False),
            "path": workspace_path,
        },
        "guidance": guidance,
    }


async def _handle_workspace_status(
    client: ApiClient, project_slug: str, agent_id: str
) -> dict[str, Any]:
    """Handle workspace status request."""
    try:
        resp = await client.get(f"/git/status?project_slug={project_slug}")
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR", f"Failed to connect: {type(e).__name__}"
        )

    # Compute full workspace path for agent
    agent_team = get_agent_team(agent_id)
    workspace_path = f"/data/workspaces/{project_slug}/{agent_team}/{agent_id}"

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        not_found_guidance = (
            f"Workspace not found at {workspace_path}. "
            "Use roboco_workspace_ensure() to create."
        )
        return {
            "status": "success",
            "data": {"exists": False, "path": workspace_path},
            "guidance": not_found_guidance,
        }

    if not resp.ok:
        return format_error_response(
            "STATUS_FAILED",
            "Failed to get workspace status",
            {"status": resp.status_code},
        )

    git_status = resp.json()
    ready_guidance = (
        f"Workspace ready at {workspace_path}. "
        "Use roboco_git_* MCP tools for git operations."
    )
    return {
        "status": "success",
        "data": {
            "exists": True,
            "branch": git_status.get("current_branch"),
            "has_uncommitted": git_status.get("has_changes", False),
            "staged_files": git_status.get("staged_files", []),
            "unstaged_files": git_status.get("unstaged_files", []),
            "path": workspace_path,
        },
        "guidance": ready_guidance,
    }


async def _handle_workspace_list(
    client: ApiClient, agent_id: str, project_slug: str
) -> dict[str, Any]:
    """Handle workspace list request (PM only)."""
    # First get project to check cell
    try:
        project_resp = await client.get(f"/projects/{project_slug}")
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR", f"Failed to connect: {type(e).__name__}"
        )

    if project_resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response(
            "PROJECT_NOT_FOUND", f"Project '{project_slug}' not found"
        )

    if not project_resp.ok:
        return format_error_response("GET_FAILED", "Failed to get project")

    project = project_resp.json()
    allowed, reason = can_list_workspaces(agent_id, project.get("assigned_cell"))
    if not allowed:
        return format_error_response("PERMISSION_DENIED", reason)

    # Note: There's no direct workspace list API endpoint yet
    # We'd need to add one, or return a placeholder
    return {
        "status": "success",
        "data": {
            "project": project_slug,
            "note": "Workspace list not yet implemented in API",
        },
        "guidance": "Workspaces are auto-created when agents access a project.",
    }


# =============================================================================
# MCP SERVER FACTORY
# =============================================================================


_PROJECT_UPDATE_ROLES = frozenset(
    {"main_pm", "cell_pm", "product_owner", "head_marketing", "auditor"}
)


def _can_update_project_tool(agent_id: str) -> bool:
    """Return True if the agent may register a project_update tool."""
    return is_ceo(agent_id) or get_agent_role(agent_id) in _PROJECT_UPDATE_ROLES


def _can_list_workspaces_tool(agent_id: str) -> bool:
    """Return True if the agent may register a workspace_list tool."""
    return is_ceo(agent_id) or get_agent_role(agent_id) in WORKSPACE_LIST_ROLES


def _register_common_project_tools(
    mcp: FastMCP, client: ApiClient, agent_id: str
) -> None:
    """Register tools available to all agents on the project server."""

    @mcp.tool()
    async def roboco_project_list(
        cell: str | None = None,
        active_only: bool = True,
    ) -> dict[str, Any]:
        """
        List available projects.

        Cell PMs see only their cell's projects.
        Main PM, CEO, and privileged roles see all projects.

        Args:
            cell: Optional cell filter (backend, frontend, ux_ui)
            active_only: Only return active projects (default: True)

        Returns:
            List of project summaries
        """
        return await _handle_project_list(client, agent_id, cell, active_only)

    @mcp.tool()
    async def roboco_project_get(slug: str) -> dict[str, Any]:
        """
        Get detailed information about a project.

        Args:
            slug: Project slug (e.g., 'roboco', 'roboco-panel')

        Returns:
            Project details including commands and config
        """
        return await _handle_project_get(client, slug)

    @mcp.tool()
    async def roboco_workspace_ensure(project_slug: str) -> dict[str, Any]:
        """
        Ensure a workspace exists for you on this project.

        Creates the workspace by cloning the repository if needed.
        This is typically called automatically, but can be used manually.

        Args:
            project_slug: Project to create workspace for

        Returns:
            Workspace path and status including full filesystem path
        """
        return await _handle_workspace_ensure(client, project_slug, agent_id)

    @mcp.tool()
    async def roboco_workspace_status(project_slug: str) -> dict[str, Any]:
        """
        Get your workspace status for a project.

        Returns whether workspace exists, current branch, changes, and your
        full filesystem path for direct file access.

        Args:
            project_slug: Project to check workspace for

        Returns:
            Workspace status including path
        """
        return await _handle_workspace_status(client, project_slug, agent_id)


def _register_privileged_project_tools(
    mcp: FastMCP, client: ApiClient, agent_id: str
) -> None:
    """Register privileged project tools gated by role."""
    if can_create_projects(agent_id):

        @mcp.tool()
        async def roboco_project_create(data: ProjectCreateInput) -> dict[str, Any]:
            """
            Register a new project (Main PM, Board, CEO only).

            Creates a project record for a git repository.
            Workspaces are created automatically when agents need them.

            Args:
                data: Project creation data including name, slug, git_url, cell

            Returns:
                Created project details
            """
            return await _handle_project_create(client, agent_id, data)

    if _can_update_project_tool(agent_id):

        @mcp.tool()
        async def roboco_project_update(
            slug: str, data: ProjectUpdateInput
        ) -> dict[str, Any]:
            """
            Update a project (PM only).

            Cell PMs can only update projects in their cell.
            Main PM and above can update any project.

            Args:
                slug: Project slug to update
                data: Fields to update (only non-None fields are changed)

            Returns:
                Updated project details
            """
            return await _handle_project_update(client, agent_id, slug, data)

    if _can_list_workspaces_tool(agent_id):

        @mcp.tool()
        async def roboco_workspace_list(project_slug: str) -> dict[str, Any]:
            """
            List all agent workspaces for a project (PM only).

            Cell PMs see only their cell's workspaces.
            Main PM and above see all workspaces.

            Args:
                project_slug: Project to list workspaces for

            Returns:
                List of workspaces with team, agent, and status
            """
            return await _handle_workspace_list(client, agent_id, project_slug)


def create_project_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Project MCP server for a specific agent.

    Args:
        agent_id: The agent identifier (e.g., "be-pm")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-project-{agent_id}", json_response=True)
    client = ApiClient(agent_id)

    _register_common_project_tools(mcp, client, agent_id)
    _register_privileged_project_tools(mcp, client, agent_id)

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    MIN_ARGS = 2
    if len(sys.argv) < MIN_ARGS:
        print("Usage: python project_server.py <agent_id>")
        sys.exit(1)

    agent_id_arg = sys.argv[1]
    server = create_project_mcp_server(agent_id_arg)
    server.run()
