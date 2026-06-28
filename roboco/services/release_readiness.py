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
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

BumpKind = Literal["major", "minor", "patch"]

# Kinds that must carry a CHANGELOG line; pure chore/docs/test noise is exempt
# so the completeness audit does not flag housekeeping commits.
_CHANGELOG_REQUIRED_KINDS = frozenset({"feat", "fix", "security", "perf", "refactor"})

# Classified kind -> Keep-a-Changelog section.
_KIND_SECTION: dict[str, str] = {
    "feat": "Added",
    "fix": "Fixed",
    "security": "Security",
    "perf": "Changed",
    "refactor": "Changed",
    "docs": "Changed",
    "chore": "Changed",
    "other": "Changed",
}
_SECTION_ORDER = ("Added", "Changed", "Fixed", "Security")

_PR_REF_RE = re.compile(r"\(#(\d+)\)")
_REVISION_RE = re.compile(r"^revision\s*[:=].*$", re.MULTILINE)
_DOWN_REVISION_RE = re.compile(r"^down_revision\s*[:=].*$", re.MULTILINE)
_QUOTED_RE = re.compile(r"""["']([^"']+)["']""")
_DECLARED_AGENTS_RE = re.compile(r"(\d+)\s+AI\s+agents", re.IGNORECASE)

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


@dataclass(frozen=True)
class Gap:
    """One readiness shortfall the CEO must see before approving a release."""

    # changelog | version_ref | docs_drift | migration | gate | classification
    category: str
    detail: str


@dataclass(frozen=True)
class ReleaseRepoSnapshot:
    """Raw release-readiness facts gathered from the repo (git + filesystem).

    Pure data so ``assess`` is fully unit-testable; ``gather_snapshot`` builds it
    from a real checkout.
    """

    current_version: str
    last_tag: str | None
    commits: list[CommitInfo]
    tracked_files_with_version: list[str]
    canonical_bump_files: list[str]
    changelog_text: str
    new_migrations: list[str]
    migration_head_count: int
    master_ci_conclusion: str | None
    declared_agent_count: int | None = None
    actual_agent_count: int | None = None
    verb_tables_stale: bool = False


@dataclass(frozen=True)
class ReleaseReadinessReport:
    """The deterministic release proposal the CEO approves or rejects."""

    proposed_version: str
    bump_kind: BumpKind
    change_summary: list[str]
    drafted_changelog: str
    version_bump_plan: list[str]
    gaps: list[Gap]
    migration_notes: list[str]
    gate_state: str


def _is_documented(change: ClassifiedChange, changelog_text: str) -> bool:
    pr = change.commit.pr_number
    if pr is not None and f"#{pr}" in changelog_text:
        return True
    return bool(change.summary) and change.summary in changelog_text


def _draft_changelog(version: str, changes: list[ClassifiedChange], today: str) -> str:
    sections: dict[str, list[str]] = {}
    for change in changes:
        section = _KIND_SECTION.get(change.kind, "Changed")
        bullet = change.summary
        if change.commit.pr_number is not None:
            bullet = f"{bullet} (#{change.commit.pr_number})"
        sections.setdefault(section, []).append(f"- {bullet}")
    lines = [f"## [{version}] - {today}", ""]
    for section in _SECTION_ORDER:
        bullets = sections.get(section)
        if bullets:
            lines.append(f"### {section}")
            lines.extend(bullets)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _changelog_gaps(changes: list[ClassifiedChange], changelog_text: str) -> list[Gap]:
    gaps = []
    for change in changes:
        if change.kind not in _CHANGELOG_REQUIRED_KINDS:
            continue
        if not _is_documented(change, changelog_text):
            ref = f" (#{change.commit.pr_number})" if change.commit.pr_number else ""
            gaps.append(
                Gap("changelog", f"{change.summary}{ref} is not in the CHANGELOG")
            )
    return gaps


def _version_ref_gaps(snapshot: ReleaseRepoSnapshot) -> list[Gap]:
    planned = set(snapshot.canonical_bump_files)
    return [
        Gap(
            "version_ref",
            f"{path} holds {snapshot.current_version} but is not in the bump plan",
        )
        for path in snapshot.tracked_files_with_version
        if path not in planned
    ]


def _docs_drift_gaps(snapshot: ReleaseRepoSnapshot) -> list[Gap]:
    gaps = []
    declared, actual = snapshot.declared_agent_count, snapshot.actual_agent_count
    if declared is not None and actual is not None and declared != actual:
        gaps.append(
            Gap(
                "docs_drift",
                f"declared agent count {declared} != actual {actual}; update the docs",
            )
        )
    if snapshot.verb_tables_stale:
        gaps.append(
            Gap(
                "docs_drift",
                "verb-surface tables are stale — run scripts/regenerate_verb_tables.py",
            )
        )
    return gaps


def _migration_gaps_and_notes(
    snapshot: ReleaseRepoSnapshot,
) -> tuple[list[Gap], list[str]]:
    gaps, notes = [], []
    if snapshot.new_migrations:
        names = ", ".join(Path(path).name for path in snapshot.new_migrations)
        notes.append(
            f"Run `alembic upgrade head` — {len(snapshot.new_migrations)} new "
            f"migration(s): {names}"
        )
    if snapshot.migration_head_count > 1:
        gaps.append(
            Gap(
                "migration",
                f"{snapshot.migration_head_count} alembic heads — the chain must "
                "have a single head",
            )
        )
    return gaps, notes


def _gate_state(conclusion: str | None) -> str:
    if conclusion == "success":
        return "green"
    if conclusion is None:
        return "unknown"
    return "red"


def assess(snapshot: ReleaseRepoSnapshot, *, today: str) -> ReleaseReadinessReport:
    """Turn a repo snapshot into a gap-flagged, CEO-reviewable release proposal."""
    changes = classify_changes(snapshot.commits)
    bump = derive_bump(changes)
    proposed = next_version(snapshot.current_version, bump)

    gaps: list[Gap] = []
    gaps.extend(_changelog_gaps(changes, snapshot.changelog_text))
    gaps.extend(_version_ref_gaps(snapshot))
    gaps.extend(_docs_drift_gaps(snapshot))
    migration_gaps, migration_notes = _migration_gaps_and_notes(snapshot)
    gaps.extend(migration_gaps)
    gaps.extend(
        Gap("classification", f"{c.commit.subject!r} could not be classified")
        for c in changes
        if c.needs_manual_classification
    )

    gate_state = _gate_state(snapshot.master_ci_conclusion)
    if gate_state != "green":
        gaps.append(
            Gap("gate", f"master CI is {gate_state}; a release needs a green gate")
        )

    return ReleaseReadinessReport(
        proposed_version=proposed,
        bump_kind=bump,
        change_summary=[f"{c.kind}: {c.summary}" for c in changes],
        drafted_changelog=_draft_changelog(proposed, changes, today),
        version_bump_plan=list(snapshot.canonical_bump_files),
        gaps=gaps,
        migration_notes=migration_notes,
        gate_state=gate_state,
    )


# --------------------------------------------------------------------------- #
# gather_snapshot — the git + filesystem I/O that builds a real snapshot.
# Read-only: it never mutates the working tree (the design forbids it).
# --------------------------------------------------------------------------- #

_GIT_RECORD_SEP = "\x1e"
_GIT_FIELD_SEP = "\x1f"


def _run_git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def _pyproject_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def _last_tag(root: Path) -> str | None:
    tag = _run_git(root, ["describe", "--tags", "--abbrev=0"]).strip()
    return tag or None


def _commits_since(root: Path, tag: str | None) -> list[CommitInfo]:
    rng = f"{tag}..HEAD" if tag else "HEAD"
    fmt = f"%H{_GIT_FIELD_SEP}%s{_GIT_FIELD_SEP}%b{_GIT_RECORD_SEP}"
    raw = _run_git(root, ["log", rng, f"--format={fmt}"])
    commits = []
    for raw_record in raw.split(_GIT_RECORD_SEP):
        record = raw_record.strip()
        if not record:
            continue
        sha, subject, body = [*record.split(_GIT_FIELD_SEP), "", "", ""][:3]
        pr_match = _PR_REF_RE.search(subject)
        commits.append(
            CommitInfo(
                sha=sha.strip(),
                subject=subject.strip(),
                body=body.strip(),
                pr_number=int(pr_match.group(1)) if pr_match else None,
            )
        )
    return commits


def _tracked_files_with_version(root: Path, version: str) -> list[str]:
    # Exclude tests/ — fixtures legitimately embed version strings and are not
    # bump targets; flagging them would be pure noise.
    raw = _run_git(root, ["grep", "-lF", version, "--", ".", ":(exclude)tests/"])
    return sorted(line.strip() for line in raw.splitlines() if line.strip())


def _canonical_bump_files(root: Path, version: str) -> list[str]:
    # Subsequent releases derive the canonical bump set from the previous
    # ``chore(release):`` commit's touched files — the historical record of
    # what a release bumps.
    sha = _run_git(
        root, ["log", "--grep", "^chore(release):", "-n1", "--format=%H"]
    ).strip()
    if sha:
        raw = _run_git(root, ["show", "--name-only", "--format=", sha])
        return sorted(line.strip() for line in raw.splitlines() if line.strip())
    # F058: the FIRST release has no prior ``chore(release):`` commit, so the
    # historical derivation returns ``[]`` and the executor would publish a tag
    # with no files bumped (a no-op release masquerading as X.Y.Z). Fall back to
    # the version-reference scan — the files currently embedding the version are
    # exactly the set a first release must bump, and the set the first release
    # commit then records as canonical for every subsequent release. Read-only.
    return _tracked_files_with_version(root, version)


def _new_migrations(root: Path, tag: str | None) -> list[str]:
    if not tag:
        return []
    raw = _run_git(
        root,
        [
            "diff",
            "--name-only",
            "--diff-filter=A",
            f"{tag}..HEAD",
            "--",
            "alembic/versions/",
        ],
    )
    return sorted(line.strip() for line in raw.splitlines() if line.strip())


def _migration_head_count(root: Path) -> int:
    versions = root / "alembic" / "versions"
    if not versions.is_dir():
        return 1
    revisions: set[str] = set()
    referenced: set[str] = set()
    for path in versions.glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        rev_line = _REVISION_RE.search(text)
        if rev_line is not None:
            tokens = _QUOTED_RE.findall(rev_line.group(0))
            if tokens:
                revisions.add(tokens[0])
        down_line = _DOWN_REVISION_RE.search(text)
        if down_line is not None:
            referenced.update(_QUOTED_RE.findall(down_line.group(0)))
    heads = revisions - referenced
    return len(heads) if heads else 1


def _declared_agent_count(root: Path) -> int | None:
    try:
        text = (root / "roboco" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = _DECLARED_AGENTS_RE.search(text)
    return int(match.group(1)) if match else None


def _actual_agent_count() -> int | None:
    try:
        from roboco.foundation.identity import AGENTS

        return sum(
            1 for row in AGENTS.values() if row.role.value not in {"system", "ceo"}
        )
    except Exception:
        # Best-effort drift signal; a failure here must never block a release.
        return None


def gather_snapshot(
    root: Path, *, master_ci_conclusion: str | None
) -> ReleaseRepoSnapshot:
    """Build a ReleaseRepoSnapshot from a real checkout (read-only).

    ``master_ci_conclusion`` is injected (the engine fetches it via GitService) so
    this stays pure git + filesystem. ``verb_tables_stale`` is left False here:
    reliably detecting it requires running the regen script (which writes files),
    and the read-only sweep must not mutate the tree — the fail-closed executor's
    gate catches a stale generated artifact instead.
    """
    version = _pyproject_version(root)
    tag = _last_tag(root)
    return ReleaseRepoSnapshot(
        current_version=version,
        last_tag=tag,
        commits=_commits_since(root, tag),
        tracked_files_with_version=_tracked_files_with_version(root, version),
        canonical_bump_files=_canonical_bump_files(root, version),
        changelog_text=_read_changelog(root),
        new_migrations=_new_migrations(root, tag),
        migration_head_count=_migration_head_count(root),
        master_ci_conclusion=master_ci_conclusion,
        declared_agent_count=_declared_agent_count(root),
        actual_agent_count=_actual_agent_count(),
        verb_tables_stale=False,
    )


def _read_changelog(root: Path) -> str:
    try:
        return (root / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def report_to_dict(report: ReleaseReadinessReport) -> dict[str, Any]:
    """Serialize a report to a plain dict for JSONB storage on the proposal task."""
    return {
        "proposed_version": report.proposed_version,
        "bump_kind": report.bump_kind,
        "change_summary": list(report.change_summary),
        "drafted_changelog": report.drafted_changelog,
        "version_bump_plan": list(report.version_bump_plan),
        "gaps": [
            {"category": gap.category, "detail": gap.detail} for gap in report.gaps
        ],
        "migration_notes": list(report.migration_notes),
        "gate_state": report.gate_state,
    }


def report_from_dict(data: dict[str, Any]) -> ReleaseReadinessReport:
    """Rebuild a report from its stored dict (the inverse of ``report_to_dict``)."""
    return ReleaseReadinessReport(
        proposed_version=data["proposed_version"],
        bump_kind=data["bump_kind"],
        change_summary=list(data.get("change_summary", [])),
        drafted_changelog=data.get("drafted_changelog", ""),
        version_bump_plan=list(data.get("version_bump_plan", [])),
        gaps=[
            Gap(category=g["category"], detail=g["detail"])
            for g in data.get("gaps", [])
        ],
        migration_notes=list(data.get("migration_notes", [])),
        gate_state=data.get("gate_state", "unknown"),
    )
