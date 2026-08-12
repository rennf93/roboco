"""roboco.services.gateway.content_actions.propose_postmortem — Auditor-gated
Coroner postmortem authoring. Mirrors test_content_actions_pest_control.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import ConflictError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``markers`` and ``propose_postmortem`` touch."""

    def __init__(
        self,
        *,
        assigned_to: Any,
        orchestration_markers: dict[str, Any] | None = None,
    ) -> None:
        self.id = uuid4()
        self.assigned_to = assigned_to
        self.orchestration_markers = orchestration_markers
        self.status = TaskStatus.PENDING


def _actions(role: str, *, notification_delivery: Any = None) -> ContentActions:
    task = MagicMock()
    agent = MagicMock()
    agent.role = role
    task.agent_for = AsyncMock(return_value=agent)
    task.session = MagicMock()
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
        notification_delivery=notification_delivery,
    )
    return ContentActions(deps)


def _valid_process_change(kind: str = "prompt_fix") -> dict[str, Any]:
    return {"kind": kind, "description": "add a venv-freshness check to the gate"}


def _valid_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_id": uuid4(),
        "incident_summary": "the task bounced repeatedly over a stale venv",
        "root_cause": "the gate never verified the venv's dev extras were installed",
        "failed_stage": "awaiting_qa",
        "process_change": _valid_process_change(),
        "playbook": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_propose_postmortem_forbidden_for_non_auditor() -> None:
    env = await _actions("product_owner").propose_postmortem(**_valid_kwargs())
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_postmortem_forbidden_for_developer() -> None:
    env = await _actions("developer").propose_postmortem(**_valid_kwargs())
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_postmortem_rejects_short_incident_summary() -> None:
    env = await _actions("auditor").propose_postmortem(
        **_valid_kwargs(incident_summary="too short")
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_postmortem_rejects_oversized_root_cause() -> None:
    env = await _actions("auditor").propose_postmortem(
        **_valid_kwargs(root_cause="x" * 801)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_postmortem_rejects_unknown_failed_stage() -> None:
    env = await _actions("auditor").propose_postmortem(
        **_valid_kwargs(failed_stage="not_a_real_status")
    )
    assert env.error == "invalid_state"
    assert "failed_stage" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_postmortem_accepts_every_real_status_as_failed_stage() -> None:
    with patch("roboco.services.task.get_task_service") as get_task_service:
        task_svc = MagicMock()
        task_svc.list_open_coroner_cycles = AsyncMock(return_value=[])
        get_task_service.return_value = task_svc
        for status in TaskStatus:
            env = await _actions("auditor").propose_postmortem(
                **_valid_kwargs(failed_stage=status.value)
            )
            # Every real status clears the failed_stage gate — the "no open
            # autopsy task assigned" rejection proves the failed_stage check
            # itself did NOT reject.
            assert env.error == "invalid_state"
            assert "no open Coroner autopsy task" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_postmortem_rejects_unknown_process_change_kind() -> None:
    env = await _actions("auditor").propose_postmortem(
        **_valid_kwargs(process_change=_valid_process_change(kind="not_a_kind"))
    )
    assert env.error == "invalid_state"
    assert "process_change" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_postmortem_rejects_short_process_change_description() -> None:
    env = await _actions("auditor").propose_postmortem(
        **_valid_kwargs(process_change={"kind": "prompt_fix", "description": "short"})
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_postmortem_rejects_playbook_kind_without_playbook() -> None:
    env = await _actions("auditor").propose_postmortem(
        **_valid_kwargs(
            process_change=_valid_process_change(kind="playbook"), playbook=None
        )
    )
    assert env.error == "invalid_state"
    assert "playbook" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_postmortem_rejects_thin_playbook_body() -> None:
    env = await _actions("auditor").propose_postmortem(
        **_valid_kwargs(
            process_change=_valid_process_change(kind="playbook"),
            playbook={"title": "A real title", "body": "short"},
        )
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_postmortem_no_open_autopsy_assigned() -> None:
    actions = _actions("auditor")
    with patch("roboco.services.task.get_task_service") as get_task_service:
        task_svc = MagicMock()
        task_svc.list_open_coroner_cycles = AsyncMock(return_value=[])
        get_task_service.return_value = task_svc
        env = await actions.propose_postmortem(**_valid_kwargs())
    assert env.error == "invalid_state"
    assert "no open Coroner autopsy task" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_postmortem_completes_the_task_and_stamps_marker() -> None:
    agent_id = uuid4()
    task = _FakeTask(assigned_to=agent_id)
    actions = _actions("auditor")

    with (
        patch("roboco.services.task.get_task_service") as get_task_service,
        patch("roboco.services.coroner_engine.get_coroner_engine") as get_engine,
    ):
        task_svc = MagicMock()
        task_svc.list_open_coroner_cycles = AsyncMock(return_value=[task])
        get_task_service.return_value = task_svc

        async def _complete(t: Any, payload: dict[str, Any]) -> None:
            markers.set_coroner_postmortem(t, payload)
            t.status = TaskStatus.COMPLETED

        engine = MagicMock()
        engine.complete_with_postmortem = AsyncMock(side_effect=_complete)
        get_engine.return_value = engine

        env = await actions.propose_postmortem(**_valid_kwargs(agent_id=agent_id))

    assert env.status == "postmortem_proposed"
    engine.complete_with_postmortem.assert_awaited_once()
    payload = markers.get_coroner_postmortem(task)
    assert payload is not None
    assert payload["failed_stage"] == "awaiting_qa"
    assert payload["process_change"]["kind"] == "prompt_fix"
    # A non-playbook process change stays "proposed" — the CEO's per-item
    # approve/dismiss decision (CoronerService) is still open.
    assert payload["process_change"]["status"] == "proposed"
    assert payload["process_change"]["materialized_task_id"] is None
    assert payload["playbook_id"] is None
    assert task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_propose_postmortem_drafts_playbook_when_kind_is_playbook() -> None:
    agent_id = uuid4()
    task = _FakeTask(assigned_to=agent_id)
    actions = _actions("auditor")
    drafted = MagicMock()
    drafted.id = uuid4()

    with (
        patch("roboco.services.task.get_task_service") as get_task_service,
        patch("roboco.services.coroner_engine.get_coroner_engine") as get_engine,
        patch("roboco.services.playbook.get_playbook_service") as get_playbook_svc,
    ):
        task_svc = MagicMock()
        task_svc.list_open_coroner_cycles = AsyncMock(return_value=[task])
        get_task_service.return_value = task_svc

        playbook_svc = MagicMock()
        playbook_svc.draft = AsyncMock(return_value=drafted)
        get_playbook_svc.return_value = playbook_svc

        engine = MagicMock()
        engine.complete_with_postmortem = AsyncMock()
        get_engine.return_value = engine

        env = await actions.propose_postmortem(
            **_valid_kwargs(
                agent_id=agent_id,
                process_change=_valid_process_change(kind="playbook"),
                playbook={
                    "title": "Verify venv freshness before gate",
                    "body": "Check the venv's dev-extra marker before make quality",
                },
            )
        )

    assert env.status == "postmortem_proposed"
    playbook_svc.draft.assert_awaited_once()
    # DEFECT 1 regression: created_by alone can't discriminate a Coroner
    # draft from a Librarian one (same fixed Auditor identity), the call
    # site must stamp its own program explicitly.
    assert playbook_svc.draft.await_args.kwargs["source_program"] == "coroner"
    payload = engine.complete_with_postmortem.await_args.args[1]
    assert payload["playbook_id"] == str(drafted.id)
    # A "playbook" kind already routed into the curation queue above —
    # CoronerService refuses to act on it (see its own test module).
    assert payload["process_change"]["status"] == "not_applicable"


@pytest.mark.asyncio
async def test_propose_postmortem_playbook_title_conflict_is_clean_rejection() -> None:
    agent_id = uuid4()
    task = _FakeTask(assigned_to=agent_id)
    actions = _actions("auditor")

    with (
        patch("roboco.services.task.get_task_service") as get_task_service,
        patch("roboco.services.coroner_engine.get_coroner_engine") as get_engine,
        patch("roboco.services.playbook.get_playbook_service") as get_playbook_svc,
    ):
        task_svc = MagicMock()
        task_svc.list_open_coroner_cycles = AsyncMock(return_value=[task])
        get_task_service.return_value = task_svc

        playbook_svc = MagicMock()
        playbook_svc.draft = AsyncMock(
            side_effect=ConflictError("dup slug", resource_type="playbook")
        )
        get_playbook_svc.return_value = playbook_svc

        engine = MagicMock()
        engine.complete_with_postmortem = AsyncMock()
        get_engine.return_value = engine

        env = await actions.propose_postmortem(
            **_valid_kwargs(
                agent_id=agent_id,
                process_change=_valid_process_change(kind="playbook"),
                playbook={
                    "title": "Dup title",
                    "body": "a substantive procedure body here",
                },
            )
        )

    assert env.error == "invalid_state"
    engine.complete_with_postmortem.assert_not_awaited()
