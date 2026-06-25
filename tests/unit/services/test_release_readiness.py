"""Pure release-readiness primitives: classify changes + derive semver bump.

These are git-free so they're unit-testable from synthetic commits. The
git-backed assess() is covered in test_release_readiness_audit.py (Task 3).
"""

from __future__ import annotations

from roboco.services.release_readiness import (
    CommitInfo,
    classify_changes,
    derive_bump,
    next_version,
)


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
