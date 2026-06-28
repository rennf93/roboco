"""The FIRST release (no prior ``chore(release):`` commit) must still produce a
non-empty version-bump plan: ``_canonical_bump_files`` falls back to the
version-reference scan when no prior release commit exists.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from roboco.services.release_readiness import (
    _canonical_bump_files,
    assess,
    gather_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

_TODAY = "2026-06-28"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _first_release_repo(tmp_path: Path) -> Path:
    """A git repo at its first release: pyproject embeds 0.1.0, one feat commit,
    NO prior ``chore(release):`` commit."""
    root = tmp_path / "first-release-repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "-A")
    # A non-release commit — there is intentionally NO chore(release): commit.
    _git(root, "commit", "-m", "feat: initial import")
    return root


def test_canonical_bump_files_falls_back_on_first_release(tmp_path: Path) -> None:
    """No prior ``chore(release):`` commit ⇒ the canonical set is the version-
    reference scan, NOT empty."""
    root = _first_release_repo(tmp_path)
    files = _canonical_bump_files(root, "0.1.0")
    assert files  # non-empty
    assert "pyproject.toml" in files


def test_canonical_bump_files_uses_prior_release_commit_when_present(
    tmp_path: Path,
) -> None:
    """A subsequent release keeps deriving the canonical set from the previous
    ``chore(release):`` commit — the fallback must NOT override a real history."""
    root = _first_release_repo(tmp_path)
    # Cut a real release commit touching pyproject.toml + a marker file.
    (root / "RELEASE_MARKER.txt").write_text("released\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('version = "0.2.0"\n', encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore(release): 0.2.0")
    # A further feat commit so there's something to release next.
    (root / "feature.txt").write_text("x\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: add a thing")

    files = _canonical_bump_files(root, "0.2.0")
    # The historical release commit wins — the marker it touched is canonical.
    assert "RELEASE_MARKER.txt" in files
    # And the fallback set (the version scan) must NOT have leaked the feature
    # file in: it isn't version-embedding and wasn't in the release commit.
    assert "feature.txt" not in files


def test_gather_snapshot_first_release_has_nonempty_bump_plan(
    tmp_path: Path,
) -> None:
    """End-to-end: the first-release snapshot's bump plan is non-empty and
    carries the version-embedding file (was ``[]`` before the fix)."""
    root = _first_release_repo(tmp_path)
    snap = gather_snapshot(root, master_ci_conclusion="success")
    assert snap.canonical_bump_files  # non-empty
    assert "pyproject.toml" in snap.canonical_bump_files


def test_assess_first_release_version_bump_plan_is_nonempty(
    tmp_path: Path,
) -> None:
    """The CEO-reviewable report for a first release actually plans to bump the
    version-embedding file — the executor will not publish a no-op tag."""
    root = _first_release_repo(tmp_path)
    report = assess(
        gather_snapshot(root, master_ci_conclusion="success"),
        today=_TODAY,
    )
    assert report.version_bump_plan  # non-empty
    assert "pyproject.toml" in report.version_bump_plan


def test_first_release_emits_no_version_ref_gap_for_planned_files(
    tmp_path: Path,
) -> None:
    """On the first release the bump plan equals the version-reference scan, so
    no file is flagged as 'holds the version but not in the plan' — the fallback
    closes the gap the empty plan used to fabricate."""
    root = _first_release_repo(tmp_path)
    report = assess(
        gather_snapshot(root, master_ci_conclusion="success"),
        today=_TODAY,
    )
    assert not any(g.category == "version_ref" for g in report.gaps)


def test_canonical_bump_files_ignores_body_only_chore_release_match(
    tmp_path: Path,
) -> None:
    """A non-release commit whose message BODY references ``chore(release):``
    (a body line starting with ``chore(release):``) must NOT be misidentified
    as the last release commit.

    ``git log --grep "^chore(release):"`` matches ANY message line, so a
    fix/docs commit that explains the release process (body line
    ``chore(release): the canonical set ...``) is matched and — being newer —
    shadows the real release commit. The canonical set must come from the
    commit whose SUBJECT starts with ``chore(release):`` (the real release
    shape ``chore(release): X.Y.Z``), not a body-only match. (Regression: an
    earlier fix commit's body referencing ``chore(release):`` was picked up,
    so the bump plan listed that fix's files instead of the release's.)
    """
    root = _first_release_repo(tmp_path)
    # Real release commit (older) touching the version-embedding file + marker.
    (root / "RELEASE_MARKER.txt").write_text("released\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('version = "0.2.0"\n', encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore(release): 0.2.0")
    # A NEWER non-release commit whose BODY has a line starting with the
    # release-commit prefix.
    (root / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(
        root,
        "commit",
        "-m",
        "fix: tighten release-readiness derivation",
        "-m",
        "chore(release): the canonical set derives from this commit type",
    )

    files = _canonical_bump_files(root, "0.2.0")
    # The canonical set is the REAL release commit's files...
    assert "RELEASE_MARKER.txt" in files
    assert "pyproject.toml" in files
    # ...NOT the body-only-matching fix commit's file.
    assert "other.py" not in files


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
