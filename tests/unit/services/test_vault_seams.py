"""Vault event seams (journal write / A2A send / task status transition):
best-effort isolation. A raised VaultWriter error must NEVER fail the
underlying verb; the flag off must short-circuit before any writer call;
``is_private`` journal entries must be excluded (same rule as the RAG corpus).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.models.base import JournalEntryType
from roboco.services.a2a import A2AService
from roboco.services.journal import JournalService
from roboco.services.task import TaskService
from roboco.services.vault_writer import VaultWriter

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.db.tables import TaskTable


def _entry_row(*, is_private: bool = False, task_id: object | None = None) -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.task_id = task_id
    row.title = "Some entry"
    row.content = "Body text"
    row.timestamp = datetime.now(UTC)
    row.type = JournalEntryType.GENERAL
    row.is_private = is_private
    return row


# --- journal seam --------------------------------------------------------- #


@pytest.mark.asyncio
async def test_journal_seam_noop_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", False)
    svc = JournalService(MagicMock())
    monkeypatch.setattr(svc, "get_agent_slug", AsyncMock(return_value="be-dev-1"))
    with patch("roboco.services.vault_writer.get_vault_writer") as get_writer:
        await svc._materialize_vault_note(_entry_row(), uuid4(), is_private=False)
    get_writer.assert_not_called()


@pytest.mark.asyncio
async def test_journal_seam_excludes_private_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors the RAG-index exclusion: a private entry never reaches the vault."""
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    svc = JournalService(MagicMock())
    monkeypatch.setattr(svc, "get_agent_slug", AsyncMock(return_value="be-dev-1"))
    with patch("roboco.services.vault_writer.get_vault_writer") as get_writer:
        await svc._materialize_vault_note(
            _entry_row(is_private=True), uuid4(), is_private=True
        )
    get_writer.assert_not_called()


@pytest.mark.asyncio
async def test_journal_seam_writer_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    svc = JournalService(MagicMock())
    monkeypatch.setattr(svc, "get_agent_slug", AsyncMock(return_value="be-dev-1"))
    writer = MagicMock()
    writer.write_journal_entry.side_effect = OSError("disk full")
    with patch("roboco.services.vault_writer.get_vault_writer", return_value=writer):
        await svc._materialize_vault_note(_entry_row(), uuid4(), is_private=False)
    writer.write_journal_entry.assert_called_once()


@pytest.mark.asyncio
async def test_journal_seam_writes_when_enabled_and_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    svc = JournalService(MagicMock())
    monkeypatch.setattr(svc, "get_agent_slug", AsyncMock(return_value="be-dev-1"))
    writer = MagicMock()
    with patch("roboco.services.vault_writer.get_vault_writer", return_value=writer):
        await svc._materialize_vault_note(_entry_row(), uuid4(), is_private=False)
    writer.write_journal_entry.assert_called_once()


# --- A2A seam --------------------------------------------------------------- #


def _a2a_msg() -> MagicMock:
    msg = MagicMock()
    msg.id = uuid4()
    msg.content = "hello"
    msg.created_at = datetime.now(UTC)
    return msg


def _a2a_conv(task_id: object | None = None) -> MagicMock:
    conv = MagicMock()
    conv.id = uuid4()
    conv.task_id = task_id
    return conv


@pytest.mark.asyncio
async def test_a2a_seam_noop_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", False)
    with patch("roboco.services.vault_writer.get_vault_writer") as get_writer:
        await A2AService._materialize_vault_note(
            _a2a_msg(), _a2a_conv(), "be-dev-1", "be-pm"
        )
    get_writer.assert_not_called()


@pytest.mark.asyncio
async def test_a2a_seam_writer_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    writer = MagicMock()
    writer.append_a2a_message.side_effect = RuntimeError("boom")
    with patch("roboco.services.vault_writer.get_vault_writer", return_value=writer):
        await A2AService._materialize_vault_note(
            _a2a_msg(), _a2a_conv(), "be-dev-1", "be-pm"
        )
    writer.append_a2a_message.assert_called_once()


# --- task status-transition seam -------------------------------------------- #


def _task_row() -> MagicMock:
    task = MagicMock()
    task.id = uuid4()
    task.pr_number = None
    task.pr_url = None
    return task


def test_task_transition_seam_noop_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", False)
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    with patch("roboco.services.vault_writer.get_vault_writer") as get_writer:
        svc._touch_vault_frontmatter(
            _task_row(), to_status="in_progress", team="backend"
        )
    get_writer.assert_not_called()


def test_task_transition_seam_writer_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    writer = MagicMock()
    writer.touch_task_frontmatter.side_effect = OSError("nope")
    with patch("roboco.services.vault_writer.get_vault_writer", return_value=writer):
        svc._touch_vault_frontmatter(
            _task_row(), to_status="in_progress", team="backend"
        )
    writer.touch_task_frontmatter.assert_called_once()


def test_task_transition_seam_touches_status_team_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    task = _task_row()
    task.pr_number = 7
    task.pr_url = "https://github.com/x/y/pull/7"
    writer = MagicMock()
    with patch("roboco.services.vault_writer.get_vault_writer", return_value=writer):
        svc._touch_vault_frontmatter(task, to_status="awaiting_qa", team="backend")
    writer.touch_task_frontmatter.assert_called_once_with(
        task_id=str(task.id),
        status="awaiting_qa",
        team="backend",
        pr_number=7,
        pr_url="https://github.com/x/y/pull/7",
    )


# --- materialize-on-create seam ---------------------------------------------- #


def _fresh_task_stub() -> TaskTable:
    stub = SimpleNamespace(
        id=uuid4(),
        title="Fresh task",
        description="Just created.",
        status="pending",
        team="backend",
        priority=2,
        task_type="code",
        acceptance_criteria=[],
        pr_number=None,
        pr_url=None,
        project_id=None,
        parent_task_id=None,
        dependency_ids=None,
        batch_id=None,
        completed_at=None,
        updated_at=None,
        created_at=datetime.now(UTC),
    )
    return cast("TaskTable", stub)


def _create_seam_service() -> TaskService:
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    object.__setattr__(svc, "get", AsyncMock(return_value=None))
    object.__setattr__(svc, "get_subtasks", AsyncMock(return_value=[]))
    return svc


@pytest.mark.asyncio
async def test_create_seam_noop_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", False)
    svc = _create_seam_service()
    with patch("roboco.services.vault_writer.get_vault_writer") as get_writer:
        await svc._materialize_vault_note(_fresh_task_stub())
    get_writer.assert_not_called()


@pytest.mark.asyncio
async def test_create_seam_writer_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    svc = _create_seam_service()
    writer = MagicMock()
    writer.write_task.side_effect = OSError("disk full")
    with patch("roboco.services.vault_writer.get_vault_writer", return_value=writer):
        await svc._materialize_vault_note(_fresh_task_stub())
    writer.write_task.assert_called_once()


@pytest.mark.asyncio
async def test_create_seam_materializes_note_in_tmp_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    svc = _create_seam_service()
    task = _fresh_task_stub()
    with patch(
        "roboco.services.vault_writer.get_vault_writer",
        return_value=VaultWriter(tmp_path),
    ):
        await svc._materialize_vault_note(task)
    note = VaultWriter(tmp_path).find_task_note(str(task.id))
    assert note is not None
    text = note.read_text(encoding="utf-8")
    assert "status: pending" in text
    # Narrative stays Auditor-owned: only the placeholder is rendered.
    assert "_Pending Auditor curation._" in text
