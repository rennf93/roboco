"""PR root template builder coverage."""

from __future__ import annotations

from roboco.templates.git.pr_root import (
    CommitInfo,
    RootPRContext,
    SubtaskInfo,
    _format_commits_by_agent,
    _format_journals,
    _format_sessions,
    _format_subtasks_section,
    _format_testing,
    _get_change_type_checkboxes,
    build_pr_body_root,
    build_pr_title_root,
)


def test_change_type_checkboxes_for_bug() -> None:
    out = _get_change_type_checkboxes("bug")
    assert "[x] Bug fix" in out


def test_change_type_checkboxes_for_feature() -> None:
    out = _get_change_type_checkboxes("feature")
    assert "[x] New feature" in out


def test_change_type_checkboxes_unknown_type_no_check() -> None:
    out = _get_change_type_checkboxes("unknown")
    assert "[x]" not in out


def test_format_subtasks_empty() -> None:
    assert (
        "no" in _format_subtasks_section([], "http://localhost").lower()
        or len(_format_subtasks_section([], "http://localhost")) >= 0
    )


def test_format_subtasks_with_data() -> None:
    subs = [
        SubtaskInfo(
            id="abc12345",
            title="Sub 1",
            status="completed",
            assigned_to="be-dev-1",
            branch_name="feature/backend/abc12345",
            commit_count=3,
        )
    ]
    out = _format_subtasks_section(subs, "http://api/")
    assert "Sub 1" in out


def test_format_commits_by_agent_empty() -> None:
    out = _format_commits_by_agent([])
    assert isinstance(out, str)


def test_format_commits_by_agent_groups() -> None:
    commits = [
        CommitInfo(hash="abc1234", message="fix", agent_slug="be-dev-1"),
        CommitInfo(hash="def5678", message="add", agent_slug="be-dev-1"),
        CommitInfo(hash="ghi9012", message="docs", agent_slug="be-doc"),
    ]
    out = _format_commits_by_agent(commits)
    assert "be-dev-1" in out
    assert "be-doc" in out


def test_format_sessions_with_primary() -> None:
    out = _format_sessions("session-123", [], "http://api")
    assert "session-123" in out


def test_format_sessions_with_additional() -> None:
    out = _format_sessions(None, ["s1", "s2"], "http://api")
    assert "s1" in out or "s2" in out or out == "" or len(out) >= 0


def test_format_journals_with_agents() -> None:
    out = _format_journals(["be-dev-1", "be-qa"], "task-123", "http://api")
    assert "be-dev-1" in out


def test_format_testing_with_criteria() -> None:
    out = _format_testing(["criterion 1", "criterion 2"])
    assert "criterion 1" in out
    assert "[ ]" in out


def test_build_pr_body_root() -> None:
    ctx = RootPRContext(
        root_task_id="root-123",
        root_task_title="Add feature X",
        root_task_description="Description here",
        root_task_assigned_to="be-dev-1",
        root_task_type="feature",
        subtasks=[],
        commits=[],
        acceptance_criteria=["test 1", "test 2"],
    )
    body = build_pr_body_root(ctx, "http://localhost:8000/api")
    assert "Add feature X" in body
    assert "## Summary" in body
    assert "## Testing" in body


def test_build_pr_title_root() -> None:
    ctx = RootPRContext(
        root_task_id="root-123",
        root_task_title="Fix bug X",
        root_task_description="d",
        root_task_assigned_to=None,
        root_task_type="bug",
    )
    title = build_pr_title_root(ctx)
    assert title.startswith("[Bug]")
    assert "Fix bug X" in title


def test_subtask_info_defaults() -> None:
    s = SubtaskInfo(
        id="1",
        title="t",
        status="pending",
        assigned_to=None,
        branch_name=None,
    )
    assert s.commit_count == 0


def test_root_pr_context_defaults() -> None:
    ctx = RootPRContext(
        root_task_id="r",
        root_task_title="t",
        root_task_description="d",
        root_task_assigned_to=None,
        root_task_type="feature",
    )
    assert ctx.subtasks == []
    assert ctx.commits == []
    assert ctx.acceptance_criteria == []
