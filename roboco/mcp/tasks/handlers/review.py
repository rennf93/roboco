"""
Task Review Handlers

Handlers for task verification and QA review.
"""

from typing import Any

from roboco.agents_config import get_agent_role
from roboco.enforcement import can_review_task
from roboco.mcp.tasks import format_task_response
from roboco.mcp.tasks.handlers._helpers import (
    fetch_task_or_error,
    validate_task_ownership,
    validate_task_status,
    validate_task_status_in,
)
from roboco.mcp.utils import ApiClient, format_error_response, resolve_agent_uuid_cached
from roboco.services.task import extract_original_developer

# QA workflow statuses: awaiting_qa → claim → plan → start (in_progress) → verdict
QA_WORKFLOW_STATUSES = {"awaiting_qa", "claimed", "in_progress"}


def _validate_developer_role(agent_id: str) -> dict[str, Any] | None:
    """Validate agent is a developer (not PM/QA/Documenter). Returns error or None."""
    agent_role = get_agent_role(agent_id)
    if agent_role != "developer":
        return format_error_response(
            "NOT_DEVELOPER",
            "Only developers can submit work for verification/QA. "
            "PMs should use roboco_task_complete() directly.",
            {"your_role": agent_role, "allowed_roles": ["developer"]},
            hint="roboco_kb_search('developer workflow submit qa')",
        )
    return None


def _has_work_evidence(task: dict[str, Any]) -> bool:
    """Check if task has evidence of work done."""
    return bool(
        task.get("commits") or task.get("progress_updates") or task.get("checkpoints")
    )


def _build_verification_checklist(task: dict[str, Any]) -> str:
    """Build markdown checklist from acceptance criteria."""
    criteria = task.get("acceptance_criteria", [])
    return "\n".join(f"- [ ] {c}" for c in criteria)


async def _validate_verification_submission(
    client: ApiClient, task_id: str, agent_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate task for verification. Returns (task, None) or (None, error)."""
    # Only developers can submit for verification
    if error := _validate_developer_role(agent_id):
        return None, error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return None, error
    assert task is not None

    if error := await validate_task_ownership(task, agent_id, client):
        return None, error

    if error := validate_task_status(task, "in_progress", "submit for verification"):
        return None, error

    if not _has_work_evidence(task):
        return None, format_error_response(
            "NO_WORK_EVIDENCE",
            "No evidence of work found. Add commits with roboco_task_add_commit "
            "or update progress with roboco_task_progress before verification.",
            hint="roboco_kb_search('task progress commits')",
        )

    return task, None


async def handle_task_submit_verification(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task verification submission."""
    task, error = await _validate_verification_submission(client, task_id, agent_id)
    if error:
        return error
    assert task is not None

    verify_resp = await client.post(f"/tasks/{task_id}/verify")
    if not verify_resp.ok:
        return format_error_response(
            "VERIFY_FAILED", "Failed to submit for verification"
        )

    checklist = _build_verification_checklist(task)
    return format_task_response(
        verify_resp.json(),
        "VERIFY",
        f"Self-verify against acceptance criteria:\n\n{checklist}\n\n"
        "Check EACH criterion. Run tests. Check edge cases.\n"
        "When ALL pass, call roboco_task_submit_qa.\n"
        "If issues found, fix them and update progress.",
    )


def _validate_qa_notes(dev_notes: str, handoff_summary: str) -> dict[str, Any] | None:
    """Validate required QA submission notes. Returns error or None."""
    if not dev_notes or not handoff_summary:
        return format_error_response(
            "MISSING_NOTES",
            "Both dev_notes and handoff_summary are required for QA submission.",
        )
    return None


async def _validate_qa_submission(
    client: ApiClient, task_id: str, agent_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate task for QA submission. Returns (task, None) or (None, error)."""
    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return None, error
    assert task is not None

    if error := await validate_task_ownership(task, agent_id, client):
        return None, error

    if error := validate_task_status(task, "verifying", "submit for QA"):
        return None, error

    return task, None


async def _save_notes_and_submit(
    client: ApiClient, task_id: str, dev_notes: str, handoff_summary: str, agent_id: str
) -> dict[str, Any]:
    """Save notes and submit for QA. Returns response dict."""
    combined_notes = f"{dev_notes}\n\n---\nHandoff Summary:\n{handoff_summary}"
    notes_resp = await client.patch(
        f"/tasks/{task_id}", json={"dev_notes": combined_notes}
    )
    if not notes_resp.ok:
        return format_error_response(
            "NOTES_SAVE_FAILED",
            "Failed to save dev notes. QA submission aborted.",
        )

    qa_resp = await client.post(f"/tasks/{task_id}/submit-qa")
    if not qa_resp.ok:
        return format_error_response("SUBMIT_FAILED", "Failed to submit for QA")

    # Determine QA agent based on agent's team
    team_prefix = agent_id[:2] if agent_id else "be"
    qa_agent = f"{team_prefix}-qa"

    return format_task_response(
        qa_resp.json(),
        "WAIT_FOR_QA",
        "Task submitted for QA review.\n\n"
        "WHAT HAPPENS NEXT:\n"
        "- QA will review your work and either PASS or FAIL\n"
        "- If PASS: goes to documentation, then PM review\n"
        "- If FAIL: returns to you with feedback for revision\n\n"
        "Call roboco_task_scan for other work while waiting.",
        a2a_suggestion=(
            f"Notify QA directly for faster review:\n"
            f"roboco_agent_request(target_agent='{qa_agent}', "
            f"skill='qa_review', message='Task {task_id} ready for QA review')"
        ),
    )


async def handle_task_submit_qa(
    client: ApiClient,
    task_id: str,
    dev_notes: str,
    handoff_summary: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task QA submission."""
    # Only developers can submit for QA
    if error := _validate_developer_role(agent_id):
        return error

    if error := _validate_qa_notes(dev_notes, handoff_summary):
        return error

    _, error = await _validate_qa_submission(client, task_id, agent_id)
    if error:
        return error

    return await _save_notes_and_submit(
        client, task_id, dev_notes, handoff_summary, agent_id
    )


def _validate_qa_role(agent_id: str, action: str) -> dict[str, Any] | None:
    """Validate agent has QA role. Returns error or None."""
    agent_role = get_agent_role(agent_id)
    if agent_role != "qa":
        return format_error_response(
            "NOT_QA",
            f"Only QA agents can {action} tasks in QA review.",
            {"your_role": agent_role},
            hint="roboco_kb_search('qa workflow pass fail')",
        )
    return None


async def _check_self_review(
    task: dict[str, Any], agent_id: str, client: ApiClient
) -> dict[str, Any] | None:
    """Check if agent is reviewing their own work. Returns error or None."""
    quick_context = task.get("quick_context")
    original_dev = extract_original_developer(quick_context)
    agent_uuid = await resolve_agent_uuid_cached(agent_id, client)
    if agent_uuid and not can_review_task(agent_uuid, original_dev):
        return format_error_response(
            "SELF_REVIEW",
            "Cannot review your own work.",
            hint="roboco_kb_search('self review prevention')",
        )
    return None


async def handle_task_qa_pass(
    client: ApiClient, task_id: str, qa_notes: str, agent_id: str
) -> dict[str, Any]:
    """Handle task QA pass."""
    if error := _validate_qa_role(agent_id, "pass"):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    if error := validate_task_status_in(task, QA_WORKFLOW_STATUSES, "pass QA on"):
        return error

    if error := await _check_self_review(task, agent_id, client):
        return error

    pass_resp = await client.post(f"/tasks/{task_id}/pass-qa", json={"notes": qa_notes})
    if not pass_resp.ok:
        return format_error_response(
            "QA_FAILED",
            "Failed to pass QA",
            {"status_code": pass_resp.status_code, "api_error": pass_resp.text},
        )

    # Determine documenter based on QA's team
    team_prefix = agent_id[:2] if agent_id else "be"
    doc_agent = f"{team_prefix}-doc"

    return format_task_response(
        pass_resp.json(),
        "NOTIFY_DEV",
        "Task passed QA. Documenter will be notified.\n"
        "Call roboco_task_scan for next QA task.",
        a2a_suggestion=(
            f"Notify documenter directly:\n"
            f"roboco_agent_request(target_agent='{doc_agent}', "
            f"skill='documentation', message='Task {task_id} ready for docs')"
        ),
    )


def _validate_qa_issues(issues: list[str]) -> dict[str, Any] | None:
    """Validate QA failure issues list. Returns error or None."""
    if not issues:
        return format_error_response(
            "NO_ISSUES",
            "Must specify at least one issue when failing QA.",
        )
    return None


async def _validate_qa_fail_request(
    client: ApiClient, task_id: str, issues: list[str], agent_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate QA fail request. Returns (task, None) or (None, error)."""
    if error := _validate_qa_role(agent_id, "fail"):
        return None, error

    if error := _validate_qa_issues(issues):
        return None, error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return None, error
    assert task is not None

    if error := validate_task_status_in(task, QA_WORKFLOW_STATUSES, "fail QA on"):
        return None, error

    # Security: prevent QA from reviewing their own work
    if error := await _check_self_review(task, agent_id, client):
        return None, error

    return task, None


async def handle_task_qa_fail(
    client: ApiClient,
    task_id: str,
    qa_notes: str,
    issues: list[str],
    agent_id: str,
) -> dict[str, Any]:
    """Handle task QA failure."""
    task, error = await _validate_qa_fail_request(client, task_id, issues, agent_id)
    if error:
        return error
    assert task is not None

    full_notes = f"{qa_notes}\n\nIssues:\n" + "\n".join(f"- {i}" for i in issues)
    fail_resp = await client.post(
        f"/tasks/{task_id}/fail-qa", json={"notes": full_notes}
    )
    if not fail_resp.ok:
        return format_error_response(
            "QA_FAILED",
            "Failed to fail QA",
            {"status_code": fail_resp.status_code, "api_error": fail_resp.text},
        )

    # Get the original developer from quick_context
    quick_context = task.get("quick_context", "")
    original_dev = extract_original_developer(quick_context)
    issues_summary = "; ".join(issues[:3])  # First 3 issues for message

    return format_task_response(
        fail_resp.json(),
        "NOTIFY_DEV",
        f"Task returned for revision with {len(issues)} issue(s).\n"
        "Developer will be notified.\nCall roboco_task_scan for next QA task.",
        a2a_suggestion=(
            f"Notify developer immediately (urgent):\n"
            f"roboco_agent_request('{original_dev}', 'revision', "
            f"'QA failed: {issues_summary}', options={{'urgent': True}})"
        )
        if original_dev
        else None,
    )
