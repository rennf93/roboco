"""
Task MCP Handlers

Exports all task handlers for the Task MCP server.
"""

from roboco.mcp.tasks.handlers.blocking import (
    handle_task_block,
    handle_task_pause,
    handle_task_unblock,
)
from roboco.mcp.tasks.handlers.claim import handle_task_claim, handle_task_unclaim
from roboco.mcp.tasks.handlers.lifecycle import (
    handle_agent_idle,
    handle_ceo_approve,
    handle_ceo_reject,
    handle_docs_complete,
    handle_escalate_to_ceo,
    handle_pm_reject,
    handle_submit_pm_review,
    handle_task_cancel,
    handle_task_complete,
)
from roboco.mcp.tasks.handlers.management import (
    handle_task_activate,
    handle_task_assign,
    handle_task_create,
    handle_task_escalate,
)
from roboco.mcp.tasks.handlers.review import (
    handle_task_qa_fail,
    handle_task_qa_pass,
    handle_task_submit_qa,
    handle_task_submit_verification,
)
from roboco.mcp.tasks.handlers.scan import handle_task_get, handle_task_scan
from roboco.mcp.tasks.handlers.sessions import (
    handle_group_create,
    handle_session_create_for_tasks,
    handle_session_get_for_task,
    handle_session_link_task,
    handle_session_unlink_task,
)
from roboco.mcp.tasks.handlers.substitute import handle_task_substitute
from roboco.mcp.tasks.handlers.work import (
    handle_task_plan,
    handle_task_progress,
    handle_task_start,
)

__all__ = [
    "handle_agent_idle",
    "handle_ceo_approve",
    "handle_ceo_reject",
    "handle_docs_complete",
    "handle_escalate_to_ceo",
    "handle_group_create",
    "handle_pm_reject",
    "handle_session_create_for_tasks",
    "handle_session_get_for_task",
    "handle_session_link_task",
    "handle_session_unlink_task",
    "handle_submit_pm_review",
    "handle_task_activate",
    "handle_task_assign",
    "handle_task_block",
    "handle_task_cancel",
    "handle_task_claim",
    "handle_task_complete",
    "handle_task_create",
    "handle_task_escalate",
    "handle_task_get",
    "handle_task_pause",
    "handle_task_plan",
    "handle_task_progress",
    "handle_task_qa_fail",
    "handle_task_qa_pass",
    "handle_task_scan",
    "handle_task_start",
    "handle_task_submit_qa",
    "handle_task_submit_verification",
    "handle_task_substitute",
    "handle_task_unblock",
    "handle_task_unclaim",
]
