"""Supersede close-on-land: the marker parser + author tag + replacement link.

The supersede marker moved off ``quick_context`` into ``orchestration_markers``
(migration 041), but the close-on-land parser kept reading ``quick_context`` and
expecting a ``external_pr_supersede``-prefixed line, so it returned None for
every umbrella and the contributor PR was never closed. These tests pin the
fixed parser (reads the marker value directly) and the rich close comment
(tags the contributor, links the merged replacement PR).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import (
    SUPERSEDE_PR_CLOSE_COMMENT,
    SUPERSEDE_PR_COMMENT,
    AgentOrchestrator,
    _supersede_author_prefix,
)

_PR = 880
_PR_OTHER = 7
_REPLACEMENT_PR = 890
_REPLACEMENT_PR_42 = 42
_AUTHOR = "EdwardBeshara711"
_MARKER = f"pr={_PR} review=abc-123 author={_AUTHOR}"
_MARKER_NO_AUTHOR = f"pr={_PR_OTHER} review=xyz"
_MARKER_CLOSED = f"{_MARKER} closed=1"
_BRANCH = f"feature/main_pm/supersede-pr-{_PR}"


def _new_orchestrator() -> AgentOrchestrator:
    """A bare orchestrator instance; the close loop touches no instance state."""
    return AgentOrchestrator.__new__(AgentOrchestrator)


# ---------------------------------------------------------------------------
# _parse_supersede_pr: reads the marker value (NOT quick_context)
# ---------------------------------------------------------------------------


def test_parse_pr_from_marker_line() -> None:
    assert AgentOrchestrator._parse_supersede_pr(_MARKER) == _PR


def test_parse_pr_handles_closed_token() -> None:
    # closed=1 is appended after close-on-land; the parser must still find pr=.
    assert AgentOrchestrator._parse_supersede_pr(_MARKER_CLOSED) == _PR


def test_parse_pr_without_author() -> None:
    assert AgentOrchestrator._parse_supersede_pr(_MARKER_NO_AUTHOR) == _PR_OTHER


def test_parse_pr_empty_marker() -> None:
    assert AgentOrchestrator._parse_supersede_pr("") is None


def test_parse_pr_rejects_non_numeric() -> None:
    assert AgentOrchestrator._parse_supersede_pr("pr=notanumber review=x") is None


# ---------------------------------------------------------------------------
# _parse_supersede_author
# ---------------------------------------------------------------------------


def test_parse_author_present() -> None:
    assert AgentOrchestrator._parse_supersede_author(_MARKER) == _AUTHOR


def test_parse_author_absent() -> None:
    assert AgentOrchestrator._parse_supersede_author(_MARKER_NO_AUTHOR) == ""


def test_parse_author_empty_marker() -> None:
    assert AgentOrchestrator._parse_supersede_author("") == ""


# ---------------------------------------------------------------------------
# _supersede_author_prefix
# ---------------------------------------------------------------------------


def test_author_prefix_tags_known_login() -> None:
    assert _supersede_author_prefix(_AUTHOR) == f"@{_AUTHOR} "


def test_author_prefix_empty_when_unknown() -> None:
    assert _supersede_author_prefix("") == ""


# ---------------------------------------------------------------------------
# close comment bodies
# ---------------------------------------------------------------------------


def test_close_comment_links_replacement_pr() -> None:
    body = SUPERSEDE_PR_CLOSE_COMMENT.format(replacement_pr=_REPLACEMENT_PR)
    assert f"#{_REPLACEMENT_PR}" in body
    assert "superseded" in body


def test_close_comment_with_author_tag() -> None:
    body = _supersede_author_prefix(_AUTHOR) + SUPERSEDE_PR_CLOSE_COMMENT.format(
        replacement_pr=_REPLACEMENT_PR
    )
    assert body.startswith(f"@{_AUTHOR} ")
    assert f"#{_REPLACEMENT_PR}" in body


def test_at_supersede_comment_names_branch() -> None:
    body = SUPERSEDE_PR_COMMENT.format(branch=_BRANCH)
    assert _BRANCH in body


# ---------------------------------------------------------------------------
# _close_one_superseded_pr: the close comment carries the tag + replacement
# link, in its OWN fresh session (2026-08 pool-exhaustion hardening, see
# _sweep_superseded_prs's docstring). Mirrors _collect_reconciliations'
# session_factory-as-MagicMock-returning-an-AsyncMock-session test pattern
# from test_supersede_branch_cut.py.
# ---------------------------------------------------------------------------


def _fake_session_factory() -> tuple[Any, AsyncMock]:
    """A session_factory whose ``()`` call yields one shared AsyncMock
    session via ``async with``, mirroring test_supersede_branch_cut.py's
    ``session_factory`` fixture pattern."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.commit = AsyncMock()
    session_factory = MagicMock(return_value=mock_session)
    return session_factory, mock_session


@pytest.mark.asyncio
async def test_close_one_superseded_pr_tags_contributor_and_links_replacement() -> None:
    umbrella_id = uuid4()
    umbrella = SimpleNamespace(
        id=umbrella_id,
        project_id=uuid4(),
        orchestration_markers={"external_pr_supersede": _MARKER},
    )
    session_factory, mock_session = _fake_session_factory()

    task_service = MagicMock()
    task_service.get = AsyncMock(return_value=umbrella)
    task_service.mark_supersede_pr_closed = AsyncMock()
    git = MagicMock()
    git.close_pull_request = AsyncMock()

    with (
        patch("roboco.services.task.get_task_service", return_value=task_service),
        patch("roboco.services.git.GitService", return_value=git),
    ):
        await AgentOrchestrator._close_one_superseded_pr(
            _new_orchestrator(),
            str(umbrella_id),
            _REPLACEMENT_PR,
            cast("Any", uuid4()),
            session_factory,
        )

    git.close_pull_request.assert_awaited_once()
    kwargs = git.close_pull_request.call_args.kwargs
    assert kwargs["comment"].startswith(f"@{_AUTHOR} ")
    assert f"#{_REPLACEMENT_PR}" in kwargs["comment"]
    assert kwargs["delete_branch"] is False
    task_service.mark_supersede_pr_closed.assert_awaited_once()
    # The row's own session commits: this is the release point that keeps
    # the pool connection from sitting checked out across every OTHER
    # pending umbrella's close call this tick.
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_one_superseded_pr_skips_marker_without_pr() -> None:
    # A malformed marker (no pr=) must not crash the row.
    umbrella_id = uuid4()
    umbrella = SimpleNamespace(
        id=umbrella_id,
        project_id=uuid4(),
        orchestration_markers={"external_pr_supersede": "review=only"},
    )
    session_factory, mock_session = _fake_session_factory()

    task_service = MagicMock()
    task_service.get = AsyncMock(return_value=umbrella)
    task_service.mark_supersede_pr_closed = AsyncMock()
    git = MagicMock()
    git.close_pull_request = AsyncMock()

    with (
        patch("roboco.services.task.get_task_service", return_value=task_service),
        patch("roboco.services.git.GitService", return_value=git),
    ):
        await AgentOrchestrator._close_one_superseded_pr(
            _new_orchestrator(),
            str(umbrella_id),
            _REPLACEMENT_PR,
            cast("Any", uuid4()),
            session_factory,
        )

    git.close_pull_request.assert_not_awaited()
    task_service.mark_supersede_pr_closed.assert_not_awaited()
    mock_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_one_superseded_pr_no_author_still_links_replacement() -> None:
    """A pre-existing umbrella whose review predates the author capture still
    gets a close comment linking the replacement PR (just without the @tag)."""
    umbrella_id = uuid4()
    umbrella = SimpleNamespace(
        id=umbrella_id,
        project_id=uuid4(),
        orchestration_markers={"external_pr_supersede": _MARKER_NO_AUTHOR},
    )
    session_factory, _mock_session = _fake_session_factory()

    task_service = MagicMock()
    task_service.get = AsyncMock(return_value=umbrella)
    task_service.mark_supersede_pr_closed = AsyncMock()
    git = MagicMock()
    git.close_pull_request = AsyncMock()

    with (
        patch("roboco.services.task.get_task_service", return_value=task_service),
        patch("roboco.services.git.GitService", return_value=git),
    ):
        await AgentOrchestrator._close_one_superseded_pr(
            _new_orchestrator(),
            str(umbrella_id),
            _REPLACEMENT_PR_42,
            cast("Any", uuid4()),
            session_factory,
        )

    kwargs = git.close_pull_request.call_args.kwargs
    assert not kwargs["comment"].startswith("@")
    assert f"#{_REPLACEMENT_PR_42}" in kwargs["comment"]


@pytest.mark.asyncio
async def test_close_one_superseded_pr_isolates_row_failure() -> None:
    """A row whose git call raises (or whose lookup blows up) must be
    logged and skipped, not propagate out: the sweep loop has no try/except
    of its own around each row, so this isolation lives HERE."""
    umbrella_id = uuid4()
    umbrella = SimpleNamespace(
        id=umbrella_id,
        project_id=uuid4(),
        orchestration_markers={"external_pr_supersede": _MARKER},
    )
    session_factory, mock_session = _fake_session_factory()

    task_service = MagicMock()
    task_service.get = AsyncMock(return_value=umbrella)
    task_service.mark_supersede_pr_closed = AsyncMock()
    git = MagicMock()
    git.close_pull_request = AsyncMock(side_effect=RuntimeError("PAT revoked"))

    with (
        patch("roboco.services.task.get_task_service", return_value=task_service),
        patch("roboco.services.git.GitService", return_value=git),
    ):
        # Must not raise.
        await AgentOrchestrator._close_one_superseded_pr(
            _new_orchestrator(),
            str(umbrella_id),
            _REPLACEMENT_PR,
            cast("Any", uuid4()),
            session_factory,
        )

    task_service.mark_supersede_pr_closed.assert_not_awaited()
    mock_session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# _sweep_superseded_prs: reads the pending list once, then hands EACH row to
# its own _close_one_superseded_pr call with a fresh session per row (never
# one session shared across the whole pending list). The 2026-08 fix for
# the highest-frequency pool-hold offender (this sweep ticks every 60s).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_hands_each_pending_row_its_own_close_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _new_orchestrator()
    orch._supersede_cuts_in_flight = set()
    orch._bg_tasks = set()

    pending = [(str(uuid4()), 1), (str(uuid4()), 2)]
    monkeypatch.setattr(
        orch, "_collect_supersede_close_pending", AsyncMock(return_value=pending)
    )
    close_one = AsyncMock()
    monkeypatch.setattr(orch, "_close_one_superseded_pr", close_one)
    monkeypatch.setattr(
        orch, "_collect_supersede_reconciliations", AsyncMock(return_value=[])
    )

    with patch("roboco.db.base.get_session_factory", return_value=MagicMock()):
        await AgentOrchestrator._sweep_superseded_prs(orch)

    # One _close_one_superseded_pr call per pending row, never one call
    # (or one shared session) for the whole batch.
    assert close_one.await_count == len(pending)
    called = {(c.args[0], c.args[1]) for c in close_one.await_args_list}
    assert called == set(pending)
