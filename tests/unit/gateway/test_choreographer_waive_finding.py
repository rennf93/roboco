"""Choreographer.waive_finding — the auditor's close-without-fix verb.

``mark_waived`` was the long-unwired repo method; this is its only caller.
Severity-scoped: blocker/major must be fixed, never waived. Only open
findings are waivable, and a note is required. No task status changes —
the ledger row ``open -> waived`` plus a ``task.finding_waived`` audit
event is the durable record.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps() -> ChoreographerDeps:
    base = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    return ChoreographerDeps(**base)


def _finding_row(
    *,
    severity: str = "minor",
    status: str = "open",
    origin: str = "qa",
) -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.task_id = uuid4()
    row.severity = severity
    row.status = status
    row.origin = origin
    return row


def _patch_repo(monkeypatch: pytest.MonkeyPatch, row: Any | None) -> MagicMock:
    """Patch the board-module ReviewFindingsRepository to return ``row`` from
    ``get`` and a recording ``mark_waived``. ``row=None`` simulates not-found."""
    repo_mock = MagicMock()
    repo_mock.get = AsyncMock(return_value=row)
    repo_mock.mark_waived = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.board.ReviewFindingsRepository",
        lambda *_a, **_k: repo_mock,
    )
    return repo_mock


def _choreographer(monkeypatch: pytest.MonkeyPatch, row: Any | None):
    deps = _make_deps()
    deps.task.session = MagicMock()
    c = Choreographer(deps)
    return c, _patch_repo(monkeypatch, row)


@pytest.mark.asyncio
async def test_waive_minor_open_finding_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _finding_row(severity="minor", status="open")
    c, repo_mock = _choreographer(monkeypatch, row)

    env = await c.waive_finding(uuid4(), row.id, "cosmetic, not worth a fix")

    assert env.error is None
    assert env.status == "waived"
    repo_mock.mark_waived.assert_awaited_once_with(row.id, "cosmetic, not worth a fix")
    c.audit.log_task_event.assert_awaited_once()
    assert c.audit.log_task_event.await_args.kwargs["event_type"] == (
        "task.finding_waived"
    )


@pytest.mark.asyncio
async def test_waive_nit_open_finding_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _finding_row(severity="nit", status="open")
    c, repo_mock = _choreographer(monkeypatch, row)
    env = await c.waive_finding(uuid4(), row.id, "preference only")
    assert env.error is None
    repo_mock.mark_waived.assert_awaited_once()


@pytest.mark.asyncio
async def test_waive_rejects_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _finding_row(severity="blocker", status="open")
    c, repo_mock = _choreographer(monkeypatch, row)
    env = await c.waive_finding(uuid4(), row.id, "try to skip the fix")
    assert env.error == "invalid_state"
    repo_mock.mark_waived.assert_not_awaited()


@pytest.mark.asyncio
async def test_waive_rejects_major(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _finding_row(severity="major", status="open")
    c, repo_mock = _choreographer(monkeypatch, row)
    env = await c.waive_finding(uuid4(), row.id, "try to skip the fix")
    assert env.error == "invalid_state"
    repo_mock.mark_waived.assert_not_awaited()


@pytest.mark.asyncio
async def test_waive_rejects_already_addressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _finding_row(severity="minor", status="addressed")
    c, repo_mock = _choreographer(monkeypatch, row)
    env = await c.waive_finding(uuid4(), row.id, "already closed")
    assert env.error == "invalid_state"
    repo_mock.mark_waived.assert_not_awaited()


@pytest.mark.asyncio
async def test_waive_rejects_blank_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _finding_row(severity="minor", status="open")
    c, repo_mock = _choreographer(monkeypatch, row)
    env = await c.waive_finding(uuid4(), row.id, "   ")
    assert env.error == "invalid_state"
    repo_mock.mark_waived.assert_not_awaited()


@pytest.mark.asyncio
async def test_waive_unknown_finding_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c, repo_mock = _choreographer(monkeypatch, None)
    env = await c.waive_finding(uuid4(), uuid4(), "note")
    assert env.error == "not_found"
    repo_mock.mark_waived.assert_not_awaited()


@pytest.mark.asyncio
async def test_waive_succeeds_even_if_audit_log_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit event is best-effort: a log failure must not undo the waive."""
    row = _finding_row(severity="minor", status="open")
    c, repo_mock = _choreographer(monkeypatch, row)
    c.audit.log_task_event = AsyncMock(side_effect=RuntimeError("db gone"))

    env = await c.waive_finding(uuid4(), row.id, "still waive me")
    assert env.error is None
    repo_mock.mark_waived.assert_awaited_once()
