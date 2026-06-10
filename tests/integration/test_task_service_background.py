"""TaskService coverage — background indexing + completion learning hooks.

These hooks fire-and-forget after lifecycle transitions (complete, soft_block,
unblock, pause, resume, fail_qa, pass_qa, cancel). The methods themselves
are simple wrappers over the optimal/learning services; the production paths
just need the methods to be invoked once and not raise.

Strategy: monkeypatch `get_optimal_service` / `get_learning_service` to
return AsyncMocks so the methods run their full bodies, then explicitly
await the awaitable returned by the asyncio.create_task wrapper to ensure
the body executes synchronously within the test's event loop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, WorkSessionTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.task import TaskCreateRequest
from roboco.models.work_session import WorkSessionStatus
from roboco.services.learning import LearningScope
from roboco.services.task import (
    TaskService,
    _CompletionSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def task_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
        "db": db_session,
    }


def _req(setup: dict, **overrides) -> TaskCreateRequest:
    return TaskCreateRequest(
        title=overrides.pop("title", "t"),
        description=overrides.pop("description", "d"),
        acceptance_criteria=overrides.pop("acceptance_criteria", ["ac"]),
        team=overrides.pop("team", Team.BACKEND),
        created_by=setup["agent_id"],
        project_id=setup["project_id"],
        task_type=overrides.pop("task_type", TaskType.CODE),
        nature=overrides.pop("nature", TaskNature.TECHNICAL),
        estimated_complexity=overrides.pop("estimated_complexity", Complexity.MEDIUM),
        **overrides,
    )


# ---------------------------------------------------------------------------
# _duration_learning
# ---------------------------------------------------------------------------


def test_duration_learning_returns_none_when_missing_timestamps(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    out = svc._duration_learning(
        "title", started_at=None, completed_at=None, estimated_complexity="low"
    )
    assert out is None


def test_duration_learning_emits_for_overrun(task_setup: dict) -> None:
    svc = task_setup["svc"]
    started = datetime.now(UTC)
    completed = started + timedelta(hours=20)  # >>> 2h expected for low
    out = svc._duration_learning(
        "title", started_at=started, completed_at=completed, estimated_complexity="low"
    )
    assert out is not None
    msg, _ltype = out
    assert "took" in msg.lower()


def test_duration_learning_emits_for_underrun(task_setup: dict) -> None:
    svc = task_setup["svc"]
    started = datetime.now(UTC)
    completed = started + timedelta(minutes=20)  # << 24h for high
    out = svc._duration_learning(
        "title", started_at=started, completed_at=completed, estimated_complexity="high"
    )
    assert out is not None
    msg, _ = out
    assert "quickly" in msg.lower()


def test_duration_learning_handles_complexity_with_value(task_setup: dict) -> None:
    """Complexity enum has .value attribute — code path covered."""
    svc = task_setup["svc"]
    started = datetime.now(UTC)
    completed = started + timedelta(hours=20)
    out = svc._duration_learning(
        "title",
        started_at=started,
        completed_at=completed,
        estimated_complexity=Complexity.LOW,
    )
    assert out is not None


def test_duration_learning_returns_none_for_normal_duration(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    started = datetime.now(UTC)
    completed = started + timedelta(hours=8)  # exactly expected for medium
    out = svc._duration_learning(
        "title",
        started_at=started,
        completed_at=completed,
        estimated_complexity="medium",
    )
    assert out is None


# ---------------------------------------------------------------------------
# _commit_pattern_learning
# ---------------------------------------------------------------------------


def test_commit_pattern_learning_pattern_for_5plus(task_setup: dict) -> None:
    svc = task_setup["svc"]
    out = svc._commit_pattern_learning("title", commits=[1, 2, 3, 4, 5])
    assert out is not None
    msg, _ = out
    assert "Good commit granularity" in msg


def test_commit_pattern_learning_gotcha_for_one(task_setup: dict) -> None:
    svc = task_setup["svc"]
    out = svc._commit_pattern_learning("title", commits=[1])
    assert out is not None
    msg, _ = out
    assert "Single commit" in msg


def test_commit_pattern_learning_returns_none_for_middle(task_setup: dict) -> None:
    svc = task_setup["svc"]
    out = svc._commit_pattern_learning("title", commits=[1, 2, 3])
    assert out is None


# ---------------------------------------------------------------------------
# _collect_completion_learnings
# ---------------------------------------------------------------------------


def test_collect_completion_learnings_aggregates_all(task_setup: dict) -> None:
    svc = task_setup["svc"]
    started = datetime.now(UTC)
    completed = started + timedelta(hours=20)
    snapshot = _CompletionSnapshot(
        task_title="big task",
        started_at=started,
        completed_at=completed,
        estimated_complexity="low",
        commits=[1, 2, 3, 4, 5, 6],
        dev_notes="A" * 100,
        qa_notes="B" * 100,
    )
    learnings = svc._collect_completion_learnings(snapshot)
    # 4 entries: duration + commit pattern + dev notes + qa notes
    expected_count = 4
    assert len(learnings) == expected_count


def test_collect_completion_learnings_empty_for_no_data(task_setup: dict) -> None:
    svc = task_setup["svc"]
    snapshot = _CompletionSnapshot(
        task_title="x",
        started_at=None,
        completed_at=None,
        estimated_complexity="low",
        commits=[],
        dev_notes=None,
        qa_notes=None,
    )
    assert svc._collect_completion_learnings(snapshot) == []


# ---------------------------------------------------------------------------
# _determine_learning_scope
# ---------------------------------------------------------------------------


def test_determine_learning_scope_for_cell_team(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert svc._determine_learning_scope("backend") == LearningScope.CELL


def test_determine_learning_scope_for_org_team(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert svc._determine_learning_scope("board") == LearningScope.ORG


def test_determine_learning_scope_default_team(task_setup: dict) -> None:
    svc = task_setup["svc"]
    assert svc._determine_learning_scope("unknown") == LearningScope.TEAM


# ---------------------------------------------------------------------------
# _extract_decisions_from_notes
# ---------------------------------------------------------------------------


def test_extract_decisions_finds_pattern(task_setup: dict) -> None:
    svc = task_setup["svc"]
    decisions = svc._extract_decisions_from_notes(
        "We decided to use Postgres. The reasoning was clear.",
        "task title",
    )
    assert len(decisions) >= 1
    assert any("decided to" in d["decision"].lower() for d in decisions)


def test_extract_decisions_returns_empty_no_match(task_setup: dict) -> None:
    svc = task_setup["svc"]
    decisions = svc._extract_decisions_from_notes(
        "Plain text with nothing decision-like.", "title"
    )
    assert decisions == []


# ---------------------------------------------------------------------------
# _parse_qa_notes
# ---------------------------------------------------------------------------


def test_parse_qa_notes_with_bullets(task_setup: dict) -> None:
    svc = task_setup["svc"]
    issues = svc._parse_qa_notes("- Issue one\n* Issue two\n• Issue three")
    expected_count = 3
    assert len(issues) == expected_count


def test_parse_qa_notes_with_numbered_list(task_setup: dict) -> None:
    svc = task_setup["svc"]
    issues = svc._parse_qa_notes("1. First issue\n2. Second issue")
    expected_count = 2
    assert len(issues) == expected_count


def test_parse_qa_notes_falls_back_to_raw(task_setup: dict) -> None:
    svc = task_setup["svc"]
    issues = svc._parse_qa_notes("Free-form note without bullets")
    assert len(issues) == 1
    assert "Free-form" in issues[0]["description"]


def test_parse_qa_notes_empty_for_blank(task_setup: dict) -> None:
    svc = task_setup["svc"]
    issues = svc._parse_qa_notes("")
    assert issues == []


# ---------------------------------------------------------------------------
# _extract_completion_learnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_completion_learnings_calls_record(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.started_at = datetime.now(UTC) - timedelta(hours=20)
    task.completed_at = datetime.now(UTC)
    task.estimated_complexity = Complexity.LOW
    task.commits = [{"hash": f"h{i}"} for i in range(7)]
    task.dev_notes = "D" * 100
    task.qa_notes = "Q" * 100
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    record_mock = AsyncMock()
    fake_learning_svc = MagicMock()
    fake_learning_svc.record_learning = record_mock

    async def _get_learning_service() -> Any:
        return fake_learning_svc

    monkeypatch.setattr(
        "roboco.services.learning.get_learning_service",
        _get_learning_service,
    )
    await svc._extract_completion_learnings(task, task_setup["agent_id"])
    assert record_mock.await_count > 0


@pytest.mark.asyncio
async def test_extract_completion_learnings_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure in learning_svc should not raise."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))

    async def _bad() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("roboco.services.learning.get_learning_service", _bad)
    # Should not raise
    await svc._extract_completion_learnings(task, task_setup["agent_id"])


# ---------------------------------------------------------------------------
# _index_code_changes_background
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_code_changes_with_files(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_optimal = MagicMock()
    fake_optimal.index_code = AsyncMock(return_value=2)

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    commits = [{"files": ["a.py", "b.py"]}, {"files": ["a.py"]}]
    await svc._index_code_changes_background(uuid4(), commits, "roboco")
    fake_optimal.index_code.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_code_changes_with_no_files(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_optimal = MagicMock()
    fake_optimal.index_code = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    # Empty list of files
    await svc._index_code_changes_background(uuid4(), [], "roboco")
    fake_optimal.index_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_index_code_changes_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]

    async def _bad() -> None:
        raise RuntimeError("optimal down")

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _bad)
    # Should not raise
    await svc._index_code_changes_background(uuid4(), [{"files": ["a.py"]}], "p")


# ---------------------------------------------------------------------------
# _index_decisions_background
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_decisions_no_notes_returns_early(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    # No exception even if optimal isn't patched — early return
    await svc._index_decisions_background(uuid4(), "title", Team.BACKEND, None, None)


@pytest.mark.asyncio
async def test_index_decisions_with_notes_calls_index(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_optimal = MagicMock()
    fake_optimal.index_decision = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    notes = "We decided to refactor the auth flow. Reasoning: it was buggy."
    await svc._index_decisions_background(
        uuid4(), "title", Team.BACKEND, notes, uuid4()
    )
    fake_optimal.index_decision.assert_awaited()


@pytest.mark.asyncio
async def test_index_decisions_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]

    async def _bad() -> None:
        raise RuntimeError("oops")

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _bad)
    # Should not raise
    await svc._index_decisions_background(
        uuid4(),
        "title",
        Team.BACKEND,
        "decided to do thing",
        None,
    )


# ---------------------------------------------------------------------------
# _index_docs_background
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_docs_with_paths_calls_index(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_optimal = MagicMock()
    fake_optimal.index_documentation = AsyncMock(return_value=2)

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    docs = [{"path": "doc1.md"}, {"path": "doc2.md"}]
    await svc._index_docs_background(uuid4(), docs)
    fake_optimal.index_documentation.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_docs_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]

    async def _bad() -> None:
        raise RuntimeError("oops")

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _bad)
    # Should not raise
    await svc._index_docs_background(uuid4(), [{"path": "x.md"}])


@pytest.mark.asyncio
async def test_capture_workspace_docs_lands_missing_doc(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A doc that lives only in the agent's clone is read out of the branch and
    written under DOCS_BASE_PATH so the indexer can see it."""
    svc = task_setup["svc"]
    monkeypatch.setattr("roboco.services.docs.DOCS_BASE_PATH", tmp_path)
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/abc"
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    fake_git = MagicMock()
    fake_git.read_file_at_branch = AsyncMock(return_value="# Guide\ncontent\n")
    monkeypatch.setattr("roboco.services.git.get_git_service", lambda _s: fake_git)

    await svc._capture_workspace_docs(
        task.id, [{"path": "guide.md"}], task_setup["agent_id"]
    )

    assert (tmp_path / "guide.md").read_text(encoding="utf-8") == "# Guide\ncontent\n"
    fake_git.read_file_at_branch.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_workspace_docs_skips_doc_already_on_server(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A doc already under DOCS_BASE_PATH (written via roboco_docs_write) is not
    re-fetched from the branch."""
    svc = task_setup["svc"]
    monkeypatch.setattr("roboco.services.docs.DOCS_BASE_PATH", tmp_path)
    (tmp_path / "api.md").write_text("already here", encoding="utf-8")
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/abc"
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    fake_git = MagicMock()
    fake_git.read_file_at_branch = AsyncMock(return_value="should not be used")
    monkeypatch.setattr("roboco.services.git.get_git_service", lambda _s: fake_git)

    await svc._capture_workspace_docs(
        task.id, [{"path": "api.md"}], task_setup["agent_id"]
    )

    assert (tmp_path / "api.md").read_text(encoding="utf-8") == "already here"
    fake_git.read_file_at_branch.assert_not_awaited()


# ---------------------------------------------------------------------------
# _index_qa_review_background / _index_qa_errors_background
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_qa_review_calls_record_review(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_optimal = MagicMock()
    fake_optimal.record_review = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    dev_id = uuid4()
    await svc._index_qa_review_background(
        uuid4(),
        f"original_developer:{dev_id}",
        passed=True,
        qa_notes="LGTM",
        qa_agent_id=uuid4(),
    )
    fake_optimal.record_review.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_qa_review_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]

    async def _bad() -> None:
        raise RuntimeError("oops")

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _bad)
    await svc._index_qa_review_background(
        uuid4(), None, passed=False, qa_notes="x", qa_agent_id=None
    )


@pytest.mark.asyncio
async def test_index_qa_errors_calls_index_error(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_optimal = MagicMock()
    fake_optimal.index_error = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    await svc._index_qa_errors_background(
        uuid4(),
        "task title",
        Team.BACKEND,
        "- one\n- two",
    )
    assert fake_optimal.index_error.await_count >= 1


@pytest.mark.asyncio
async def test_index_qa_errors_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]

    async def _bad() -> None:
        raise RuntimeError("oops")

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _bad)
    await svc._index_qa_errors_background(uuid4(), "t", Team.BACKEND, "notes")


# ---------------------------------------------------------------------------
# _index_blocker_background
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_blocker_calls_index_error(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_optimal = MagicMock()
    fake_optimal.index_error = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    await svc._index_blocker_background(
        uuid4(),
        Team.BACKEND,
        {
            "type": "external",
            "title": "title",
            "reason": "reason",
            "what_needed": "key",
        },
    )
    fake_optimal.index_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_blocker_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]

    async def _bad() -> None:
        raise RuntimeError("oops")

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _bad)
    await svc._index_blocker_background(uuid4(), Team.BACKEND, {})


# ---------------------------------------------------------------------------
# _index_lifecycle_event_background
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_lifecycle_event_calls_index_journal(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_optimal = MagicMock()
    fake_optimal.index_journal_entry = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    await svc._index_lifecycle_event_background(
        uuid4(),
        "block",
        "title",
        Team.BACKEND,
        details={"reason": "x"},
    )
    fake_optimal.index_journal_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_lifecycle_event_no_team(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_optimal = MagicMock()
    fake_optimal.index_journal_entry = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    await svc._index_lifecycle_event_background(
        uuid4(), "cancel", "title", task_team=None
    )
    fake_optimal.index_journal_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_lifecycle_event_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]

    async def _bad() -> None:
        raise RuntimeError("oops")

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _bad)
    await svc._index_lifecycle_event_background(
        uuid4(), "cancel", "title", task_team=None
    )


# ---------------------------------------------------------------------------
# _close_work_session_for_task / _delete_task_branch_best_effort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_work_session_for_task_no_session(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # Should be a no-op when no work_session_id
    await svc._close_work_session_for_task(task, reason="x")


@pytest.mark.asyncio
async def test_close_work_session_for_task_calls_close(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/x",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
    )
    db_session.add(ws)
    await db_session.flush()
    task.work_session_id = ws.id
    await db_session.flush()

    fake_ws_svc = MagicMock()
    fake_ws_svc.close = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.work_session.get_work_session_service",
        lambda _s: fake_ws_svc,
    )
    await svc._close_work_session_for_task(task, reason="completed")
    fake_ws_svc.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_task_branch_no_branch_no_op(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))  # no branch_name
    await svc._delete_task_branch_best_effort(task)


@pytest.mark.asyncio
async def test_delete_task_branch_no_project_returns(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """If project lookup yields None, returns silently.

    Patches the SELECT directly so the project_id FK on the task remains
    valid (we can't delete the project while the task still references it).
    """
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/AAAA"
    await db_session.flush()

    # Stub session.execute to return a result that scalars to None on the
    # ProjectTable lookup branch.
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = None

    real_execute = db_session.execute

    async def _execute_stub(stmt, *args, **kwargs):
        # Return None-result for ProjectTable.slug lookup (the SELECT in
        # _delete_task_branch_best_effort), forward everything else.
        compiled = str(stmt)
        if "projects.slug" in compiled.lower():
            return fake_result
        return await real_execute(stmt, *args, **kwargs)

    # Bind the stub
    object.__setattr__(
        svc, "session", MagicMock(execute=AsyncMock(side_effect=_execute_stub))
    )
    try:
        await svc._delete_task_branch_best_effort(task)
    finally:
        # Restore real session
        object.__setattr__(svc, "session", db_session)


@pytest.mark.asyncio
async def test_delete_task_branch_swallows_git_errors(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/x"
    await db_session.flush()

    fake_git = MagicMock()
    fake_git.delete_task_branch = AsyncMock(side_effect=RuntimeError("git fail"))
    monkeypatch.setattr("roboco.services.git.get_git_service", lambda _s: fake_git)
    # Should not raise
    await svc._delete_task_branch_best_effort(task)


# ---------------------------------------------------------------------------
# _abandon_work_session_for_task / _abandon_work_session_best_effort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abandon_work_session_for_task_no_session_is_noop(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # No work_session_id
    await svc._abandon_work_session_for_task(task, reason="x")


@pytest.mark.asyncio
async def test_abandon_work_session_for_task_invokes_abandon(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/x",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
    )
    db_session.add(ws)
    await db_session.flush()
    task.work_session_id = ws.id
    await db_session.flush()

    fake_ws = MagicMock()
    fake_ws.abandon = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.work_session.get_work_session_service",
        lambda _s: fake_ws,
    )
    await svc._abandon_work_session_for_task(task, reason="cancelled")
    fake_ws.abandon.assert_awaited_once()


@pytest.mark.asyncio
async def test_abandon_work_session_best_effort_swallows_errors(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    fake_ws = MagicMock()
    fake_ws.abandon = AsyncMock(side_effect=RuntimeError("ws fail"))
    monkeypatch.setattr(
        "roboco.services.work_session.WorkSessionService",
        lambda _s: fake_ws,
    )
    # Should not raise
    await svc._abandon_work_session_best_effort(uuid4(), reason="x")


# ---------------------------------------------------------------------------
# _trigger_completion_hooks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_completion_hooks_with_commits_and_notes(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.commits = [{"hash": "h", "files": ["a.py"]}]
    task.dev_notes = "decided to use postgres" * 5
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    fake_optimal = MagicMock()
    fake_optimal.index_code = AsyncMock(return_value=1)
    fake_optimal.index_decision = AsyncMock()

    async def _get_optimal() -> Any:
        return fake_optimal

    fake_learning = MagicMock()
    fake_learning.record_learning = AsyncMock()

    async def _get_learning() -> Any:
        return fake_learning

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    monkeypatch.setattr("roboco.services.learning.get_learning_service", _get_learning)
    # Just runs all 3 hook spawners
    await svc._trigger_completion_hooks(task, task_setup["agent_id"])


@pytest.mark.asyncio
async def test_trigger_completion_hooks_no_commits_no_notes(
    task_setup: dict,
    db_session: AsyncSession,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.commits = []
    task.dev_notes = None
    await db_session.flush()
    # Should still spawn the learnings hook only
    await svc._trigger_completion_hooks(task, task_setup["agent_id"])


# ---------------------------------------------------------------------------
# _assert_pr_merged_for_complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assert_pr_merged_no_session_returns_true(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    assert await svc._assert_pr_merged_for_complete(task) is True


@pytest.mark.asyncio
async def test_assert_pr_merged_with_merged_session(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/x",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
        pr_status="merged",
    )
    db_session.add(ws)
    await db_session.flush()
    task.work_session_id = ws.id
    await db_session.flush()
    assert await svc._assert_pr_merged_for_complete(task) is True


@pytest.mark.asyncio
async def test_assert_pr_merged_with_open_session_returns_false(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/x",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
        pr_status="open",
    )
    db_session.add(ws)
    await db_session.flush()
    task.work_session_id = ws.id
    await db_session.flush()
    assert await svc._assert_pr_merged_for_complete(task) is False


# ---------------------------------------------------------------------------
# ceo_approve with PR merged check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceo_approve_returns_none_when_pr_not_merged(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_CEO_APPROVAL
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/y",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
        pr_status="open",  # NOT merged
        pr_number=42,
    )
    db_session.add(ws)
    await db_session.flush()
    task.work_session_id = ws.id
    task.docs_complete = True
    task.pr_created = True
    task.pr_number = 42
    await db_session.flush()
    out = await svc.ceo_approve(task.id)
    assert out is None
