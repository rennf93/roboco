"""The release-proposal verification rollup, read in bulk.

The panel's rollup used to compose each member task's verification receipt
from per-task requests: the task (acceptance criteria + ``qa_notes``), its
findings ledger, its task metrics (reviewer chain), its attestation (CI
verdict) and the project's conventions feed. A release carrying 74 member
tasks meant about 300 requests on mount, repeated for every refetch of the
``["tasks"]`` query prefix; on 2026-10-28 (05:23, then 177-310 requests a
minute at 05:34-05:36) that exhausted the orchestrator's DB pool ("QueuePool
limit of size 10 overflow 20 reached") and the guard's Redis pool ("Too many
connections").

Two reads replace it, one request each:

* :func:`read_release_member_receipts` reads every member's receipt from the
  database with a fixed number of queries on the caller's session, however
  many members the release carries.
* :func:`read_release_member_ci_verdicts` reads each member PR's CI verdict
  from the forge, :data:`CI_LOOKUP_CONCURRENCY` at a time, each lookup on its
  own background-pool session, so a large release neither serialises behind
  one connection nor fans out without bound, and never competes with
  agent-facing traffic on the primary pool.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from roboco.db.base import get_db_context
from roboco.db.tables import ProjectTable, TaskTable
from roboco.services.attestation import CiVerdict, pr_ci_verdict
from roboco.services.conventions import convention_findings_for_tasks
from roboco.services.git import get_git_service
from roboco.services.metrics import reviewer_chains_for_tasks
from roboco.services.release_proposal import member_task_ids_for_proposal
from roboco.services.repositories.review_findings import ReviewFindingsRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskReviewFindingTable
    from roboco.models.metrics import ReviewerChainEntry

# Forge lookups in flight at once for one rollup: each holds a background-pool
# connection and makes two or three forge API calls.
CI_LOOKUP_CONCURRENCY = 4


@dataclass(frozen=True)
class MemberReceipt:
    """One member task's verification receipt, as the rollup renders it."""

    task_id: UUID
    title: str
    acceptance_criteria: tuple[str, ...]
    qa_notes: str | None
    findings: tuple[TaskReviewFindingTable, ...]
    conventions_findings: tuple[dict[str, Any], ...]
    reviewer_chain: tuple[ReviewerChainEntry, ...]


@dataclass(frozen=True)
class MemberCiVerdict:
    """One member task's PR CI verdict."""

    task_id: UUID
    ci: CiVerdict


def _member_ids(members: list[dict[str, Any]]) -> list[UUID]:
    return [UUID(str(m["task_id"])) for m in members]


async def _member_task_rows(
    session: AsyncSession, task_ids: Sequence[UUID]
) -> dict[UUID, Any]:
    result = await session.execute(
        select(
            TaskTable.id,
            TaskTable.title,
            TaskTable.acceptance_criteria,
            TaskTable.qa_notes,
        ).where(TaskTable.id.in_(task_ids))
    )
    return {UUID(str(row.id)): row for row in result.all()}


async def _findings_by_task(
    session: AsyncSession, task_ids: Sequence[UUID]
) -> dict[UUID, list[TaskReviewFindingTable]]:
    grouped: dict[UUID, list[TaskReviewFindingTable]] = {}
    for row in await ReviewFindingsRepository(session).list_for_tasks(task_ids):
        grouped.setdefault(UUID(str(row.task_id)), []).append(row)
    return grouped


async def _conventions_by_task(
    session: AsyncSession, task_ids: Sequence[UUID]
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in await convention_findings_for_tasks(session, task_ids):
        grouped.setdefault(str(finding["task_id"]), []).append(finding)
    return grouped


async def read_release_member_receipts(
    session: AsyncSession, proposal: TaskTable
) -> list[MemberReceipt]:
    """Every member task's receipt, in ``member_task_ids_for_proposal`` order."""
    task_ids = _member_ids(await member_task_ids_for_proposal(session, proposal))
    tasks = await _member_task_rows(session, task_ids)
    findings = await _findings_by_task(session, task_ids)
    conventions = await _conventions_by_task(session, task_ids)
    chains = await reviewer_chains_for_tasks(session, task_ids)
    return [
        MemberReceipt(
            task_id=task_id,
            title=tasks[task_id].title,
            acceptance_criteria=tuple(tasks[task_id].acceptance_criteria or ()),
            qa_notes=tasks[task_id].qa_notes,
            findings=tuple(findings.get(task_id, ())),
            conventions_findings=tuple(conventions.get(str(task_id), ())),
            reviewer_chain=tuple(chains.get(str(task_id), ())),
        )
        for task_id in task_ids
        if task_id in tasks
    ]


async def _proposal_project_slug(
    session: AsyncSession, proposal: TaskTable
) -> str | None:
    if proposal.project_id is None:
        return None
    result = await session.execute(
        select(ProjectTable.slug).where(ProjectTable.id == proposal.project_id)
    )
    return result.scalar_one_or_none()


async def _read_pr_ci_verdict(
    gate: asyncio.Semaphore, project_slug: str, pr_number: int | None
) -> CiVerdict:
    if pr_number is None:
        return await pr_ci_verdict(None, project_slug, None)
    async with gate, get_db_context(pool="background") as lookup_session:
        return await pr_ci_verdict(
            get_git_service(lookup_session), project_slug, pr_number
        )


async def read_release_member_ci_verdicts(
    session: AsyncSession, proposal: TaskTable
) -> list[MemberCiVerdict]:
    """Every member task's PR CI verdict, in ``member_task_ids_for_proposal``
    order; ``not_available`` for a member without a PR or a project-less
    proposal (no forge call is made for either)."""
    members = await member_task_ids_for_proposal(session, proposal)
    project_slug = await _proposal_project_slug(session, proposal)
    if project_slug is None:
        unavailable = await pr_ci_verdict(None, None, None)
        return [
            MemberCiVerdict(task_id, unavailable) for task_id in _member_ids(members)
        ]
    gate = asyncio.Semaphore(CI_LOOKUP_CONCURRENCY)
    verdicts = await asyncio.gather(
        *(_read_pr_ci_verdict(gate, project_slug, m.get("pr_number")) for m in members)
    )
    return [
        MemberCiVerdict(task_id, ci)
        for task_id, ci in zip(_member_ids(members), verdicts, strict=True)
    ]
