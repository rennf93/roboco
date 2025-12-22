"""
Task MCP Server Session Handlers

PM-specific session-task handlers for the Task MCP server.
Enables PMs to create work sessions linked to tasks.
"""

from typing import Any

from fastapi import status

from roboco.agents_config import (
    can_create_tasks,
    get_agent_role,
    get_agent_team,
)
from roboco.mcp.schemas import SessionCreateForTasksInput, SessionLinkTaskInput
from roboco.mcp.utils import ApiClient, format_error_response

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _validate_pm_permissions(agent_id: str) -> dict[str, Any] | None:
    """Validate agent has PM permissions. Returns error or None."""
    if not can_create_tasks(agent_id):
        return format_error_response(
            "PERMISSION_DENIED",
            "Only PMs and management can manage session-task links",
            {"role": get_agent_role(agent_id)},
        )
    return None


def _validate_channel_access(agent_id: str, channel_slug: str) -> dict[str, Any] | None:
    """Validate Cell PM channel restrictions. Returns error or None."""
    role = get_agent_role(agent_id)
    agent_team = get_agent_team(agent_id)

    if role != "cell_pm":
        return None  # Main PM and board can access any channel

    # Cell PM channel restrictions
    team_channels = {
        "backend": ["backend-cell"],
        "frontend": ["frontend-cell"],
        "ux_ui": ["uxui-cell"],
    }

    allowed = team_channels.get(agent_team or "", [])
    if channel_slug not in allowed:
        return format_error_response(
            "CHANNEL_ACCESS_DENIED",
            "Cell PM can only create sessions in their team channel",
            {"channel": channel_slug, "allowed": allowed},
        )

    return None


def _format_session_response(
    session: dict[str, Any],
    links: list[dict[str, Any]],
    status_code: str,
    guidance: str,
) -> dict[str, Any]:
    """Format session response with guidance."""
    return {
        "status": status_code,
        "session": session,
        "task_links": links,
        "guidance": guidance,
    }


# =============================================================================
# SESSION HANDLERS
# =============================================================================


async def handle_session_create_for_tasks(
    client: ApiClient,
    input_data: SessionCreateForTasksInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle session creation linked to tasks (PM only)."""
    if error := _validate_pm_permissions(agent_id):
        return error

    if error := _validate_channel_access(agent_id, input_data.channel_slug):
        return error

    payload = {
        "task_ids": input_data.task_ids,
        "channel_slug": input_data.channel_slug,
        "scope": input_data.scope,
        "relationship_type": input_data.relationship_type,
    }

    try:
        resp = await client.post("/sessions/for-tasks", json=payload)
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if not resp.is_status(status.HTTP_201_CREATED):
        return format_error_response(
            "CREATE_FAILED",
            "Failed to create session for tasks",
            {"status_code": resp.status_code, "detail": resp.text},
        )

    data = resp.json()
    session = data.get("session", {})
    links = data.get("links", [])

    guidance = (
        f"Work session created. Session ID: {session.get('id', 'unknown')}. "
        f"Linked to {len(links)} task(s). "
        f"First task is marked as primary. "
        "Assigned agents can now discuss in this session."
    )

    return _format_session_response(session, links, "CREATED", guidance)


async def handle_session_link_task(
    client: ApiClient,
    input_data: SessionLinkTaskInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle linking a session to a task (PM only)."""
    if error := _validate_pm_permissions(agent_id):
        return error

    payload = {
        "task_id": input_data.task_id,
        "is_primary": input_data.is_primary,
        "relationship_type": input_data.relationship_type,
    }

    try:
        resp = await client.post(
            f"/sessions/{input_data.session_id}/tasks", json=payload
        )
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if resp.is_status(status.HTTP_409_CONFLICT):
        return format_error_response(
            "ALREADY_LINKED",
            "Session is already linked to this task",
            {"session_id": input_data.session_id, "task_id": input_data.task_id},
        )

    if not resp.ok:
        return format_error_response(
            "LINK_FAILED",
            "Failed to link session to task",
            {"status_code": resp.status_code, "detail": resp.text},
        )

    link = resp.json()
    primary_note = " (marked as primary)" if input_data.is_primary else ""

    return {
        "status": "LINKED",
        "link": link,
        "guidance": (
            f"Session linked to task{primary_note}. "
            "Task's assigned agent can now access this session."
        ),
    }


async def handle_session_unlink_task(
    client: ApiClient,
    session_id: str,
    task_id: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle unlinking a session from a task (PM only)."""
    if error := _validate_pm_permissions(agent_id):
        return error

    try:
        resp = await client.delete(f"/sessions/{session_id}/tasks/{task_id}")
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response(
            "NOT_FOUND",
            "Session-task link not found",
            {"session_id": session_id, "task_id": task_id},
        )

    if not resp.ok:
        return format_error_response(
            "UNLINK_FAILED",
            "Failed to unlink session from task",
            {"status_code": resp.status_code, "detail": resp.text},
        )

    return {
        "status": "UNLINKED",
        "guidance": "Session unlinked from task. Task agent no longer has access.",
    }


async def handle_session_get_for_task(
    client: ApiClient,
    task_id: str,
    _agent_id: str,  # Kept for handler signature consistency
) -> dict[str, Any]:
    """Handle getting sessions for a task."""
    # Any agent can query sessions for tasks they have access to
    try:
        resp = await client.get(f"/tasks/{task_id}/sessions")
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", f"Task {task_id} not found")

    if not resp.ok:
        return format_error_response(
            "FETCH_FAILED",
            "Failed to fetch sessions for task",
            {"status_code": resp.status_code, "detail": resp.text},
        )

    data = resp.json()
    sessions = data.get("sessions", [])
    primary = next((s for s in sessions if s.get("is_primary")), None)

    guidance = f"Found {len(sessions)} session(s) for this task."
    if primary:
        guidance += f" Primary session: {primary.get('session_id', 'unknown')}."
    else:
        guidance += " No primary session set."

    return {
        "status": "OK",
        "sessions": sessions,
        "primary_session_id": primary.get("session_id") if primary else None,
        "guidance": guidance,
    }
