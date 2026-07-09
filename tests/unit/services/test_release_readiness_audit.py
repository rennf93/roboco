"""The readiness audit: assess() turns a repo snapshot into a gap-flagged report.

assess() is pure over a ``ReleaseRepoSnapshot`` so every "no stone unturned"
check (changelog/version-ref/docs-drift/migration/gate completeness) is tested
from synthetic data. The git-backed gather_snapshot() is smoke-tested against
the real repo at the bottom.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from roboco.services.release_readiness import (
    CommitInfo,
    ReleaseReadinessReport,
    ReleaseRepoSnapshot,
    assess,
    gather_snapshot,
)

_TODAY = "2026-06-25"
_DECLARED = 25
_DRIFTED = 26


def _snap(**overrides: Any) -> ReleaseRepoSnapshot:
    base = ReleaseRepoSnapshot(
        current_version="0.12.0",
        last_tag="v0.12.0",
        commits=[CommitInfo(sha="a1", subject="feat: add a thing", pr_number=1)],
        tracked_files_with_version=["pyproject.toml"],
        canonical_bump_files=["pyproject.toml"],
        changelog_text="## [Unreleased]\n### Added\n- add a thing (#1)\n",
        new_migrations=[],
        migration_head_count=1,
        master_ci_conclusion="success",
        declared_agent_count=_DECLARED,
        actual_agent_count=_DECLARED,
        verb_tables_stale=False,
    )
    return replace(base, **overrides)


def _categories(report: ReleaseReadinessReport) -> set[str]:
    return {gap.category for gap in report.gaps}


def test_assess_proposes_next_version_and_bump() -> None:
    report = assess(_snap(), today=_TODAY)
    assert report.bump_kind == "minor"
    assert report.proposed_version == "0.13.0"
    assert report.gate_state == "green"


def test_clean_snapshot_has_no_gaps() -> None:
    assert assess(_snap(), today=_TODAY).gaps == []


def test_undocumented_commit_is_a_changelog_gap() -> None:
    snap = _snap(
        commits=[CommitInfo(sha="a1", subject="feat: undocumented", pr_number=99)],
        changelog_text="## [Unreleased]\n",
    )
    assert "changelog" in _categories(assess(snap, today=_TODAY))


def test_chore_commit_does_not_need_a_changelog_line() -> None:
    snap = _snap(
        commits=[CommitInfo(sha="a1", subject="chore: tidy imports", pr_number=7)],
        changelog_text="## [Unreleased]\n",
    )
    assert "changelog" not in _categories(assess(snap, today=_TODAY))


def test_missed_version_ref_is_a_gap() -> None:
    snap = _snap(
        tracked_files_with_version=["pyproject.toml", "panel/pnpm-lock.yaml"],
        canonical_bump_files=["pyproject.toml"],
    )
    report = assess(snap, today=_TODAY)
    version_gaps = [g for g in report.gaps if g.category == "version_ref"]
    assert any("pnpm-lock.yaml" in g.detail for g in version_gaps)


def test_bump_plan_is_the_canonical_set() -> None:
    snap = _snap(canonical_bump_files=["pyproject.toml", "roboco/__init__.py"])
    report = assess(snap, today=_TODAY)
    assert report.version_bump_plan == ["pyproject.toml", "roboco/__init__.py"]


def test_stale_agent_count_is_docs_drift_gap() -> None:
    snap = _snap(declared_agent_count=_DECLARED, actual_agent_count=_DRIFTED)
    assert "docs_drift" in _categories(assess(snap, today=_TODAY))


def test_stale_verb_tables_is_docs_drift_gap() -> None:
    assert "docs_drift" in _categories(
        assess(_snap(verb_tables_stale=True), today=_TODAY)
    )


def test_new_migration_listed_in_notes() -> None:
    snap = _snap(new_migrations=["alembic/versions/050_playbooks.py"])
    report = assess(snap, today=_TODAY)
    assert any("050_playbooks" in note for note in report.migration_notes)


def test_multiple_alembic_heads_is_a_migration_gap() -> None:
    head_count = 2
    assert "migration" in _categories(
        assess(_snap(migration_head_count=head_count), today=_TODAY)
    )


def test_red_ci_is_a_gate_gap() -> None:
    report = assess(_snap(master_ci_conclusion="failure"), today=_TODAY)
    assert report.gate_state == "red"
    assert "gate" in _categories(report)


def test_unknown_ci_is_a_gate_gap() -> None:
    report = assess(_snap(master_ci_conclusion=None), today=_TODAY)
    assert report.gate_state == "unknown"
    assert "gate" in _categories(report)


def test_unclassifiable_commit_is_a_classification_gap() -> None:
    snap = _snap(
        commits=[CommitInfo(sha="a1", subject="random merge subject", pr_number=5)],
        changelog_text="- random merge subject (#5)\n",
    )
    assert "classification" in _categories(assess(snap, today=_TODAY))


def test_drafted_changelog_is_keepachangelog_and_single_line() -> None:
    snap = _snap(
        commits=[
            CommitInfo(sha="a1", subject="feat: add A", pr_number=1),
            CommitInfo(sha="b2", subject="fix: fix B", pr_number=2),
        ],
        changelog_text="- add A (#1)\n- fix B (#2)\n",
    )
    report = assess(snap, today=_TODAY)
    assert "## [0.13.0] - 2026-06-25" in report.drafted_changelog
    assert "### Added" in report.drafted_changelog
    assert "### Fixed" in report.drafted_changelog
    bullets = [
        ln for ln in report.drafted_changelog.splitlines() if ln.startswith("- ")
    ]
    assert len(bullets) == 2  # noqa: PLR2004 - exactly the two commits above


# --- gather_snapshot: real-repo smoke (this repo is a git checkout at 0.12.0) ---


def test_gather_snapshot_reads_the_real_repo() -> None:
    root = Path(__file__).resolve().parents[3]
    snap = gather_snapshot(root, master_ci_conclusion=None)
    # The repo version moves with each release — assert it's a semver, not a literal.
    assert re.fullmatch(r"\d+\.\d+\.\d+", snap.current_version)
    # last_tag depends on the ambient checkout, not on gather_snapshot's logic:
    # agent dev/QA workspaces clone with --no-tags by design (workspace.py's
    # _clone_repo), so git describe legitimately finds nothing there, while CI
    # (ci.yml pins fetch-tags: true) and the production release-manager read
    # clone (workspace.py's _sync_read_clone) both fetch tags explicitly.
    # Assert the shape wherever a tag IS present instead of assuming one
    # always exists, so this stays meaningful in both kinds of checkout.
    assert snap.last_tag is None or re.fullmatch(r"v\d+\.\d+\.\d+", snap.last_tag)
    assert isinstance(snap.commits, list)
    assert "pyproject.toml" in snap.canonical_bump_files
    assert snap.changelog_text  # CHANGELOG.md is non-empty
    assert snap.migration_head_count >= 1


def test_gather_snapshot_then_assess_produces_a_report() -> None:
    root = Path(__file__).resolve().parents[3]
    report = assess(gather_snapshot(root, master_ci_conclusion="success"), today=_TODAY)
    assert report.proposed_version
    assert report.bump_kind in {"major", "minor", "patch"}
    assert report.gate_state == "green"
