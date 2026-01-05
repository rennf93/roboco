"""
Task Server Utilities

Shared utilities for task server operations including response formatting
and status guidance.
"""

from typing import Any

from roboco.llm import ToonAdapter

# Global TOON adapter for encoding task data
_toon = ToonAdapter()

# Progress percentage bounds
MIN_PERCENTAGE = 0
MAX_PERCENTAGE = 100


def format_task_response(
    task: dict[str, Any],
    next_step: str,
    guidance: str,
    project: dict[str, Any] | None = None,
    a2a_suggestion: str | None = None,
) -> dict[str, Any]:
    """
    Format a standardized task response with guidance.

    Includes both JSON task data and TOON-encoded version for
    token-efficient LLM consumption.

    Args:
        task: Task data dictionary
        next_step: Next workflow step identifier
        guidance: Human-readable guidance text
        project: Optional project data
        a2a_suggestion: Optional A2A communication suggestion for handoffs
    """
    # Encode task data as TOON for token efficiency when LLM processes response
    task_toon = _toon.encode(task)

    response = {
        "status": task.get("status"),
        "task": task,
        "task_toon": task_toon,  # TOON-encoded for LLM token efficiency
        "next_step": next_step,
        "guidance": guidance,
    }
    if project:
        response["project"] = project
        response["project_toon"] = _toon.encode(project)
    if a2a_suggestion:
        response["a2a_suggestion"] = a2a_suggestion
    return response


def get_next_step_guidance(status: str) -> tuple[str, str]:
    """Get next step and guidance based on task status."""
    guidance_map = {
        "claimed": (
            "UNDERSTAND",
            "Read the task description and acceptance criteria carefully. "
            "If anything is unclear, ask questions before proceeding. "
            "Do NOT start coding until you fully understand the requirements.",
        ),
        "in_progress": (
            "EXECUTE",
            "Work through your plan step by step. "
            "Commit frequently with clear messages. "
            "Update progress regularly. "
            "If blocked, call roboco_task_block with details.",
        ),
        "blocked": (
            "RESOLVE_BLOCKER",
            "Task is blocked. Options: "
            "1) UNBLOCK - If resolved, call roboco_task_unblock() to resume. "
            "2) WAIT - If waiting for external resolution. "
            "3) SWITCH - Call roboco_task_scan for other work. "
            "4) ESCALATE - If urgent, message your PM.",
        ),
        "paused": (
            "RESUME_OR_SCAN",
            "This task is paused. "
            "Call roboco_task_start to resume, or "
            "call roboco_task_scan for other work.",
        ),
        "verifying": (
            "SELF_VERIFY",
            "Verify against ALL acceptance criteria. "
            "Run tests, check edge cases. "
            "If all pass, call roboco_task_submit_qa. "
            "If issues found, fix them first.",
        ),
        "awaiting_qa": (
            "WAIT_FOR_QA",
            "Task submitted for QA review. "
            "You will be notified of the result. "
            "In the meantime, scan for other available work.",
        ),
        "needs_revision": (
            "FIX_ISSUES",
            "QA found issues. Read the QA notes carefully. "
            "Call roboco_task_start() to resume work, fix all issues, "
            "then re-submit for QA with roboco_task_submit_qa().",
        ),
        "awaiting_documentation": (
            "DOCUMENT",
            "QA passed. Create handoff documentation for the documenter. "
            "Include: what was built, how it works, any gotchas.",
        ),
        "completed": (
            "DONE",
            "Task completed. Call roboco_task_scan for new work.",
        ),
    }
    return guidance_map.get(status, ("UNKNOWN", "Check task status."))


def get_available_tasks_guidance(
    available_tasks: list[dict[str, Any]], agent_role: str
) -> str:
    """Generate guidance for available tasks based on agent role."""
    review_count = sum(
        1 for t in available_tasks if t.get("status") == "awaiting_pm_review"
    )
    pending_count = len(available_tasks) - review_count

    if agent_role in ("cell_pm", "main_pm") and review_count > 0:
        return (
            f"Found {review_count} task(s) awaiting your review. "
            "Use roboco_task_get to review, then roboco_task_complete to finalize. "
            f"Also {pending_count} pending task(s) need triage."
        )
    return (
        f"Found {len(available_tasks)} available task(s). "
        "Review and claim one that matches your skills."
    )
