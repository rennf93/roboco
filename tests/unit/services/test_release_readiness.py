"""Pure release-readiness primitives: classify changes + derive semver bump.

These are git-free so they're unit-testable from synthetic commits. The
git-backed assess() is covered in test_release_readiness_audit.py (Task 3).
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from roboco.services.release_readiness import (
    CommitInfo,
    _commits_since,
    _draft_changelog,
    _run_git,
    classify_changes,
    derive_bump,
    next_version,
)

if TYPE_CHECKING:
    from pathlib import Path


def _commit(subject: str, body: str = "", labels: tuple[str, ...] = ()) -> CommitInfo:
    return CommitInfo(sha="abc1234", subject=subject, body=body, labels=labels)


def test_feat_drives_minor_bump() -> None:
    changes = classify_changes([_commit("feat: add X"), _commit("fix: a bug")])
    assert derive_bump(changes) == "minor"


def test_only_fix_and_chore_is_patch() -> None:
    changes = classify_changes([_commit("fix: a bug"), _commit("chore: bump deps")])
    assert derive_bump(changes) == "patch"


def test_bang_marker_drives_major() -> None:
    changes = classify_changes([_commit("feat!: drop the old API")])
    assert derive_bump(changes) == "major"


def test_breaking_change_body_drives_major() -> None:
    changes = classify_changes(
        [_commit("feat: new thing", body="BREAKING CHANGE: removes Y")]
    )
    assert derive_bump(changes) == "major"


def test_security_change_is_patch_when_not_breaking() -> None:
    changes = classify_changes([_commit("security: patch a CVE")])
    assert derive_bump(changes) == "patch"


def test_empty_change_set_is_patch() -> None:
    assert derive_bump([]) == "patch"


def test_next_version_minor() -> None:
    assert next_version("0.8.0", "minor") == "0.9.0"


def test_next_version_patch() -> None:
    assert next_version("0.8.0", "patch") == "0.8.1"


def test_next_version_major() -> None:
    assert next_version("0.8.0", "major") == "1.0.0"


def test_next_version_tolerates_v_prefix() -> None:
    assert next_version("v0.12.0", "minor") == "0.13.0"


def test_classify_extracts_kind_and_summary() -> None:
    [change] = classify_changes([_commit("feat(api): add endpoint (#12)")])
    assert change.kind == "feat"
    assert change.breaking is False
    assert change.summary == "add endpoint (#12)"
    assert change.needs_manual_classification is False


def test_unknown_subject_flags_manual_classification() -> None:
    [change] = classify_changes([_commit("Random merge subject")])
    assert change.kind == "other"
    assert change.needs_manual_classification is True


def test_pr_label_fallback_classifies_unconventional_subject() -> None:
    [change] = classify_changes([_commit("Random subject", labels=("bug",))])
    assert change.kind == "fix"
    assert change.needs_manual_classification is False


def test_breaking_label_drives_major_even_on_unconventional_subject() -> None:
    changes = classify_changes([_commit("Big rework", labels=("breaking",))])
    assert derive_bump(changes) == "major"


# --- _run_git: a hung git child must bubble a clear error, not hang silently ---


def test_run_git_timeout_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _hang(*_a: object, **_kw: object) -> object:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=30)

    monkeypatch.setattr(subprocess, "run", _hang)
    with pytest.raises(RuntimeError, match="timed out after 30s"):
        _run_git(tmp_path, ["log"])


# --- _commits_since: an embedded \x1f in a body must stay in the body field ---


def test_commits_since_preserves_field_sep_in_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sha = "deadbeef"
    subject = "feat: a thing (#1)"
    body = "BREAKING CHANGE: drops\x1fold api"
    raw = f"{sha}\x1f{subject}\x1f{body}\x1e"

    def _fake_run_git(_root: Path, args: list[str]) -> str:
        # Only the log call is expected; return the crafted raw record.
        assert args[0] == "log"
        return raw

    monkeypatch.setattr("roboco.services.release_readiness._run_git", _fake_run_git)
    commits = _commits_since(tmp_path, tag=None)
    assert len(commits) == 1
    [commit] = commits
    assert commit.sha == sha
    assert commit.subject == subject
    assert commit.body == body  # the embedded \x1f is preserved
    assert commit.pr_number == 1


def test_draft_changelog_uses_curated_unreleased_body() -> None:
    changes = classify_changes(
        [CommitInfo(sha="a" * 8, subject="feat: shiny thing", body="")]
    )
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n"
        "- **Shiny thing.** Curated prose about it.\n\n"
        "## [0.24.0] - 2026-07-14\n\n- old\n"
    )
    draft = _draft_changelog("0.25.0", changes, "2026-07-15", changelog)
    assert draft.startswith("## [0.25.0] - 2026-07-15")
    assert "Curated prose about it." in draft
    assert "feat: shiny thing" not in draft
    assert "[Unreleased]" not in draft


def test_draft_changelog_falls_back_to_transcription_without_curation() -> None:
    changes = classify_changes(
        [CommitInfo(sha="a" * 8, subject="feat: shiny thing", body="")]
    )
    empty = "# Changelog\n\n## [Unreleased]\n\n## [0.24.0] - 2026-07-14\n"
    draft = _draft_changelog("0.25.0", changes, "2026-07-15", empty)
    assert "### Added" in draft
    assert "- shiny thing" in draft
