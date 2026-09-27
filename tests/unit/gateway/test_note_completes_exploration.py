"""The complete-at-note contract: ``note()`` completes the caller's own open
exploration task when the task's Board Program declares
``BoardProgram.completes_on_note`` (decisions_audit — one review pass IS the
cycle). Regression for the 2026-09-27 auditor wedge: a findings-present
decisions-audit run had NO closing verb at all — note() only wrote journal
entries and i_am_idle leaves the task pending — so the cycle pend-resp-
looped into the breaker and paged the CEO every day. Mock-based shape
mirrors test_content_actions_nothing_to_propose.py; the write itself is
stubbed so these tests exercise exactly the completion seam."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.gateway.envelope import Envelope


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``_complete_note_completing_exploration`` touches."""

    def __init__(
        self,
        *,
        source: str,
        assigned_to: Any,
        status: Any = TaskStatus.PENDING,
    ) -> None:
        self.id = uuid4()
        self.source = source
        self.assigned_to = assigned_to
        self.status = status


def _actions(exploration_task: _FakeTask | None) -> ContentActions:
    task_svc = MagicMock()
    task_svc.get = AsyncMock(return_value=exploration_task)
    task_svc.session = MagicMock()
    task_svc.session.flush = AsyncMock()
    deps = ContentActionsDeps(
        task=task_svc,
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
        notification_delivery=None,
    )
    return ContentActions(deps)


def _stub_write(
    actions: ContentActions,
    monkeypatch: pytest.MonkeyPatch,
    *,
    envelope: Envelope,
) -> None:
    """Stub both write paths so the tests drive only the completion seam."""
    monkeypatch.setattr(
        actions, "_write_journal_note", AsyncMock(return_value=envelope)
    )
    monkeypatch.setattr(
        actions, "_record_section_handoff", AsyncMock(return_value=envelope)
    )


AGENT = uuid4()


def _ok_envelope() -> Envelope:
    return Envelope.ok(status="noted", next="i_am_idle()")


# ---------------------------------------------------------------------------
# completes_on_note programs
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_note_completes_decisions_audit_exploration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful note addressed to the caller's own decisions_audit task
    completes it — the findings-present run finally has a closing verb."""
    exploration = _FakeTask(source="board_decisions_audit", assigned_to=AGENT)
    actions = _actions(exploration)
    task = exploration
    _stub_write(actions, monkeypatch, envelope=_ok_envelope())

    env = await actions.note(
        agent_id=AGENT,
        text="Per-pilot assessment: tool_spotlight drifting low.",
        task_id=task.id,
    )

    assert env.error is None
    assert task.status is TaskStatus.COMPLETED
    task_svc_session = actions.task.session
    task_svc_session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_note_completion_is_idempotent_on_terminal_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subsequent notes (one per pilot) never corrupt an already-terminal
    task."""
    exploration = _FakeTask(
        source="board_decisions_audit",
        assigned_to=AGENT,
        status=TaskStatus.COMPLETED,
    )
    actions = _actions(exploration)
    task = exploration
    _stub_write(actions, monkeypatch, envelope=_ok_envelope())

    await actions.note(
        agent_id=AGENT,
        text="Second pilot note for the same window.",
        task_id=task.id,
    )

    assert task.status is TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# programs WITHOUT the contract stay open
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_note_never_completes_proposal_program(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Roadmap (and every other proposal program) stays open awaiting per-item
    CEO review — a note must not auto-complete it."""
    exploration = _FakeTask(source="board_roadmap", assigned_to=AGENT)
    actions = _actions(exploration)
    task = exploration
    _stub_write(actions, monkeypatch, envelope=_ok_envelope())

    await actions.note(
        agent_id=AGENT,
        text="Working notes while authoring the cycle.",
        task_id=task.id,
    )

    assert task.status is TaskStatus.PENDING


@pytest.mark.asyncio
async def test_note_does_not_complete_someone_elses_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exploration = _FakeTask(source="board_decisions_audit", assigned_to=uuid4())
    actions = _actions(exploration)
    task = exploration
    _stub_write(actions, monkeypatch, envelope=_ok_envelope())

    await actions.note(
        agent_id=AGENT,
        text="A note about a task that is not mine.",
        task_id=task.id,
    )

    assert task.status is TaskStatus.PENDING


@pytest.mark.asyncio
async def test_failed_note_completes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected note (soup guard) must not complete the exploration task."""
    exploration = _FakeTask(source="board_decisions_audit", assigned_to=AGENT)
    actions = _actions(exploration)
    task = exploration
    _stub_write(
        actions,
        monkeypatch,
        envelope=Envelope.invalid_state(message="too short", remediate="elaborate"),
    )

    await actions.note(
        agent_id=AGENT,
        text="wip",
        task_id=task.id,
    )

    assert task.status is TaskStatus.PENDING


@pytest.mark.asyncio
async def test_note_without_task_id_never_looks_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The journal-only note (no task scope) must not even probe the task
    service."""
    actions = _actions(None)
    _stub_write(actions, monkeypatch, envelope=_ok_envelope())

    env = await actions.note(agent_id=AGENT, text="An unscaled journal note.")

    assert env.error is None
    actions.task.get.assert_not_awaited()
