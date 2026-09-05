"""Assembles the per-task verification attestation — a single, auditable
snapshot proving how a task was verified, sourced entirely from existing
tables/stamps: no new columns, tables, or capture points.

Every field below is read, never written, from data another part of the
lifecycle already persists:

* ``task_review_findings`` (``ReviewFindingsRepository``) — the full
  revision-findings ledger, grouped by round.
* the ``qa_notes`` per-AC verification stamps — the ``"[AC] <criterion> —
  verified: <evidence>"`` lines ``pass_review``'s ``criteria_verified``
  renders into ``qa_notes`` (see ``choreographer/qa.py``
  ``_render_criteria_verified``).
* ``work_sessions`` — the commit/branch/PR refs each dev round is bound to.
* ``audit_log`` — the task's status-transition custody chain (who moved it
  through which status, and when).
* ``project_convention_findings`` — the architectural-conventions findings
  already recorded for this task at its ``i_am_done`` gate.

This is leaf 1 of 2: leaf 2 (the route + Markdown render + tests) consumes
``assemble_task_attestation``'s return value directly, so its shape is the
real contract — kept as plain, frozen dataclasses (not Pydantic/SQLAlchemy
models; see ``roboco.api.schemas.attestation`` for the mirrored response
schemas leaf 2 builds from these).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from roboco.db.tables import (
    AgentTable,
    AuditLogTable,
    ProjectConventionFindingTable,
    TaskTable,
    WorkSessionTable,
)
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

# Matches the exact line ``QAMixin._render_criteria_verified`` produces:
# "[AC] <criterion> — verified: <evidence>".
_AC_STAMP_RE = re.compile(r"^\[AC\]\s+(.+?)\s+—\s+verified:\s+(.+)$")

_CI_NOT_AVAILABLE_STATE = "not_available"


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


@dataclass(frozen=True)
class AttestedCriterion:
    """One acceptance criterion plus its verification stamp, if any."""

    id: str | None
    text: str
    verified: bool
    evidence: str | None = None


@dataclass(frozen=True)
class AttestedFinding:
    """One row of the revision-findings ledger (``task_review_findings``)."""

    id: str
    round: int
    origin: str
    severity: str
    status: str
    file: str | None
    line: int | None
    criterion: str | None
    expected: str
    actual: str
    fix: str | None
    resolution_note: str | None
    created_at: datetime


@dataclass(frozen=True)
class FindingsRound:
    """Every ledger row raised in one revision round."""

    round: int
    findings: tuple[AttestedFinding, ...]


@dataclass(frozen=True)
class CiVerdict:
    """The CI verdict for the PR's head commit (``GitService.get_pr_ci_status``'s
    own state vocabulary), or ``not_available`` when no PR/``git_service`` was
    supplied to the assembler."""

    state: str
    head_sha: str | None = None
    failing_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConventionFinding:
    """One recorded architectural-conventions finding for this task's diff."""

    file: str
    line: int
    rule: str
    level: str
    kind: str | None
    message: str


@dataclass(frozen=True)
class ReviewerChainEntry:
    """One status-transition hop in the task's custody chain."""

    to_status: str
    agent_slug: str | None
    agent_role: str | None
    timestamp: datetime


@dataclass(frozen=True)
class WorkSessionRef:
    """One work session bound to this task — the commit/PR refs an outside
    auditor can verify against the real git history."""

    agent_slug: str | None
    branch_name: str
    base_branch: str
    target_branch: str
    status: str
    commits: tuple[str, ...]
    pr_number: int | None
    pr_url: str | None
    pr_status: str | None
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True)
class TaskAttestation:
    """The full per-task verification attestation."""

    task_id: str
    title: str
    status: str
    team: str
    project_slug: str | None
    branch_name: str | None
    pr_number: int | None
    pr_url: str | None
    revision_count: int
    commits: tuple[dict[str, Any], ...]
    work_sessions: tuple[WorkSessionRef, ...]
    acceptance_criteria: tuple[AttestedCriterion, ...]
    findings_by_round: tuple[FindingsRound, ...]
    ci: CiVerdict
    conventions_findings: tuple[ConventionFinding, ...]
    reviewer_chain: tuple[ReviewerChainEntry, ...]
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _parse_ac_stamps(qa_notes: str | None) -> dict[str, str]:
    """``{criterion text: evidence}`` parsed out of ``qa_notes``'s ``[AC]``
    lines. Best-effort text parsing of a deterministic rendering, not a new
    capture point — the stamps already live in ``qa_notes``; this just reads
    them back out."""
    if not qa_notes:
        return {}
    stamps: dict[str, str] = {}
    for line in qa_notes.splitlines():
        m = _AC_STAMP_RE.match(line.strip())
        if m:
            stamps[m.group(1).strip()] = m.group(2).strip()
    return stamps


def _attested_criteria(task: TaskTable) -> tuple[AttestedCriterion, ...]:
    stamps = _parse_ac_stamps(task.qa_notes)
    ac_ids = list(task.acceptance_criteria_ids or [])
    return tuple(
        AttestedCriterion(
            id=ac_ids[idx] if idx < len(ac_ids) else None,
            text=text,
            verified=text in stamps,
            evidence=stamps.get(text),
        )
        for idx, text in enumerate(task.acceptance_criteria or [])
    )


async def _findings_by_round(
    session: AsyncSession, task_id: UUID
) -> tuple[FindingsRound, ...]:
    rows = await ReviewFindingsRepository(session).list_for_task(task_id, limit=500)
    by_round: dict[int, list[AttestedFinding]] = {}
    for row in rows:
        by_round.setdefault(row.round, []).append(
            AttestedFinding(
                id=str(row.id),
                round=row.round,
                origin=row.origin,
                severity=row.severity,
                status=row.status,
                file=row.file,
                line=row.line,
                criterion=row.criterion,
                expected=row.expected,
                actual=row.actual,
                fix=row.fix,
                resolution_note=row.resolution_note,
                created_at=row.created_at,
            )
        )
    return tuple(
        FindingsRound(round=r, findings=tuple(by_round[r])) for r in sorted(by_round)
    )


async def _conventions_findings(
    session: AsyncSession, task_id: UUID
) -> tuple[ConventionFinding, ...]:
    result = await session.execute(
        select(ProjectConventionFindingTable)
        .where(ProjectConventionFindingTable.task_id == task_id)
        .order_by(ProjectConventionFindingTable.detected_at.desc())
    )
    return tuple(
        ConventionFinding(
            file=row.file,
            line=row.line,
            rule=row.rule,
            level=row.level,
            kind=row.kind,
            message=row.message,
        )
        for row in result.scalars().all()
    )


async def _reviewer_chain(
    session: AsyncSession, task_id: UUID
) -> tuple[ReviewerChainEntry, ...]:
    """The custody chain: one entry per generic ``task.<to_status>`` audit
    row, oldest first. Skips the rejector-attributed duplicate rows
    (``task.qa_fail`` / ``task.pr_fail`` / ``task.request_changes`` /
    ``task.ceo_reject``, emitted alongside the generic row by
    ``TaskService._audit_events_for`` for rework attribution) so each real
    transition appears exactly once."""
    result = await session.execute(
        select(AuditLogTable, AgentTable.slug)
        .outerjoin(AgentTable, AgentTable.id == AuditLogTable.agent_id)
        .where(
            AuditLogTable.target_type == "task",
            AuditLogTable.target_id == task_id,
            AuditLogTable.event_type.like("task.%"),
        )
        .order_by(AuditLogTable.timestamp.asc())
    )
    entries: list[ReviewerChainEntry] = []
    for row, agent_slug in result.all():
        details = row.details or {}
        to_status = details.get("to_status")
        if not to_status or row.event_type != f"task.{to_status}":
            continue
        entries.append(
            ReviewerChainEntry(
                to_status=to_status,
                agent_slug=agent_slug,
                agent_role=details.get("agent_role"),
                timestamp=row.timestamp,
            )
        )
    return tuple(entries)


async def _work_sessions(
    session: AsyncSession, task_id: UUID
) -> tuple[WorkSessionRef, ...]:
    result = await session.execute(
        select(WorkSessionTable, AgentTable.slug)
        .outerjoin(AgentTable, AgentTable.id == WorkSessionTable.agent_id)
        .where(WorkSessionTable.task_id == task_id)
        .order_by(WorkSessionTable.started_at.asc())
    )
    return tuple(
        WorkSessionRef(
            agent_slug=agent_slug,
            branch_name=row.branch_name,
            base_branch=row.base_branch,
            target_branch=row.target_branch,
            status=_enum_value(row.status),
            commits=tuple(row.commits or ()),
            pr_number=row.pr_number,
            pr_url=row.pr_url,
            pr_status=row.pr_status,
            started_at=row.started_at,
            ended_at=row.ended_at,
        )
        for row, agent_slug in result.all()
    )


async def _ci_verdict(
    git_service: Any, project_slug: str | None, pr_number: int | None
) -> CiVerdict:
    """The PR head commit's CI verdict via ``git_service.get_pr_ci_status``
    (duck-typed — a real ``GitService`` in production, a fake in tests).
    ``not_available`` when no PR exists yet or the caller supplied no
    ``git_service``/``project_slug`` (no live call is attempted)."""
    if git_service is None or project_slug is None or pr_number is None:
        return CiVerdict(state=_CI_NOT_AVAILABLE_STATE)
    try:
        result = await git_service.get_pr_ci_status(project_slug, pr_number)
    except Exception:
        return CiVerdict(state="error")
    if not result:
        return CiVerdict(state=_CI_NOT_AVAILABLE_STATE)
    return CiVerdict(
        state=str(result.get("state", "unknown")),
        head_sha=result.get("head_sha"),
        failing_checks=tuple(result.get("failing_checks") or ()),
    )


async def assemble_task_attestation(
    session: AsyncSession,
    task: TaskTable,
    *,
    project_slug: str | None = None,
    git_service: Any = None,
) -> TaskAttestation:
    """Assemble the full verification attestation for ``task``.

    ``project_slug``/``git_service`` are optional and duck-typed (the caller
    passes an already-resolved ``GitService`` instance) purely to fetch the
    live CI verdict — every other field comes from the DB rows already
    passed in or reachable from ``session``. No new capture point: every
    source here (``task_review_findings``, ``qa_notes``, ``work_sessions``,
    ``audit_log``, ``project_convention_findings``) is written elsewhere in
    the lifecycle; this only reads.
    """
    task_id = require_uuid(task.id)
    return TaskAttestation(
        task_id=str(task.id),
        title=task.title,
        status=_enum_value(task.status),
        team=_enum_value(task.team),
        project_slug=project_slug,
        branch_name=task.branch_name,
        pr_number=task.pr_number,
        pr_url=task.pr_url,
        revision_count=task.revision_count or 0,
        commits=tuple(task.commits or ()),
        work_sessions=await _work_sessions(session, task_id),
        acceptance_criteria=_attested_criteria(task),
        findings_by_round=await _findings_by_round(session, task_id),
        ci=await _ci_verdict(git_service, project_slug, task.pr_number),
        conventions_findings=await _conventions_findings(session, task_id),
        reviewer_chain=await _reviewer_chain(session, task_id),
    )
