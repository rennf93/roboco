"""Supersede branch-cut async path: dispatcher gate, in-flight dedup, retry
backoff, and the workspace-decoupled contributor comment.

Covers the findings from the adversarial review of the supersede timeout fix:
F2 (double-spawn), F3 (retry/backoff model), F4 (dispatcher gate checks
branch_cut_failed), F5 (comment_pull_request never clones), F6 (tests).
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.runtime.orchestrator import (
    AgentOrchestrator,
    _is_branch_pending,
)
from roboco.services.git import GitService

_PR_NUM = 99
_PR_42 = 42
_ATTEMPTS = 3
_PRIOR_FAILURES = 2
_RETRY_TS = 12345.6

# ---------------------------------------------------------------------------
# F4 / F6(a): dispatcher gate skips branch_pending AND branch_cut_failed
# ---------------------------------------------------------------------------


def test_is_branch_pending_true_for_branch_pending() -> None:
    task: dict[str, Any] = {"orchestration_markers": {"branch_pending": True}}
    assert _is_branch_pending(task) is True


def test_is_branch_pending_true_for_branch_cut_failed() -> None:
    """F4: branch_cut_failed must also gate the dispatcher (a CEO-unblocked
    umbrella whose cut hasn't re-run yet must not be routed to Main PM)."""
    task: dict[str, Any] = {
        "orchestration_markers": {"branch_cut_failed": 3},
    }
    assert _is_branch_pending(task) is True


def test_is_branch_pending_false_when_clean() -> None:
    task: dict[str, Any] = {"orchestration_markers": {"other": 1}}
    assert _is_branch_pending(task) is False


def test_is_branch_pending_false_when_no_markers() -> None:
    task: dict[str, Any] = {"orchestration_markers": None}
    assert _is_branch_pending(task) is False


@pytest.mark.asyncio
async def test_dispatcher_skips_branch_pending_umbrella() -> None:
    """A branch_pending supersede umbrella must NOT be routed to Main PM."""
    tasks = [
        {
            "id": "A",
            "source": "external_pr_supersede",
            "assigned_to": None,
            "orchestration_markers": {"branch_pending": True},
        },
        {
            "id": "B",
            "source": "manual",
            "assigned_to": None,
            "orchestration_markers": None,
        },
    ]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="main-pm-1")
    stub._BOARD_AGENTS = frozenset()
    stub._route_unassigned_pm_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._dispatch_board_program_exploration = AsyncMock(return_value=False)

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    routed = [c.args[1]["id"] for c in stub._route_unassigned_pm_task.await_args_list]
    assert routed == ["B"]


@pytest.mark.asyncio
async def test_dispatcher_skips_branch_cut_failed_umbrella() -> None:
    """F4: a branch_cut_failed umbrella (e.g. CEO unblocked after exhaustion)
    must NOT be routed to Main PM until the sweep re-runs the cut."""
    tasks = [
        {
            "id": "A",
            "source": "external_pr_supersede",
            "assigned_to": None,
            "orchestration_markers": {"branch_cut_failed": 3},
        },
    ]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._is_paused = AsyncMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="main-pm-1")
    stub._BOARD_AGENTS = frozenset()
    stub._route_unassigned_pm_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()
    stub._dispatch_board_program_exploration = AsyncMock(return_value=False)

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._route_unassigned_pm_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# F2 / F6(b): sweep does not double-spawn for an in-flight umbrella
# ---------------------------------------------------------------------------


def _new_orchestrator() -> AgentOrchestrator:
    """A bare orchestrator instance (no __init__) so the real
    _reconcile_one_umbrella / _reconcile_umbrella_row / _parse_supersede_pr
    methods resolve normally instead of returning an unbound MagicMock that
    can't be awaited -- a plain MagicMock() stand-in only "worked" here
    because the old (pre-per-row-isolation) code's broad try/except quietly
    swallowed that TypeError and returned [], masking the bug."""
    return AgentOrchestrator.__new__(AgentOrchestrator)


@pytest.mark.asyncio
async def test_sweep_skips_in_flight_umbrella(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sweep must not spawn a second _cut_supersede_branch for an
    umbrella whose cut is already running."""
    umbrella_id = uuid4()
    orch = _new_orchestrator()
    orch._supersede_cuts_in_flight = {str(umbrella_id)}
    orch._bg_tasks = set()
    cut_mock = AsyncMock()
    monkeypatch.setattr(orch, "_cut_supersede_branch", cut_mock)

    umbrella = SimpleNamespace(
        id=umbrella_id,
        quick_context="external_pr_supersede pr=42 review=abc",
        project_id=uuid4(),
        branch_name="feature/main_pm/supersede-pr-42",
        status=TaskStatus.PENDING,
        orchestration_markers={"branch_pending": True},
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=mock_session)

    mock_task_service = MagicMock()
    mock_task_service.supersede_umbrellas_branch_pending = AsyncMock(
        return_value=[umbrella]
    )
    mock_task_service.get = AsyncMock(return_value=umbrella)
    mock_project_service = MagicMock()
    mock_project_service.get = AsyncMock(
        return_value=SimpleNamespace(slug="test-project")
    )

    with (
        patch("roboco.services.task.get_task_service", return_value=mock_task_service),
        patch(
            "roboco.services.project.get_project_service",
            return_value=mock_project_service,
        ),
    ):
        to_reconcile = await AgentOrchestrator._collect_supersede_reconciliations(
            orch, session_factory
        )

    # The in-flight umbrella is filtered out.
    assert to_reconcile == []
    cut_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_reconciliations_isolates_one_failing_umbrella() -> None:
    """One umbrella raising mid-reconciliation must not discard entries
    already reconciled for its siblings on the same sweep tick (per-row
    isolation, mirroring the notification re-escalation sweep #721/#730)."""
    orch = _new_orchestrator()
    orch._supersede_cuts_in_flight = set()

    good_id = uuid4()
    bad_id = uuid4()
    good_umbrella = SimpleNamespace(
        id=good_id,
        project_id=uuid4(),
        branch_name="feature/main_pm/supersede-pr-42",
        status=TaskStatus.PENDING,
        orchestration_markers={
            "branch_pending": True,
            "external_pr_supersede": f"pr={_PR_42} review=abc",
        },
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=mock_session)

    mock_task_service = MagicMock()
    mock_task_service.supersede_umbrellas_branch_pending = AsyncMock(
        return_value=[SimpleNamespace(id=bad_id), SimpleNamespace(id=good_id)]
    )

    async def _get(uid: Any) -> Any:
        if uid == bad_id:
            raise RuntimeError("db exploded reading this umbrella")
        return good_umbrella

    mock_task_service.get = AsyncMock(side_effect=_get)
    mock_project_service = MagicMock()
    mock_project_service.get = AsyncMock(
        return_value=SimpleNamespace(slug="test-project")
    )

    with (
        patch("roboco.services.task.get_task_service", return_value=mock_task_service),
        patch(
            "roboco.services.project.get_project_service",
            return_value=mock_project_service,
        ),
    ):
        to_reconcile = await AgentOrchestrator._collect_supersede_reconciliations(
            orch, session_factory
        )

    # The bad umbrella is skipped; the good sibling still reconciles.
    assert len(to_reconcile) == 1
    assert to_reconcile[0][0] == str(good_id)
    assert to_reconcile[0][1] == "test-project"
    assert to_reconcile[0][2] == _PR_42


# ---------------------------------------------------------------------------
# F1: the comment-posted marker must be durable (committed) before the risky
# branch-cut step, so a later failure's session rollback can't resurrect a
# flush-only marker and cause a retry to repost a duplicate PR comment.
# ---------------------------------------------------------------------------


class _FakeSupersedeDb:
    """Minimal fake session that reproduces the exact bug shape: a `flush()`
    is NOT durable and is wiped by `rollback()`; only `commit()` makes an
    ORM object's mutated attribute survive a rollback. Real enough to prove
    F1 without a real DB."""

    def __init__(self, obj: SimpleNamespace) -> None:
        self._obj = obj
        self._committed = dict(obj.orchestration_markers or {})

    async def flush(self) -> None:
        pass  # NOT durable, matching real SQLAlchemy flush-without-commit.

    async def commit(self) -> None:
        self._committed = dict(self._obj.orchestration_markers or {})

    async def rollback(self) -> None:
        self._obj.orchestration_markers = dict(self._committed)


def _fake_get_db_context(fake_db: _FakeSupersedeDb) -> Any:
    """Mirrors `roboco.db.base.get_db_context`'s real commit/rollback shape:
    commit on clean exit, rollback + re-raise on any exception."""

    @asynccontextmanager
    async def _ctx() -> Any:
        try:
            yield fake_db
            await fake_db.commit()
        except Exception:
            await fake_db.rollback()
            raise

    return _ctx


@pytest.mark.asyncio
async def test_branch_cut_retry_does_not_repost_comment_after_step_failure() -> None:
    """F1: a branch-cut step failure AFTER a successful comment post must not
    wipe the comment-posted marker (pre-fix: it was flush-only and the
    get_db_context rollback discarded it) -- and a retry must then see the
    marker and skip re-posting to the contributor's public PR."""
    umbrella_id = uuid4()
    umbrella = SimpleNamespace(
        id=umbrella_id,
        status=TaskStatus.PENDING,
        orchestration_markers={
            "branch_pending": True,
            "external_pr_supersede": f"pr={_PR_NUM} review=abc author=someone",
        },
    )
    fake_db = _FakeSupersedeDb(umbrella)

    mock_task_service = MagicMock()
    mock_task_service.get = AsyncMock(return_value=umbrella)

    mock_git = MagicMock()
    mock_git.comment_pull_request = AsyncMock()
    mock_git.get_workspace = AsyncMock(side_effect=RuntimeError("workspace boom"))
    mock_git.create_branch_from_pr_head = AsyncMock()

    orch = MagicMock()
    orch._supersede_cuts_in_flight = set()
    orch._parse_supersede_author = AgentOrchestrator._parse_supersede_author
    orch._fail_supersede_branch_cut = AsyncMock()

    with (
        patch("roboco.db.get_db_context", _fake_get_db_context(fake_db)),
        patch("roboco.services.task.get_task_service", return_value=mock_task_service),
        patch("roboco.services.git.GitService", return_value=mock_git),
    ):
        await AgentOrchestrator._cut_supersede_branch(
            cast("AgentOrchestrator", orch),
            umbrella_id=str(umbrella_id),
            project_slug="test-project",
            pr_number=_PR_NUM,
            project_id=uuid4(),
            branch_name="feature/main_pm/supersede-pr-99",
        )

    # The comment was posted once; the branch-cut step's failure was handed
    # off to the retry/backoff path (mocked out -- covered separately below).
    mock_git.comment_pull_request.assert_awaited_once()
    orch._fail_supersede_branch_cut.assert_awaited_once()
    # The critical assertion: despite the session rollback triggered by the
    # LATER failure, the comment-posted marker survived because it was
    # committed durably the moment the comment landed.
    assert markers.is_supersede_comment_posted(umbrella) is True

    # Retry: this time the branch cut succeeds. The marker must gate a
    # second post -- comment_pull_request stays called exactly once total.
    mock_git.get_workspace = AsyncMock(return_value=MagicMock())
    mock_git.create_branch_from_pr_head = AsyncMock()
    with (
        patch("roboco.db.get_db_context", _fake_get_db_context(fake_db)),
        patch("roboco.services.task.get_task_service", return_value=mock_task_service),
        patch("roboco.services.git.GitService", return_value=mock_git),
    ):
        await AgentOrchestrator._cut_supersede_branch(
            cast("AgentOrchestrator", orch),
            umbrella_id=str(umbrella_id),
            project_slug="test-project",
            pr_number=_PR_NUM,
            project_id=uuid4(),
            branch_name="feature/main_pm/supersede-pr-99",
        )

    mock_git.comment_pull_request.assert_awaited_once()
    assert markers.is_branch_pending(umbrella) is False


# ---------------------------------------------------------------------------
# Reset-durability: the CEO-unblock reset (branch_cut_failed -> fresh
# branch_pending) must survive a LATER git failure in the same call, the same
# way F1 already covers the comment-posted marker. Exact combo: the comment
# was already posted on an earlier attempt (so that block's own commit is
# skipped entirely this round) AND the umbrella is CEO-unblocked
# (branch_cut_failed set, branch_pending clear) when the branch cut itself
# raises.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ceo_unblock_reset_survives_later_branch_cut_failure() -> None:
    """A flush-only reset is wiped by get_db_context's rollback on the git
    failure below it, so _fail_supersede_branch_cut's own `is_branch_pending`
    guard sees the pre-reset state and silently no-ops -- dropping that
    attempt's retry bookkeeping (attempt count + backoff) entirely. A commit
    makes the reset durable before the risky git step runs, so the failure
    handler still sees branch_pending and records the retry."""
    umbrella_id = uuid4()
    umbrella = SimpleNamespace(
        id=umbrella_id,
        status=TaskStatus.PENDING,
        orchestration_markers={
            # Exhausted + CEO-unblocked: branch_cut_failed set, no
            # branch_pending (mirrors the real post-unblock state).
            "branch_cut_failed": _ATTEMPTS,
            "supersede_comment_posted": True,
            "external_pr_supersede": f"pr={_PR_NUM} review=abc author=someone",
        },
    )
    fake_db = _FakeSupersedeDb(umbrella)

    mock_task_service = MagicMock()
    mock_task_service.get = AsyncMock(return_value=umbrella)
    mock_task_service.admin_set_status = AsyncMock()

    mock_git = MagicMock()
    mock_git.comment_pull_request = AsyncMock()
    mock_git.get_workspace = AsyncMock(side_effect=RuntimeError("workspace boom"))
    mock_git.create_branch_from_pr_head = AsyncMock()

    orch = MagicMock()
    orch._supersede_cuts_in_flight = set()
    orch._parse_supersede_author = AgentOrchestrator._parse_supersede_author
    # Bind the REAL failure handler (not mocked out, unlike the F1 test
    # above) so this proves the reset's bookkeeping actually reaches it.
    real_fail = AgentOrchestrator._fail_supersede_branch_cut
    orch._fail_supersede_branch_cut = real_fail.__get__(orch, AgentOrchestrator)

    with (
        patch("roboco.db.get_db_context", _fake_get_db_context(fake_db)),
        patch("roboco.services.task.get_task_service", return_value=mock_task_service),
        patch("roboco.services.git.GitService", return_value=mock_git),
    ):
        await AgentOrchestrator._cut_supersede_branch(
            cast("AgentOrchestrator", orch),
            umbrella_id=str(umbrella_id),
            project_slug="test-project",
            pr_number=_PR_NUM,
            project_id=uuid4(),
            branch_name="feature/main_pm/supersede-pr-99",
        )

    # The comment was already posted -- that block must not re-post.
    mock_git.comment_pull_request.assert_not_awaited()
    # The critical assertions: the reset's branch_pending survived the LATER
    # git failure (durable, not wiped by that failure's own rollback), and
    # the failure handler actually recorded this attempt instead of no-op'ing
    # on a stale is_branch_pending() read.
    assert markers.is_branch_pending(umbrella) is True
    assert markers.get_branch_cut_next_retry_at(umbrella) is not None
    # Attempt count restarted fresh off the CEO-unblock reset (0 -> 1), not
    # left stuck at the pre-unblock exhausted count.
    assert markers.get_branch_cut_attempts(umbrella) == 1
    # Still below MAX_ATTEMPTS this round -- no premature re-escalation.
    mock_task_service.admin_set_status.assert_not_awaited()


# ---------------------------------------------------------------------------
# F3 / F6(c): failure path retries with backoff, then escalates to BLOCKED
# ---------------------------------------------------------------------------


def _marker_task(**kw: Any) -> SimpleNamespace:
    om = kw.pop("orchestration_markers", None) or {}
    return SimpleNamespace(orchestration_markers=om, **kw)


@pytest.mark.asyncio
async def test_fail_keeps_branch_pending_and_sets_backoff_below_max() -> None:
    """F3(a): below MAX_ATTEMPTS, failure keeps branch_pending so the sweep
    retries, and stamps a backoff so it doesn't hammer every 60s."""
    orch = MagicMock()
    orch._supersede_cuts_in_flight = set()

    umbrella = _marker_task(
        id=uuid4(),
        branch_name="feature/main_pm/supersede-pr-42",
        orchestration_markers={"branch_pending": True},
    )

    mock_task_service = MagicMock()
    mock_task_service.get = AsyncMock(return_value=umbrella)
    mock_task_service.admin_set_status = AsyncMock()

    db_mock = AsyncMock()
    db_mock.commit = AsyncMock()
    db_mock.flush = AsyncMock()

    with (
        patch("roboco.db.get_db_context") as ctx_mock,
        patch("roboco.services.task.get_task_service", return_value=mock_task_service),
    ):
        ctx_mock.return_value.__aenter__ = AsyncMock(return_value=db_mock)
        ctx_mock.return_value.__aexit__ = AsyncMock(return_value=None)
        await AgentOrchestrator._fail_supersede_branch_cut(
            cast("AgentOrchestrator", orch),
            str(umbrella.id),
            "feature/main_pm/supersede-pr-42",
            RuntimeError("clone failed"),
        )

    # branch_pending is kept; branch_cut_failed stores attempt count.
    assert markers.is_branch_pending(umbrella) is True
    assert markers.get_branch_cut_attempts(umbrella) == 1
    assert markers.get_branch_cut_next_retry_at(umbrella) is not None
    # admin_set_status (BLOCKED) is NOT called below MAX_ATTEMPTS.
    mock_task_service.admin_set_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_escalates_to_blocked_at_max_attempts() -> None:
    """F3(a): at MAX_ATTEMPTS, failure clears branch_pending, sets BLOCKED,
    and notifies the CEO (after the commit, per F7)."""
    orch = MagicMock()
    orch._supersede_cuts_in_flight = set()

    # Pre-stamp 2 prior failures so this call hits the MAX.
    umbrella = _marker_task(
        id=uuid4(),
        branch_name="feature/main_pm/supersede-pr-42",
        orchestration_markers={
            "branch_pending": True,
            "branch_cut_failed": 2,
            "branch_cut_next_retry_at": time.time() - 1,
        },
    )

    mock_task_service = MagicMock()
    mock_task_service.get = AsyncMock(return_value=umbrella)
    mock_task_service.admin_set_status = AsyncMock()

    mock_notify_service = MagicMock()
    mock_notify_service.notify_ceo_of_supersede_branch_cut_failure = AsyncMock()

    db_mock = AsyncMock()
    db_mock.commit = AsyncMock()
    db_mock.flush = AsyncMock()

    sessions = [db_mock, db_mock]  # main session, then notify session

    class _Ctx:
        async def __aenter__(self) -> Any:
            return sessions.pop(0)

        async def __aexit__(self, *a: object) -> None:
            pass

    with (
        patch("roboco.db.get_db_context", return_value=_Ctx()),
        patch("roboco.services.task.get_task_service", return_value=mock_task_service),
        patch(
            "roboco.services.notification_delivery.get_notification_delivery_service",
            return_value=mock_notify_service,
        ),
    ):
        await AgentOrchestrator._fail_supersede_branch_cut(
            cast("AgentOrchestrator", orch),
            str(umbrella.id),
            "feature/main_pm/supersede-pr-42",
            RuntimeError("clone failed 3rd time"),
        )

    # branch_pending is cleared; BLOCKED was set.
    assert markers.is_branch_pending(umbrella) is False
    assert markers.get_branch_cut_attempts(umbrella) == _ATTEMPTS
    mock_task_service.admin_set_status.assert_awaited_once()
    # CEO was notified.
    mock_notify_service.notify_ceo_of_supersede_branch_cut_failure.assert_awaited_once()


# ---------------------------------------------------------------------------
# F5 / F6(e): comment_pull_request resolves token + remote from the project,
# never calling get_workspace / ensure_workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_comment_pull_request_does_not_clone() -> None:
    """F5: comment_pull_request must resolve the repo ref from the project's
    git_url, NOT from a workspace clone. get_workspace / ensure_workspace
    must never be called."""
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(
                return_value=MagicMock(
                    id=uuid4(),
                    pr_number=42,
                    project_id=uuid4(),
                )
            )
        )
    )

    git = GitService(session)
    project = SimpleNamespace(
        slug="test-project",
        git_url="https://github.com/owner/repo.git",
    )
    git._project_for_task = AsyncMock(return_value=project)  # type: ignore[method-assign]
    git._get_project_token_or_raise = AsyncMock(return_value="fake-token")  # type: ignore[method-assign]

    mock_forge = MagicMock()
    mock_forge.create_issue_comment = AsyncMock()

    # Spy: if get_workspace is called, the test fails.
    workspace_spy = AsyncMock(
        side_effect=AssertionError(
            "comment_pull_request must not call get_workspace (F5: no clone)"
        )
    )

    with (
        patch.object(GitService, "_forge", new=mock_forge),
        patch.object(GitService, "get_workspace", new=workspace_spy),
    ):
        await git.comment_pull_request(
            _PR_42,
            project_id=uuid4(),
            comment="test comment",
        )

    mock_forge.create_issue_comment.assert_awaited_once()
    # Verify the repo_ref came from the project git_url, not a workspace.
    call_args = mock_forge.create_issue_comment.call_args
    repo_ref = call_args.args[0]
    assert repo_ref.owner == "owner"
    assert repo_ref.repo == "repo"


@pytest.mark.asyncio
async def test_comment_pull_request_posts_on_correct_pr() -> None:
    """F6(d): the comment is posted on the PR number passed to the call."""
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(
                return_value=MagicMock(
                    id=uuid4(),
                    pr_number=_PR_NUM,
                    project_id=uuid4(),
                )
            )
        )
    )

    git = GitService(session)
    project = SimpleNamespace(
        slug="test-project",
        git_url="https://github.com/owner/repo.git",
    )
    git._project_for_task = AsyncMock(return_value=project)  # type: ignore[method-assign]
    git._get_project_token_or_raise = AsyncMock(return_value="fake-token")  # type: ignore[method-assign]

    mock_forge = MagicMock()
    mock_forge.create_issue_comment = AsyncMock()

    with patch.object(GitService, "_forge", new=mock_forge):
        await git.comment_pull_request(
            _PR_NUM,
            project_id=uuid4(),
            comment="supersede comment",
        )

    call_args = mock_forge.create_issue_comment.call_args
    # (repo_ref, token, pr_number, comment)
    assert call_args.args[2] == _PR_NUM
    assert call_args.args[3] == "supersede comment"


# ---------------------------------------------------------------------------
# F6(d): supersede_comment_posted marker prevents double-posting
# ---------------------------------------------------------------------------


def test_comment_posted_marker_prevents_double_post() -> None:
    """The background cut checks is_supersede_comment_posted before posting;
    if the fast path already posted, the marker is set and the background
    path skips."""
    task = _marker_task()
    assert markers.is_supersede_comment_posted(task) is False
    markers.mark_supersede_comment_posted(task)
    assert markers.is_supersede_comment_posted(task) is True


# ---------------------------------------------------------------------------
# Marker helpers: branch_cut_failed stores attempt count
# ---------------------------------------------------------------------------


def test_branch_cut_failed_stores_attempts() -> None:
    task = _marker_task()
    assert markers.get_branch_cut_attempts(task) == 0
    markers.mark_branch_cut_failed(task, _PRIOR_FAILURES)
    assert markers.get_branch_cut_attempts(task) == _PRIOR_FAILURES
    assert markers.is_branch_cut_failed(task) is True
    markers.clear_branch_cut_failed(task)
    assert markers.is_branch_cut_failed(task) is False


def test_branch_cut_next_retry_at_round_trip() -> None:
    task = _marker_task()
    assert markers.get_branch_cut_next_retry_at(task) is None
    markers.set_branch_cut_next_retry_at(task, _RETRY_TS)
    assert markers.get_branch_cut_next_retry_at(task) == _RETRY_TS
    markers.clear_branch_cut_next_retry_at(task)
    assert markers.get_branch_cut_next_retry_at(task) is None


def test_cut_success_clears_all_failure_markers() -> None:
    """On a successful cut, _cut_supersede_branch clears branch_pending,
    branch_cut_failed, and branch_cut_next_retry_at so the dispatcher gates
    release the umbrella."""
    task = _marker_task()
    markers.mark_branch_pending(task)
    markers.mark_branch_cut_failed(task, 2)
    markers.set_branch_cut_next_retry_at(task, time.time() + 60)
    # Simulate the success path's marker clears.
    markers.clear_branch_pending(task)
    markers.clear_branch_cut_failed(task)
    markers.clear_branch_cut_next_retry_at(task)
    assert markers.is_branch_pending(task) is False
    assert markers.is_branch_cut_failed(task) is False
    assert markers.get_branch_cut_next_retry_at(task) is None
    # The dispatcher gate must now release the umbrella.
    assert (
        _is_branch_pending({"orchestration_markers": task.orchestration_markers})
        is False
    )
