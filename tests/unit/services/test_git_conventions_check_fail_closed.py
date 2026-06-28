"""conventions_check_for_task must fail CLOSED on a resolution error.

The conventions block gate (i_am_done / pr_pass) treats
``could_not_run=False`` + no findings as a clean PASS. A workspace or diff
resolution error that returns ``could_not_run=False`` therefore silently
disables the block gate — the opposite of the validator's OWN fail-loud
philosophy (exit 3 → ``could_not_run=True`` → gate blocks, never silently
passes). A raised exception during workspace/diff resolution is a real error,
not an empty result, so it must fail closed: ``could_not_run=True``.

The two LEGITIMATE fail-open paths stay fail-open:
- no ``branch_name`` (a branchless/coordination task has nothing to validate)
- no changed files (nothing changed → nothing to validate)
These are empty-result cases, not errors, so the gate correctly passes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.git import GitService


def _service() -> GitService:
    return GitService(MagicMock())


def _bind(svc: GitService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _task(branch_name: str) -> MagicMock:
    return MagicMock(branch_name=branch_name)


@pytest.mark.asyncio
async def test_workspace_resolution_error_fails_closed() -> None:
    """``_workspace_for_branch`` raising → could_not_run=True (block), not
    False (silent pass)."""
    svc = _service()
    _bind(
        svc,
        "_workspace_for_branch",
        AsyncMock(side_effect=RuntimeError("workspace clone missing")),
    )
    result = await svc.conventions_check_for_task(uuid4(), _task("feature/backend/abc"))
    assert result["could_not_run"] is True
    assert result["findings"] == []
    # The reason is informational (for logs/debug), capped like the validator path.
    assert isinstance(result.get("reason"), str) and result["reason"]


@pytest.mark.asyncio
async def test_diff_resolution_error_fails_closed() -> None:
    """``list_changed_files`` raising → could_not_run=True (block), not a
    silent pass. The workspace resolved fine, but the diff itself failed."""
    svc = _service()
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(
        svc,
        "list_changed_files",
        AsyncMock(side_effect=RuntimeError("git diff errored")),
    )
    result = await svc.conventions_check_for_task(uuid4(), _task("feature/backend/abc"))
    assert result["could_not_run"] is True
    assert result["findings"] == []


@pytest.mark.asyncio
async def test_no_branch_still_fails_open() -> None:
    """A branchless/coordination task (no branch_name) has nothing to
    validate — the gate correctly passes (could_not_run=False). This is NOT
    an error; it must stay fail-open."""
    svc = _service()
    result = await svc.conventions_check_for_task(uuid4(), _task(""))
    assert result["could_not_run"] is False
    assert result["findings"] == []


@pytest.mark.asyncio
async def test_no_changed_files_still_fails_open() -> None:
    """A task whose branch resolved but has no changed files has nothing to
    validate — the gate correctly passes (could_not_run=False). This is an
    empty result, not an error; it must stay fail-open."""
    svc = _service()
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    _bind(svc, "list_changed_files", AsyncMock(return_value=[]))
    result = await svc.conventions_check_for_task(uuid4(), _task("feature/backend/abc"))
    assert result["could_not_run"] is False
    assert result["findings"] == []
