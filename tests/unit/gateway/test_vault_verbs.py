"""curate_vault content verb — role grant + ContentActions RBAC + flag gating.

Mirrors test_playbook_verbs.py's shape: only the Auditor curates; a
delivery role is refused; the flag off short-circuits before any write.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.gateway.role_config import get_role_config


def _actions(role: str) -> ContentActions:
    task = MagicMock()
    agent = MagicMock()
    agent.role = role
    task.agent_for = AsyncMock(return_value=agent)
    task.session = AsyncMock()
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
    )
    return ContentActions(deps)


def test_auditor_can_curate_vault() -> None:
    assert "curate_vault" in get_role_config("auditor").do_tools


def test_developer_cannot_curate_vault() -> None:
    assert "curate_vault" not in get_role_config("developer").do_tools


@pytest.mark.asyncio
async def test_curate_vault_forbidden_for_developer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    env = await _actions("developer").curate_vault(
        agent_id=uuid4(), task_id=uuid4(), narrative="did stuff"
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_curate_vault_disabled_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", False)
    env = await _actions("auditor").curate_vault(
        agent_id=uuid4(), task_id=uuid4(), narrative="did stuff"
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_curate_vault_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    actions = _actions("auditor")
    actions.task.get = AsyncMock(return_value=None)
    env = await actions.curate_vault(
        agent_id=uuid4(), task_id=uuid4(), narrative="did stuff"
    )
    assert env.error == "not_found"


@pytest.mark.asyncio
async def test_curate_vault_writes_narrative_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    actions = _actions("auditor")
    task_id = uuid4()
    task = MagicMock()
    actions.task.get = AsyncMock(return_value=task)
    writer = MagicMock()
    data = MagicMock()
    with (
        patch(
            "roboco.services.vault_assembly.assemble_task_note_data",
            AsyncMock(return_value=data),
        ) as assemble,
        patch("roboco.services.vault_writer.get_vault_writer", return_value=writer),
        patch("roboco.services.project.get_project_service"),
    ):
        env = await actions.curate_vault(
            agent_id=uuid4(), task_id=task_id, narrative="Shipped after one rework."
        )
    assert env.error is None
    assert env.status == "vault_curated"
    assemble.assert_awaited_once()
    assert assemble.await_args.kwargs["narrative"] == "Shipped after one rework."
    writer.write_task.assert_called_once_with(data)


@pytest.mark.asyncio
async def test_curate_vault_write_failure_returns_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    actions = _actions("auditor")
    actions.task.get = AsyncMock(return_value=MagicMock())
    with (
        patch(
            "roboco.services.vault_assembly.assemble_task_note_data",
            AsyncMock(side_effect=OSError("disk full")),
        ),
        patch("roboco.services.project.get_project_service"),
    ):
        env = await actions.curate_vault(
            agent_id=uuid4(), task_id=uuid4(), narrative="x"
        )
    assert env.error == "invalid_state"
