"""Vault event seams (journal write / A2A send / task status transition):
best-effort isolation. A raised VaultWriter error must NEVER fail the
underlying verb; the flag off must short-circuit before any writer call;
``is_private`` journal entries must be excluded (same rule as the RAG corpus).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.models.base import JournalEntryType
from roboco.services.a2a import A2AService
from roboco.services.journal import JournalService
from roboco.services.task import TaskService


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
    svc.get_agent_slug = AsyncMock(return_value="be-dev-1")
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
    svc.get_agent_slug = AsyncMock(return_value="be-dev-1")
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
    svc.get_agent_slug = AsyncMock(return_value="be-dev-1")
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
    svc.get_agent_slug = AsyncMock(return_value="be-dev-1")
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
