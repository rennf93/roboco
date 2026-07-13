"""assemble_task_note_data threads the revision-findings ledger.

Fetched via ``task_service.session`` rather than a new threaded parameter —
see ``roboco/services/vault_assembly.py`` ``_resolve_findings`` for why (every
real caller already carries a real session; only some unit-test stubs don't,
and those must degrade to empty rather than crash).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.vault_assembly import assemble_task_note_data
from roboco.services.vault_writer import VaultWriter


def _task(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid4(),
        "title": "t",
        "description": "d",
        "status": "in_progress",
        "team": "backend",
        "priority": 2,
        "task_type": "code",
        "acceptance_criteria": [],
        "pr_number": None,
        "pr_url": None,
        "project_id": None,
        "parent_task_id": None,
        "dependency_ids": [],
        "batch_id": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _finding_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid4(),
        "severity": "major",
        "file": "roboco/services/task.py",
        "line": 42,
        "expected": "x",
        "actual": "y",
        "fix": "do z",
        "status": "open",
        "round": 1,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_assemble_threads_findings_via_task_service_session() -> None:
    task_service = MagicMock()
    task_service.session = MagicMock()
    task_service.get_subtasks = AsyncMock(return_value=[])
    project_service = MagicMock()

    repo = MagicMock()
    repo.list_for_task = AsyncMock(return_value=[_finding_row()])
    with patch(
        "roboco.services.vault_assembly.ReviewFindingsRepository",
        return_value=repo,
    ):
        data = await assemble_task_note_data(task_service, project_service, _task())

    assert len(data.findings) == 1
    row = data.findings[0]
    assert (row.severity, row.file, row.fix, row.status, row.round) == (
        "major",
        "roboco/services/task.py",
        "do z",
        "open",
        1,
    )


@pytest.mark.asyncio
async def test_assemble_findings_empty_when_task_service_has_no_session() -> None:
    """A duck-typed stub without ``.session`` (e.g. the vault-janitor unit
    tests' ``_TaskSvcStub``) yields no findings rather than crashing."""
    task_service = SimpleNamespace(get_subtasks=AsyncMock(return_value=[]))
    project_service = MagicMock()

    data = await assemble_task_note_data(task_service, project_service, _task())
    assert data.findings == ()


@pytest.mark.asyncio
async def test_assemble_fails_open_when_findings_fetch_raises(tmp_path: Any) -> None:
    """A raising repository degrades to an empty findings tuple — the note is
    still assembled and materializes. The best-effort vault seams swallow
    exceptions, so a raise here would silently kill write_task entirely."""
    task_service = MagicMock()
    task_service.session = MagicMock()
    task_service.get_subtasks = AsyncMock(return_value=[])
    project_service = MagicMock()

    repo = MagicMock()
    repo.list_for_task = AsyncMock(side_effect=RuntimeError("db down"))
    with patch(
        "roboco.services.vault_assembly.ReviewFindingsRepository",
        return_value=repo,
    ):
        data = await assemble_task_note_data(task_service, project_service, _task())

    assert data.findings == ()
    note = VaultWriter(tmp_path).write_task(data)
    assert note.exists()
    assert "## Findings" not in note.read_text(encoding="utf-8")
