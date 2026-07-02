"""_cherry_unmerged_entry must not flag squash-merged children as missing.

Live false positive (2026-07-02): three children of the S6 cell task were
squash-merged (PRs #176/#185/#190) — their commits sat at the assembled
branch tip, yet ``git cherry`` reported every individual child commit as
unmerged (a squash rewrites N patches into one patch-id) and the assembly
integrity guard refused every legitimate submit_up.

Relief: every commit — including the squash commit — carries the
``[taskid8]`` prefix, so a marker-bearing commit on the parent proves the
child landed. A child with no marker on the parent stays flagged (the
original incident #11 the guard exists for).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from roboco.services.git import GitService


def _svc_with_git_responses(
    responses: dict[str, SimpleNamespace],
) -> tuple[GitService, list[list[str]]]:
    """GitService with _run_git stubbed by subcommand name; records calls."""
    svc = GitService.__new__(GitService)
    calls: list[list[str]] = []

    async def _run_git(
        _workspace: Path, args: list[str], **_kw: Any
    ) -> SimpleNamespace:
        calls.append(args)
        return responses[args[0]]

    svc_any: Any = svc
    svc_any._run_git = _run_git
    return svc, calls


def _child() -> MagicMock:
    return MagicMock(
        id=uuid4(), branch_name="feature/frontend/root--cell--child", title="t"
    )


@pytest.mark.asyncio
async def test_squash_merged_child_with_task_marker_is_not_flagged() -> None:
    """cherry says unmerged, but the [taskid8] squash commit is on the parent."""
    svc, calls = _svc_with_git_responses(
        {
            "rev-parse": SimpleNamespace(returncode=0, stdout="abc\n"),
            "cherry": SimpleNamespace(returncode=0, stdout="+ aaa\n+ bbb\n"),
            "log": SimpleNamespace(
                returncode=0, stdout="4771bd71 [deadbeef] title (#190)\n"
            ),
        }
    )
    entry = await svc._cherry_unmerged_entry(Path("/tmp"), "parent", _child())
    assert entry is None
    log_call = next(c for c in calls if c[0] == "log")
    assert any("\\[" in arg for arg in log_call)  # grep pattern escapes the bracket


@pytest.mark.asyncio
async def test_genuinely_missing_child_stays_flagged() -> None:
    """No marker commit on the parent → the original #11 catch still fires."""
    child = _child()
    svc, _calls = _svc_with_git_responses(
        {
            "rev-parse": SimpleNamespace(returncode=0, stdout="abc\n"),
            "cherry": SimpleNamespace(returncode=0, stdout="+ aaa\n"),
            "log": SimpleNamespace(returncode=0, stdout=""),
        }
    )
    entry = await svc._cherry_unmerged_entry(Path("/tmp"), "parent", child)
    assert entry == {"task_id": str(child.id)[:8], "title": "t", "unmerged": 1}


@pytest.mark.asyncio
async def test_cherry_clean_short_circuits_without_marker_probe() -> None:
    """No + lines from cherry → merged; the log probe is never run."""
    svc, calls = _svc_with_git_responses(
        {
            "rev-parse": SimpleNamespace(returncode=0, stdout="abc\n"),
            "cherry": SimpleNamespace(returncode=0, stdout="- aaa\n"),
        }
    )
    entry = await svc._cherry_unmerged_entry(Path("/tmp"), "parent", _child())
    assert entry is None
    assert not any(c[0] == "log" for c in calls)
