"""PR internal template builder coverage."""

from __future__ import annotations

from roboco.templates.git.pr_internal import (
    InternalCommitInfo,
    InternalPRContext,
    _format_commits_list,
    _format_qa_section,
    build_pr_body_internal,
    build_pr_title_internal,
)


def test_format_commits_list_empty() -> None:
    out = _format_commits_list([])
    assert "No commits" in out


def test_format_commits_list_with_data() -> None:
    commits = [
        InternalCommitInfo(hash="abcdef1234567890", message="fix bug"),
        InternalCommitInfo(hash="123456abcdef0000", message="add tests"),
    ]
    out = _format_commits_list(commits)
    assert "fix bug" in out
    assert "add tests" in out


def test_format_qa_section_pending() -> None:
    out = _format_qa_section(None, qa_passed=False)
    assert "Pending QA" in out


def test_format_qa_section_passed_no_notes() -> None:
    out = _format_qa_section(None, qa_passed=True)
    assert "QA Passed" in out


def test_format_qa_section_passed_with_notes() -> None:
    out = _format_qa_section("Looks great", qa_passed=True)
    assert "QA Passed" in out
    assert "Looks great" in out


def test_build_pr_body_internal_minimal() -> None:
    ctx = InternalPRContext(
        task_id="task-123",
        task_title="Sub task X",
        task_description="Description",
        task_status="completed",
        task_assigned_to="be-dev-1",
        parent_task_id=None,
        parent_task_title=None,
        source_branch="feature/backend/abc12345",
        target_branch="feature/backend/parent",
    )
    body = build_pr_body_internal(ctx, "http://localhost/api")
    assert "Sub task X" in body
    assert "## Summary" in body


def test_build_pr_body_internal_with_parent() -> None:
    ctx = InternalPRContext(
        task_id="task-123",
        task_title="Sub task",
        task_description="d",
        task_status="completed",
        task_assigned_to=None,
        parent_task_id="parent-456",
        parent_task_title="Parent Task",
        source_branch="src",
        target_branch="dst",
        session_id="sess-1",
    )
    body = build_pr_body_internal(ctx, "http://api")
    assert "parent-456" in body
    assert "Parent Task" in body
    assert "sess-1" in body


def test_build_pr_title_internal() -> None:
    ctx = InternalPRContext(
        task_id="abcdef12345678",
        task_title="My Task",
        task_description="d",
        task_status="completed",
        task_assigned_to=None,
        parent_task_id=None,
        parent_task_title=None,
        source_branch="src",
        target_branch="dst",
    )
    title = build_pr_title_internal(ctx)
    assert "My Task" in title
    assert title.startswith("[")


def test_internal_commit_info_dataclass() -> None:
    c = InternalCommitInfo(hash="abc", message="msg")
    assert c.hash == "abc"
    assert c.message == "msg"


def test_internal_pr_context_defaults() -> None:
    ctx = InternalPRContext(
        task_id="t",
        task_title="t",
        task_description="d",
        task_status="s",
        task_assigned_to=None,
        parent_task_id=None,
        parent_task_title=None,
        source_branch="src",
        target_branch="dst",
    )
    assert ctx.commits == []
    assert ctx.qa_passed is False
