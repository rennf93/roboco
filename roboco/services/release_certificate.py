"""Release certificate assembly — the exportable proof a release was governed.

Packages one published release's full gate chain into a single artifact: the
CI verdict and changelog excerpt from the proposal's stored readiness report,
a distinct conventions-clean verdict, per-AC QA pass states for every task in
the release, a findings-ledger summary (open/closed/waived by severity), and
the CEO approval timestamp. The CEO-gated route delegates here; this layer
owns every DB read.

Release membership has no schema link — the release proposal task
(``source=release_manager``) is the only durable release record, so the task
set is the delivery tasks ``COMPLETED`` inside the window between the previous
published release of the SAME project and this one (journaled dev decision;
first release takes everything before its own completion) — the readiness
report assesses one project's changes, so a foreign project's task sitting
inside the window must not leak into another project's certificate. Engine-held
artifacts (the proposal itself, X posts, video drafts, …) are excluded — they
are coordination artifacts, not delivered work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from roboco.db.tables import TaskTable
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.release_readiness import report_from_dict
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.services.task import HUMAN_AUTHORED_SOURCES, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.db.tables import TaskReviewFindingTable

# The criterion-stamp prefix pass_review renders into qa_notes; a task's
# verified-criterion count is its '[AC] …' line count.
_AC_MARK = "[AC] "

# Findings whose status means fixed — `addressed` (claimed fixed) or
# `verified` (confirmed by a later review round).
_CLOSED_FINDING_STATUSES = ("addressed", "verified")

_SEVERITIES = ("blocker", "major", "minor", "nit")


@dataclass(frozen=True)
class CertificateTaskState:
    """One release task's per-AC QA pass state (see the response schema)."""

    task_id: str
    title: str
    status: str
    criteria_total: int
    criteria_verified: int
    qa_passed: bool


@dataclass(frozen=True)
class SeverityCounts:
    """Finding counts in one ledger bucket, broken down by severity."""

    blocker: int
    major: int
    minor: int
    nit: int


@dataclass(frozen=True)
class FindingsSummary:
    """Findings-ledger summary across the release task set."""

    open: SeverityCounts
    closed: SeverityCounts
    waived: SeverityCounts


@dataclass(frozen=True)
class ReleaseCertificate:
    """The assembled certificate payload (serialized by the response schema)."""

    version: str
    generated_at: datetime
    ci_verdict: str
    conventions_clean: bool
    ceo_approved_at: datetime | None
    changelog_excerpt: str
    task_states: list[CertificateTaskState]
    findings_summary: FindingsSummary


def normalized_version(raw: str) -> str:
    """Canonical form: trimmed, leading ``v`` dropped (tags vs CHANGELOG refs)."""
    return raw.strip().removeprefix("v")


def _zero_counts() -> dict[str, int]:
    return dict.fromkeys(_SEVERITIES, 0)


def _verified_criteria_count(qa_notes: str | None) -> int:
    """How many acceptance criteria QA stamped as verified in the notes."""
    if not qa_notes:
        return 0
    return sum(1 for line in qa_notes.splitlines() if line.startswith(_AC_MARK))


def _counts_by_severity(
    rows: list[TaskReviewFindingTable], statuses: frozenset[str]
) -> SeverityCounts:
    """Counts for the ledger rows whose status falls in *statuses*."""
    counts = _zero_counts()
    for row in rows:
        if row.status not in statuses:
            continue
        severity = str(row.severity)
        if severity in counts:
            counts[severity] += 1
    return SeverityCounts(**counts)


def _target_for(proposals: list[TaskTable], version: str) -> TaskTable | None:
    """The first completed proposal whose stored report proposes *version*."""
    for task in proposals:
        report_dict = markers.get_release_report(task)
        if (
            report_dict is not None
            and report_from_dict(report_dict).proposed_version == version
        ):
            return task
    return None


def _same_project_previous(
    proposals: list[TaskTable], target: TaskTable
) -> datetime | None:
    """The latest same-project publication strictly before *target*'s."""
    publish_at = target.completed_at
    if publish_at is None:  # the caller only calls this on a completed target
        return None
    project_id = target.project_id
    previous: datetime | None = None
    for task in proposals:
        if task.id == target.id or task.completed_at is None:
            continue
        if project_id is not None and task.project_id != project_id:
            continue
        if task.completed_at < publish_at:
            previous = task.completed_at
    return previous


class ReleaseCertificateService(BaseService):
    """Assembles the release certificate for one published version."""

    async def build_certificate(self, raw_version: str) -> ReleaseCertificate | None:
        """The certificate for a published ``version`` (``v`` prefix tolerated).

        Returns None — the caller renders a 404 — when no COMPLETED release
        proposal carries the version in its stored readiness report.
        """
        version = normalized_version(raw_version)
        target, previous_completed_at = await self._published_proposal(version)
        if target is None or target.completed_at is None:
            return None
        report = report_from_dict(markers.get_release_report(target) or {})
        tasks = await self._release_task_set(
            proposal=target, after=previous_completed_at
        )
        return ReleaseCertificate(
            version=version,
            generated_at=datetime.now(UTC),
            ci_verdict=report.gate_state,
            # Classification gaps only — 'gate' gaps report CI red, which the
            # certificate already carries as ci_verdict; counting them here
            # would double-report one signal as two failures.
            conventions_clean=not any(
                gap.category == "classification" for gap in report.gaps
            ),
            ceo_approved_at=target.completed_at,
            changelog_excerpt=report.drafted_changelog,
            task_states=[self._task_state(t) for t in tasks],
            findings_summary=await self._findings_summary(tasks),
        )

    async def _published_proposal(
        self, version: str
    ) -> tuple[TaskTable | None, datetime | None]:
        """The COMPLETED proposal for *version* and the previous SAME-PROJECT
        publication's ``completed_at`` (None for the first release of that
        project). Proposals are ordered by completion, so the last same-project
        non-matching one before the target is the previous publication.
        """
        proposals = await get_task_service(
            self.session
        ).list_completed_release_proposals()
        target = _target_for(proposals, version)
        if target is None or target.completed_at is None:
            return target, None
        return target, _same_project_previous(proposals, target)

    async def _release_task_set(
        self, proposal: TaskTable, after: datetime | None
    ) -> list[TaskTable]:
        """The release's delivery tasks: COMPLETED inside the window between
        the previous publication and this proposal's own completion, ordered
        chronologically. Scoped to the proposal's own project — the readiness
        report assesses that project's changes, so another project's task
        completed inside the window must not leak into this certificate.
        ``source`` restricted to the human-authored delivery sources keeps
        engine-held artifacts (the proposal itself, X posts, video drafts)
        out — engine-originated tasks carry their engine constants, delivery
        work is "manual"/"prompter".
        """
        publish_at = proposal.completed_at
        if publish_at is None:  # pragma: no cover - caller guards on this
            return []
        stmt = (
            select(TaskTable)
            .where(
                TaskTable.status == TaskStatus.COMPLETED,
                TaskTable.completed_at <= publish_at,
                TaskTable.source.in_(HUMAN_AUTHORED_SOURCES),
            )
            .order_by(TaskTable.completed_at, TaskTable.created_at)
        )
        if proposal.project_id is not None:
            # ponytail: None-project proposals aren't producible today; if one
            # ever appears, fall back to unfiltered rather than guess a scope.
            stmt = stmt.where(TaskTable.project_id == proposal.project_id)
        if after is not None:
            stmt = stmt.where(TaskTable.completed_at > after)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _task_state(task: TaskTable) -> CertificateTaskState:
        total = len(task.acceptance_criteria or [])
        verified = _verified_criteria_count(task.qa_notes)
        return CertificateTaskState(
            task_id=str(task.id),
            title=task.title,
            status=str(getattr(task.status, "value", task.status)),
            criteria_total=total,
            criteria_verified=verified,
            qa_passed=verified >= total,
        )

    async def _findings_summary(self, tasks: list[TaskTable]) -> FindingsSummary:
        """Aggregate the ledger across the release task set (bounded task
        count — release windows are days, not years — so per-task queries
        stay comfortably bounded)."""
        repo = ReviewFindingsRepository(self.session)
        rows: list[TaskReviewFindingTable] = []
        for task in tasks:
            rows.extend(await repo.list_for_task(cast("UUID", task.id)))
        return FindingsSummary(
            open=_counts_by_severity(rows, frozenset({"open"})),
            closed=_counts_by_severity(rows, frozenset(_CLOSED_FINDING_STATUSES)),
            waived=_counts_by_severity(rows, frozenset({"waived"})),
        )
