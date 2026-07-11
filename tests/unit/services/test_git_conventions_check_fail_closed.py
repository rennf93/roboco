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

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services import git as git_module
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


@pytest.mark.asyncio
async def test_preferred_parent_forwards_to_list_changed_files() -> None:
    """The in-path PR-review gate's cross-team parent (see ``diff``) must
    reach ``list_changed_files`` so the validator never analyzes files
    inherited from the wrong-team derived base."""
    svc = _service()
    _bind(svc, "_workspace_for_branch", AsyncMock(return_value=Path("/tmp/ws")))
    changed = AsyncMock(return_value=[])
    _bind(svc, "list_changed_files", changed)
    actor_id = uuid4()
    await svc.conventions_check_for_task(
        actor_id,
        _task("feature/frontend/root--cell"),
        preferred_parent="feature/main_pm/root",
    )
    changed.assert_awaited_once_with(
        branch_name="feature/frontend/root--cell",
        actor_agent_id=actor_id,
        preferred_parent="feature/main_pm/root",
    )


@pytest.mark.asyncio
async def test_validator_timeout_fails_closed_and_reaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung conventions validator subprocess (tree-sitter deadlock, huge repo)
    must time out, fail closed (could_not_run=True so the block gate refuses the
    submit), and kill+wait the proc — not hang the gate forever nor orphan the
    subprocess on orchestrator restart.
    """
    fake_proc = MagicMock()
    fake_proc.returncode = None

    async def _communicate() -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return (b"", b"")

    fake_proc.communicate = _communicate
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock(return_value=-9)

    async def _fake_exec(*_args: object, **_kwargs: object) -> object:
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(git_module, "_CONVENTIONS_VALIDATOR_TIMEOUT_SECONDS", 0.01)

    svc = _service()
    result = await svc._run_conventions_validator(tmp_path, ["a.py"])
    assert result["could_not_run"] is True
    assert "timed out" in (result.get("reason") or "")
    fake_proc.kill.assert_called_once()
    fake_proc.wait.assert_awaited_once()
