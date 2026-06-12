"""Tests for transcript-retention selection — what the prune deletes, safely.

The prune is destructive (it unlinks files on the operator's real ~/.claude), so
the selection logic is proven here against a temp dir before it ever runs live.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from roboco.runtime.transcript_retention import (
    is_agent_owned_dir,
    select_prunable_transcripts,
)

if TYPE_CHECKING:
    from pathlib import Path

WORKSPACES = "/data/workspaces"
_DAY = 86400


def _touch(path: Path, age_days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    mtime = time.time() - age_days * _DAY
    os.utime(path, (mtime, mtime))
    return path


def test_is_agent_owned_dir_matches_app_and_workspaces() -> None:
    assert is_agent_owned_dir("-app", WORKSPACES) is True
    assert is_agent_owned_dir("-data-workspaces-roboco-be-dev-1", WORKSPACES) is True
    # The operator's own sessions are never agent-owned.
    assert is_agent_owned_dir("-Users-renzof-Documents-foo", WORKSPACES) is False
    assert is_agent_owned_dir("-home-renzof-code", WORKSPACES) is False


def test_is_agent_owned_dir_handles_root_slash_safely() -> None:
    # A pathological "/" workspaces root must not make every dir agent-owned.
    assert is_agent_owned_dir("-Users-renzof-secret", "/") is False
    assert is_agent_owned_dir("-app", "/") is True


def test_select_prunes_only_old_agent_transcripts(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    old_app = _touch(projects / "-app" / "old.jsonl", age_days=30)
    fresh_app = _touch(projects / "-app" / "fresh.jsonl", age_days=1)
    old_ws = _touch(
        projects / "-data-workspaces-roboco-be-dev-1" / "old.jsonl", age_days=30
    )
    # The operator's own old session — MUST be preserved.
    human_old = _touch(
        projects / "-Users-renzof-Documents-thing" / "old.jsonl", age_days=99
    )

    cutoff = time.time() - 14 * _DAY
    prunable = set(select_prunable_transcripts(projects, WORKSPACES, cutoff))

    assert old_app in prunable
    assert old_ws in prunable
    assert fresh_app not in prunable  # too new
    assert human_old not in prunable  # operator's own session — never pruned


def test_select_ignores_non_jsonl_and_directories(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    _touch(projects / "-app" / "keep.txt", age_days=30)  # not a transcript
    (projects / "-app" / "subdir.jsonl").mkdir(parents=True)  # a dir named like one
    cutoff = time.time() - 14 * _DAY
    assert select_prunable_transcripts(projects, WORKSPACES, cutoff) == []


def test_select_handles_missing_projects_root(tmp_path: Path) -> None:
    assert select_prunable_transcripts(tmp_path / "nope", WORKSPACES, time.time()) == []
