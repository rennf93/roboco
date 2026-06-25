"""Release-readiness assessment — conventional-commit classification + semver.

The pure, git-free primitives live here (Task 2): ``classify_changes`` maps each
commit to a normalized kind, ``derive_bump`` reduces the set to a semver bump,
and ``next_version`` applies it. They take synthetic input so the deterministic
correctness this feature exists to guarantee is unit-tested without git.

The git-backed ``assess()`` that produces the full ``ReleaseReadinessReport``
(diff-since-tag, version-ref / CHANGELOG completeness, docs drift, migrations,
gate state) is layered on top of these primitives in Task 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

BumpKind = Literal["major", "minor", "patch"]

# Conventional-commit type -> normalized kind. Non-release-shaping types
# (test/build/ci/style) collapse to "chore" so they count only as a patch.
_CONVENTIONAL_TYPES: dict[str, str] = {
    "feat": "feat",
    "fix": "fix",
    "security": "security",
    "perf": "perf",
    "refactor": "refactor",
    "docs": "docs",
    "chore": "chore",
    "test": "chore",
    "build": "chore",
    "ci": "chore",
    "style": "chore",
    "revert": "fix",
}

# PR-label fallback when the subject is not conventional-commit shaped.
_LABEL_KIND: dict[str, str] = {
    "feature": "feat",
    "enhancement": "feat",
    "bug": "fix",
    "bugfix": "fix",
    "fix": "fix",
    "security": "security",
    "performance": "perf",
    "perf": "perf",
    "documentation": "docs",
    "docs": "docs",
    "chore": "chore",
    "dependencies": "chore",
    "refactor": "refactor",
}

_BREAKING_LABELS = {"breaking", "breaking-change", "breaking change", "major"}

_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-z]+)(?P<scope>\([^)]*\))?(?P<bang>!)?:\s*(?P<summary>.+)$",
    re.IGNORECASE,
)
_BREAKING_BODY_RE = re.compile(r"BREAKING[ -]CHANGE", re.IGNORECASE)


@dataclass(frozen=True)
class CommitInfo:
    """One commit since the last release tag, as parsed from ``git log``."""

    sha: str
    subject: str
    body: str = ""
    pr_number: int | None = None
    labels: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ClassifiedChange:
    """A commit annotated with its release-relevant kind + breaking flag."""

    commit: CommitInfo
    kind: str
    breaking: bool
    summary: str
    needs_manual_classification: bool


def _has_breaking_label(labels: tuple[str, ...]) -> bool:
    return any(lbl.strip().lower() in _BREAKING_LABELS for lbl in labels)


def _classify_one(commit: CommitInfo) -> ClassifiedChange:
    breaking = bool(commit.body) and _BREAKING_BODY_RE.search(commit.body) is not None
    breaking = breaking or _has_breaking_label(commit.labels)

    match = _CONVENTIONAL_RE.match(commit.subject.strip())
    if match is not None:
        kind = _CONVENTIONAL_TYPES.get(match.group("type").lower())
        if kind is not None:
            return ClassifiedChange(
                commit=commit,
                kind=kind,
                breaking=breaking or bool(match.group("bang")),
                summary=match.group("summary").strip(),
                needs_manual_classification=False,
            )

    # Subject is not conventional — fall back to PR labels before giving up.
    for label in commit.labels:
        kind = _LABEL_KIND.get(label.strip().lower())
        if kind is not None:
            return ClassifiedChange(
                commit=commit,
                kind=kind,
                breaking=breaking,
                summary=commit.subject.strip(),
                needs_manual_classification=False,
            )

    return ClassifiedChange(
        commit=commit,
        kind="other",
        breaking=breaking,
        summary=commit.subject.strip(),
        needs_manual_classification=True,
    )


def classify_changes(commits: list[CommitInfo]) -> list[ClassifiedChange]:
    """Classify each commit by conventional-commit prefix, then PR-label fallback."""
    return [_classify_one(commit) for commit in commits]


def derive_bump(changes: list[ClassifiedChange]) -> BumpKind:
    """Reduce the change set to a semver bump: breaking>feat>everything-else."""
    if any(change.breaking for change in changes):
        return "major"
    if any(change.kind == "feat" for change in changes):
        return "minor"
    return "patch"


def next_version(current: str, bump: BumpKind) -> str:
    """Apply ``bump`` to a ``MAJOR.MINOR.PATCH`` string (a leading ``v`` is ok)."""
    major, minor, patch = (int(part) for part in current.strip().lstrip("v").split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"
