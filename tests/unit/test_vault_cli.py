"""``python -m roboco.vault`` — rebuild / relocate, on a tmp vault.

Flag-gated: both subcommands refuse when ROBOCO_OBSIDIAN_VAULT_ENABLED is off.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from roboco.config import settings
from roboco.services.vault_writer import TaskNoteData
from roboco.vault import ensure_vault_assets, main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_main_refuses_rebuild_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", False)
    assert main(["rebuild"]) == 1


def test_main_refuses_relocate_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", False)
    assert main(["relocate", "/tmp/somewhere"]) == 1


def test_relocate_moves_existing_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_root = tmp_path / "old-vault"
    old_root.mkdir()
    (old_root / "RoboCo").mkdir()
    (old_root / "RoboCo" / "marker.md").write_text("hi", encoding="utf-8")
    new_root = tmp_path / "new-vault"

    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    monkeypatch.setattr(settings, "vault_path", str(old_root))
    assert main(["relocate", str(new_root)]) == 0

    assert not old_root.exists()
    assert (new_root / "RoboCo" / "marker.md").read_text(encoding="utf-8") == "hi"


def test_ensure_vault_assets_materializes_templates_idempotently(
    tmp_path: Path,
) -> None:
    ensure_vault_assets(tmp_path)
    plugins = tmp_path / ".obsidian" / "community-plugins.json"
    dashboard = tmp_path / "RoboCo" / "_meta" / "dashboard.md"
    kanban = tmp_path / "RoboCo" / "_meta" / "kanban-board.md"
    assert plugins.exists()
    assert dashboard.exists()
    assert kanban.exists()
    assert "dataview" in plugins.read_text(encoding="utf-8")

    # An operator's own edit survives a second call (never overwritten).
    dashboard.write_text("operator edit", encoding="utf-8")
    ensure_vault_assets(tmp_path)
    assert dashboard.read_text(encoding="utf-8") == "operator edit"


def _db_ctx(db: Any) -> Any:
    @asynccontextmanager
    async def _ctx() -> Any:
        yield db

    return _ctx


def test_rebuild_writes_agent_task_journal_and_a2a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main()`` drives ``_rebuild`` via ``asyncio.run`` — this test must
    stay a plain (non-async) function, or pytest-asyncio's own running loop
    collides with it."""
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    monkeypatch.setattr(settings, "vault_path", str(tmp_path))

    agent = MagicMock(
        id=uuid4(),
        slug="be-dev-1",
        name="BE Dev 1",
        role="developer",
        team=SimpleNamespace(value="backend"),
    )
    agent_service = MagicMock()
    agent_service.list_agents = AsyncMock(return_value=[agent])

    task = MagicMock(id=uuid4(), project_id=None)
    task_service = MagicMock()
    task_service.list_all = AsyncMock(side_effect=[[task], []])

    journal = MagicMock(id=uuid4())
    entry = MagicMock(
        id=uuid4(),
        task_id=None,
        title="Learned something",
        content="body",
        timestamp=datetime.now(UTC),
        type="learning",
    )
    journal_service = MagicMock()
    journal_service.get_or_create_journal = AsyncMock(return_value=journal)
    journal_service.list_entries = AsyncMock(side_effect=[[entry], []])

    conv = MagicMock(id=uuid4(), agent_a="be-dev-1", agent_b="be-pm", task_id=None)
    conv_result = MagicMock()
    conv_result.scalars.return_value.all.return_value = [conv]
    db = MagicMock()
    db.execute = AsyncMock(return_value=conv_result)

    a2a_msg = MagicMock(
        id=uuid4(), from_agent="be-dev-1", content="hi", created_at=datetime.now(UTC)
    )
    a2a_service = MagicMock()
    a2a_service.get_messages = AsyncMock(return_value=[a2a_msg])

    task_note_data = TaskNoteData(
        id=str(task.id),
        title="A task",
        project_slug="unassigned",
        description="desc",
        status="completed",
        team="backend",
        priority=2,
        task_type="code",
    )

    with (
        patch("roboco.db.base.get_db_context", _db_ctx(db)),
        patch("roboco.services.agent.AgentService", return_value=agent_service),
        patch("roboco.services.task.TaskService", return_value=task_service),
        patch("roboco.services.journal.JournalService", return_value=journal_service),
        patch("roboco.services.project.get_project_service", return_value=MagicMock()),
        patch("roboco.services.a2a.A2AService", return_value=a2a_service),
        patch(
            "roboco.services.vault_assembly.assemble_task_note_data",
            AsyncMock(return_value=task_note_data),
        ),
    ):
        assert main(["rebuild"]) == 0

    assert (tmp_path / "RoboCo" / "Agents" / "be-dev-1.md").exists()
    assert any((tmp_path / "RoboCo" / "Tasks" / "unassigned").glob("*.md"))
    assert any((tmp_path / "RoboCo" / "Journals" / "be-dev-1").glob("*.md"))
    assert any((tmp_path / "RoboCo" / "A2A").glob("*.md"))
    # asset bootstrap ran too
    assert (tmp_path / ".obsidian" / "community-plugins.json").exists()
