"""Vault intake watcher: #roboco-tagged notes become HELD intake drafts.

Mirrors the X-engine / roadmap-engine test shape. The engine only opens a
HELD draft (confirmed_by_human=False, owned by the Secretary,
source=vault_note) — it never starts anything; that is entirely the CEO's
normal task-review flow. Asserted against a real Postgres DB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable, VaultSeenNoteTable
from roboco.foundation import identity as _foundation
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskType,
    Team,
)
from roboco.models.base import TaskStatus as TS
from roboco.runtime.orchestrator import _is_held_ceo_source
from roboco.services import vault_intake_engine as vie_module
from roboco.services.task import VAULT_NOTE_SOURCE, TaskCreateRequest, get_task_service
from roboco.services.vault_intake_engine import VaultIntakeEngine
from sqlalchemy import select

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
SLUG = "roboco"
ONE = 1
TWO = 2
ZERO = 0


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (SECRETARY_UUID, "secretary-1", AgentRole.SECRETARY, None),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    session.add(
        ProjectTable(
            name="RoboCo",
            slug=SLUG,
            git_url="https://github.com/x/roboco.git",
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=SYSTEM_UUID,
            is_active=True,
        )
    )
    await session.flush()


def _enable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: object
) -> Path:
    monkeypatch.setattr(cfg, "obsidian_vault_enabled", True)
    monkeypatch.setattr(cfg, "vault_intake_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    monkeypatch.setattr(cfg, "vault_path", str(tmp_path))
    monkeypatch.setattr(cfg, "vault_intake_dir", "RoboCo/Inbox")
    monkeypatch.setattr(cfg, "vault_intake_max_per_cycle", 3)
    monkeypatch.setattr(cfg, "vault_intake_max_open_drafts", 10)
    for key, value in overrides.items():
        monkeypatch.setattr(cfg, key, value)
    inbox = tmp_path / "RoboCo" / "Inbox"
    inbox.mkdir(parents=True)
    return inbox


def _mock_local_model(monkeypatch: pytest.MonkeyPatch, reply: str | None) -> AsyncMock:
    mock = AsyncMock(return_value=reply)
    monkeypatch.setattr(vie_module, "_chat", mock)
    return mock


def _write(inbox: Path, name: str, content: str) -> Path:
    path = inbox / name
    path.write_text(content, encoding="utf-8")
    return path


_FRONTMATTER_TAGGED = "---\ntags: [roboco]\n---\n\n# Buy milk\n\nGet 2% milk.\n"
_INLINE_TAGGED = "# Fix the fence\n\n#roboco the fence is leaning.\n"
_UNTAGGED = "# Just a note\n\nNothing to see here.\n"
_WITH_CHECKBOXES = (
    "---\ntags: [roboco]\n---\n\n# Weekend chores\n\n"
    "- [ ] Mow the lawn\n- [ ] Wash the car\n"
)


# --------------------------------------------------------------------------- #
# Tag detection
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_frontmatter_tag_is_processed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path)
    _write(inbox, "a.md", _FRONTMATTER_TAGGED)
    _mock_local_model(monkeypatch, None)
    drafts = await VaultIntakeEngine(db_session).run_cycle()
    assert len(drafts) == ONE


@pytest.mark.asyncio
async def test_inline_tag_is_processed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path)
    _write(inbox, "b.md", _INLINE_TAGGED)
    _mock_local_model(monkeypatch, None)
    drafts = await VaultIntakeEngine(db_session).run_cycle()
    assert len(drafts) == ONE


@pytest.mark.asyncio
async def test_untagged_note_is_ignored(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path)
    _write(inbox, "c.md", _UNTAGGED)
    _mock_local_model(monkeypatch, None)
    drafts = await VaultIntakeEngine(db_session).run_cycle()
    assert drafts == []
    ledger = (await db_session.execute(select(VaultSeenNoteTable))).scalars().all()
    assert ledger == []


# --------------------------------------------------------------------------- #
# Ledger dedup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unchanged_note_is_never_reprocessed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path)
    _write(inbox, "d.md", _FRONTMATTER_TAGGED)
    _mock_local_model(monkeypatch, None)
    engine = VaultIntakeEngine(db_session)
    first = await engine.run_cycle()
    assert len(first) == ONE
    second = await engine.run_cycle()
    assert second == []


@pytest.mark.asyncio
async def test_edited_note_is_eligible_again(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path)
    path = _write(inbox, "e.md", _FRONTMATTER_TAGGED)
    _mock_local_model(monkeypatch, None)
    engine = VaultIntakeEngine(db_session)
    first = await engine.run_cycle()
    assert len(first) == ONE
    # Edit the note's real content (not just appending the callout) — a new
    # ledger key, so it's eligible again.
    path.write_text(
        path.read_text(encoding="utf-8") + "\nAlso get some bread.\n",
        encoding="utf-8",
    )
    second = await engine.run_cycle()
    assert len(second) == ONE


# --------------------------------------------------------------------------- #
# Caps
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_max_per_cycle_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path, vault_intake_max_per_cycle=2)
    for i in range(4):
        _write(inbox, f"note{i}.md", _FRONTMATTER_TAGGED)
    _mock_local_model(monkeypatch, None)
    engine = VaultIntakeEngine(db_session)
    first = await engine.run_cycle()
    assert len(first) == TWO
    second = await engine.run_cycle()
    assert len(second) == TWO
    third = await engine.run_cycle()
    assert third == []


@pytest.mark.asyncio
async def test_open_drafts_cap_skips_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path, vault_intake_max_open_drafts=1)
    task_svc = get_task_service(db_session)
    project_id = (
        await db_session.execute(
            select(ProjectTable.id).where(ProjectTable.slug == SLUG)
        )
    ).scalar_one()
    await task_svc.create(
        TaskCreateRequest(
            title="Vault note: existing",
            description="An already-open held draft.",
            acceptance_criteria=["CEO reviews it"],
            team=Team.MAIN_PM,
            assigned_to=SECRETARY_UUID,
            created_by=SYSTEM_UUID,
            task_type=TaskType.ADMINISTRATIVE,
            nature=TaskNature.NON_TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=project_id,
            status=TS.PENDING,
            source=VAULT_NOTE_SOURCE,
            confirmed_by_human=False,
        )
    )
    await db_session.flush()
    _write(inbox, "f.md", _FRONTMATTER_TAGGED)
    _mock_local_model(monkeypatch, None)
    drafts = await VaultIntakeEngine(db_session).run_cycle()
    assert drafts == []
    ledger = (await db_session.execute(select(VaultSeenNoteTable))).scalars().all()
    assert ledger == []


# --------------------------------------------------------------------------- #
# Held-draft shape + dispatcher exclusion
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_held_draft_shape(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path)
    _write(inbox, "g.md", _FRONTMATTER_TAGGED)
    _mock_local_model(monkeypatch, None)
    drafts = await VaultIntakeEngine(db_session).run_cycle()
    task = drafts[0]
    assert task.status == TS.PENDING
    assert task.confirmed_by_human is False
    assert task.source == VAULT_NOTE_SOURCE
    assert task.assigned_to == SECRETARY_UUID
    assert task.team == Team.MAIN_PM


def test_dispatcher_excludes_vault_note_source() -> None:
    assert _is_held_ceo_source({"source": VAULT_NOTE_SOURCE}) is True


# --------------------------------------------------------------------------- #
# Local-model fallback
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_local_model_failure_falls_back_to_deterministic(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path)
    _write(inbox, "h.md", _WITH_CHECKBOXES)
    monkeypatch.setattr(
        vie_module, "_chat", AsyncMock(side_effect=RuntimeError("local model down"))
    )
    drafts = await VaultIntakeEngine(db_session).run_cycle()
    task = drafts[0]
    assert "Weekend chores" in task.title
    assert "Mow the lawn" in task.acceptance_criteria
    assert "Wash the car" in task.acceptance_criteria


@pytest.mark.asyncio
async def test_local_model_success_is_used(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path)
    _write(inbox, "i.md", _FRONTMATTER_TAGGED)
    _mock_local_model(
        monkeypatch,
        '{"title": "Milk run", "description": "Pick up milk on the way home.", '
        '"action_items": ["Buy 2% milk"]}',
    )
    drafts = await VaultIntakeEngine(db_session).run_cycle()
    task = drafts[0]
    assert "Milk run" in task.title
    assert task.description == "Pick up milk on the way home."
    assert task.acceptance_criteria == ["Buy 2% milk"]


# --------------------------------------------------------------------------- #
# Feedback callout
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_feedback_callout_appended_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _seed(db_session)
    inbox = _enable(monkeypatch, tmp_path)
    path = _write(inbox, "j.md", _FRONTMATTER_TAGGED)
    _mock_local_model(monkeypatch, None)
    engine = VaultIntakeEngine(db_session)
    await engine.run_cycle()
    text = path.read_text(encoding="utf-8")
    assert text.count("RoboCo: drafted") == ONE
    # A second cycle over the now-callout-bearing (but otherwise unchanged)
    # note must not reprocess it or double the callout.
    second = await engine.run_cycle()
    assert second == []
    assert path.read_text(encoding="utf-8").count("RoboCo: drafted") == ONE
