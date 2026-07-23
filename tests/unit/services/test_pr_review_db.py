"""PR-review TaskService methods against a real Postgres DB.

The dedup/claim/complete/supersede helpers in TaskService run real SQL and
real lifecycle transitions, so the mock-based tests in
``test_external_pr_ingest.py`` can't prove they actually persist. These seed a
project + reviewer agent in the test DB and exercise the full round-trip:

- ``ingest_external_pr`` creates one review task and de-dupes per head SHA;
- ``pr_review_claim`` / ``complete_review`` drive the planless, branchless
  pending -> in_progress -> completed lifecycle the reviewer uses;
- ``create_supersede_umbrella`` / ``find_supersede_umbrella`` create and
  idempotently locate the takeover coordination task;
- ``list_external_pr_reviews`` isolates PR-review work from regular tasks —
  the same source filter the orchestrator's dispatchers key on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.task import TaskCreateRequest, TaskService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# PR numbers / counts used in the round-trips, named so comparisons aren't
# "magic values" (ruff PLR2004) and the intent reads at the assertion site.
EXTERNAL_PR = 170
INTERNAL_PR = 42
REVIEWS_AFTER_REREVIEW = 2


async def _seed(session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    """Seed a system agent + pr_reviewer agent + project.

    Returns ``(project_id, system_agent_id, reviewer_agent_id)`` — the FK
    targets every PR-review method needs (created_by, audit attribution,
    project the PR belongs to).
    """
    system_agent = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(system_agent)
    await session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="PR Review Test Project",
        slug=f"prreview-{uuid4().hex[:8]}",
        git_url="https://github.com/example/prreview.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_agent.id,
        is_active=True,
    )
    session.add(project)
    await session.flush()

    reviewer = AgentTable(
        id=uuid4(),
        name="PR Reviewer",
        slug=f"pr-reviewer-{uuid4().hex[:8]}",
        role=AgentRole.PR_REVIEWER,
        team=Team.SYSTEM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="reviewer",
        capabilities=["review"],
        permissions={},
        metrics={},
    )
    session.add(reviewer)
    await session.flush()

    return (
        UUID(str(project.id)),
        UUID(str(system_agent.id)),
        UUID(str(reviewer.id)),
    )


def _pr(number: int, head_sha: str, *, title: str = "Add feature") -> dict[str, object]:
    return {
        "number": number,
        "url": f"https://github.com/example/prreview/pull/{number}",
        "title": title,
        "head_sha": head_sha,
    }


# ---------------------------------------------------------------------------
# ingest_external_pr — create-once + head-SHA dedup, persisted.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_creates_review_task(db_session: AsyncSession) -> None:
    project_id, system_id, _ = await _seed(db_session)
    svc = TaskService(db_session)

    task = await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(EXTERNAL_PR, "abc123"),
        created_by=system_id,
        team=Team.SYSTEM,
    )
    await db_session.flush()

    assert task is not None
    assert task.source == "external_pr"
    assert task.pr_number == EXTERNAL_PR
    assert task.task_type == TaskType.CODE
    assert task.confirmed_by_human is False
    assert task.status == TaskStatus.PENDING
    assert task.orchestration_markers == {"external_pr_head": "abc123"}


@pytest.mark.asyncio
async def test_ingest_dedups_same_head(db_session: AsyncSession) -> None:
    project_id, system_id, _ = await _seed(db_session)
    svc = TaskService(db_session)

    first = await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(EXTERNAL_PR, "abc123"),
        created_by=system_id,
        team=Team.SYSTEM,
    )
    await db_session.flush()
    second = await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(EXTERNAL_PR, "abc123"),
        created_by=system_id,
        team=Team.SYSTEM,
    )
    await db_session.flush()

    assert first is not None
    assert second is None  # unchanged head -> skip
    assert (
        await svc.external_review_task_exists(project_id, EXTERNAL_PR, "abc123") is True
    )


@pytest.mark.asyncio
async def test_ingest_new_head_rereviews(db_session: AsyncSession) -> None:
    project_id, system_id, _ = await _seed(db_session)
    svc = TaskService(db_session)

    await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(EXTERNAL_PR, "abc123"),
        created_by=system_id,
        team=Team.SYSTEM,
    )
    await db_session.flush()
    # PR got new commits -> new head SHA -> a fresh review is opened.
    rereview = await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(EXTERNAL_PR, "def456"),
        created_by=system_id,
        team=Team.SYSTEM,
    )
    await db_session.flush()

    assert rereview is not None
    assert rereview.orchestration_markers == {"external_pr_head": "def456"}
    reviews = await svc.list_external_pr_reviews()
    matching = [t for t in reviews if t.pr_number == EXTERNAL_PR]
    assert len(matching) == REVIEWS_AFTER_REREVIEW


@pytest.mark.asyncio
async def test_ingest_internal_pr_source(db_session: AsyncSession) -> None:
    project_id, system_id, _ = await _seed(db_session)
    svc = TaskService(db_session)

    task = await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(INTERNAL_PR, "aaa111"),
        created_by=system_id,
        team=Team.SYSTEM,
        source="internal_pr",
    )
    await db_session.flush()

    assert task is not None
    assert task.source == "internal_pr"
    assert "internal PR #42" in task.title


# ---------------------------------------------------------------------------
# pr_review_claim / complete_review — planless, branchless lifecycle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_review_claim_and_complete(db_session: AsyncSession) -> None:
    project_id, system_id, reviewer_id = await _seed(db_session)
    svc = TaskService(db_session)

    review = await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(EXTERNAL_PR, "abc123"),
        created_by=system_id,
        team=Team.SYSTEM,
    )
    await db_session.flush()
    assert review is not None
    task_id = UUID(str(review.id))

    # Claim: pending -> in_progress, no plan, no branch.
    claimed = await svc.pr_review_claim(reviewer_id, task_id)
    await db_session.flush()
    assert claimed is not None
    assert claimed.status == TaskStatus.IN_PROGRESS
    assert UUID(str(claimed.claimed_by)) == reviewer_id
    assert claimed.branch_name in (None, "")
    # Heartbeat MUST be seeded at claim — a NULL heartbeat reads as a stale
    # claim to the reaper and, for a GROK reviewer, trips the idle-kill
    # watchdog before the review is posted (the wedge/respawn loop).
    assert claimed.last_heartbeat_at is not None
    # Single-claimant invariant (mirrors _qa_or_doc_claim / _finalize_claim):
    # active_claimant_id is what _active_claim_violation checks for content
    # writes (note/evidence). Without it the reviewer can claim + read the PR
    # but every note() returns _not_active_claimant -> the journal:learning
    # entry post_pr_review's tracing gate requires can never be recorded ->
    # the reviewer deadlocks at post_pr_review (tracing_gap) and burns tokens
    # into the do-server breaker. claimed_at is set for parity with QA/doc.
    assert claimed.active_claimant_id is not None
    assert UUID(str(claimed.active_claimant_id)) == reviewer_id
    assert claimed.claimed_at is not None
    # The external-PR-review claim chokepoint flips the reviewer's fleet
    # marker too — otherwise pr_reviewer agents never show as active.
    reviewer_row = await db_session.get(AgentTable, reviewer_id)
    assert reviewer_row is not None
    assert reviewer_row.status == AgentStatus.ACTIVE
    assert reviewer_row.current_task_id == task_id

    # Re-claiming a non-pending task is a no-op.
    assert await svc.pr_review_claim(reviewer_id, task_id) is None

    # Complete: in_progress -> completed, claim cleared, review in its OWN slot.
    done = await svc.complete_review(reviewer_id, task_id, notes="Posted review.")
    await db_session.flush()
    assert done is not None
    assert done.status == TaskStatus.COMPLETED
    # Reviewer content lands in pr_reviewer_notes (not qa_notes) and is structured.
    assert "Posted review." in (done.pr_reviewer_notes or "")
    assert done.qa_notes is None
    assert (done.notes_structured or {}).get("pr_review", {}).get("verdict")
    assert done.claimed_by is None
    # Single-claimant lock cleared on completion (the review hand-off is done).
    assert done.active_claimant_id is None
    # complete_review releases the reviewer's fleet marker too.
    reviewer_row = await db_session.get(AgentTable, reviewer_id)
    assert reviewer_row is not None
    assert reviewer_row.current_task_id is None

    # Re-completing a completed task is a no-op.
    assert await svc.complete_review(reviewer_id, task_id) is None


@pytest.mark.asyncio
async def test_complete_review_requires_in_progress(db_session: AsyncSession) -> None:
    project_id, system_id, reviewer_id = await _seed(db_session)
    svc = TaskService(db_session)

    review = await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(EXTERNAL_PR, "abc123"),
        created_by=system_id,
        team=Team.SYSTEM,
    )
    await db_session.flush()
    assert review is not None

    # Still PENDING (never claimed) -> complete is rejected.
    assert await svc.complete_review(reviewer_id, UUID(str(review.id))) is None


# ---------------------------------------------------------------------------
# create_supersede_umbrella / find_supersede_umbrella — idempotent takeover.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_find_supersede_umbrella(db_session: AsyncSession) -> None:
    project_id, system_id, _ = await _seed(db_session)
    svc = TaskService(db_session)

    review = await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(EXTERNAL_PR, "abc123"),
        created_by=system_id,
        team=Team.SYSTEM,
    )
    await db_session.flush()
    assert review is not None
    review_id = UUID(str(review.id))

    umbrella = await svc.create_supersede_umbrella(
        review_task_id=review_id,
        branch_name="feature/system/supersede-170",
        created_by=system_id,
    )
    await db_session.flush()
    assert umbrella is not None
    assert umbrella.source == "external_pr_supersede"
    assert umbrella.team == Team.MAIN_PM
    assert umbrella.branch_name == "feature/system/supersede-170"
    assert umbrella.confirmed_by_human is True

    found = await svc.find_supersede_umbrella(project_id, EXTERNAL_PR)
    assert found is not None
    assert UUID(str(found.id)) == UUID(str(umbrella.id))
    # No umbrella for an unrelated PR.
    assert await svc.find_supersede_umbrella(project_id, 999) is None


@pytest.mark.asyncio
async def test_create_supersede_rejects_non_review(db_session: AsyncSession) -> None:
    project_id, system_id, _ = await _seed(db_session)
    svc = TaskService(db_session)

    # A regular (source='manual') task is not a PR review -> no umbrella.
    regular = await svc.create(
        TaskCreateRequest(
            title="Regular work",
            description="not a review",
            acceptance_criteria=["done"],
            team=Team.BACKEND,
            created_by=system_id,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=project_id,
        )
    )
    await db_session.flush()

    assert (
        await svc.create_supersede_umbrella(
            review_task_id=UUID(str(regular.id)),
            branch_name="feature/system/x",
            created_by=system_id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_find_supersede_umbrella_no_prefix_false_match(
    db_session: AsyncSession,
) -> None:
    """``pr=5`` must not match the umbrella for ``pr=50`` (exact marker match)."""
    project_id, system_id, _ = await _seed(db_session)
    svc = TaskService(db_session)

    for number, sha in ((5, "sha5"), (50, "sha50")):
        review = await svc.ingest_external_pr(
            project_id=project_id,
            pr=_pr(number, sha),
            created_by=system_id,
            team=Team.SYSTEM,
        )
        await db_session.flush()
        assert review is not None
        await svc.create_supersede_umbrella(
            review_task_id=UUID(str(review.id)),
            branch_name=f"feature/system/supersede-{number}",
            created_by=system_id,
        )
        await db_session.flush()

    five = await svc.find_supersede_umbrella(project_id, 5)
    fifty = await svc.find_supersede_umbrella(project_id, 50)
    assert five is not None
    assert fifty is not None
    assert UUID(str(five.id)) != UUID(str(fifty.id))
    assert "pr=5 review=" in (five.orchestration_markers or {}).get(
        "external_pr_supersede", ""
    )
    assert "pr=50 review=" in (fifty.orchestration_markers or {}).get(
        "external_pr_supersede", ""
    )


# ---------------------------------------------------------------------------
# Source isolation — what the dispatchers key on.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_listing_isolates_pr_sources(db_session: AsyncSession) -> None:
    """list_external_pr_reviews returns only PR-review-sourced tasks.

    The orchestrator's dev/PM dispatchers skip ``PR_REVIEW_SOURCES`` and only
    the pr_reviewer claims them; this pins the data-layer half of that contract
    — a regular task in the same project never leaks into the review queue.
    """
    project_id, system_id, _ = await _seed(db_session)
    svc = TaskService(db_session)

    await svc.create(
        TaskCreateRequest(
            title="Regular backend work",
            description="real delivery task",
            acceptance_criteria=["ships"],
            team=Team.BACKEND,
            created_by=system_id,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=project_id,
        )
    )
    await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(EXTERNAL_PR, "abc"),
        created_by=system_id,
        team=Team.SYSTEM,
    )
    await svc.ingest_external_pr(
        project_id=project_id,
        pr=_pr(INTERNAL_PR, "def"),
        created_by=system_id,
        team=Team.SYSTEM,
        source="internal_pr",
    )
    await db_session.flush()

    reviews = await svc.list_external_pr_reviews()
    assert {t.source for t in reviews} == {"external_pr", "internal_pr"}
    assert {t.pr_number for t in reviews} == {EXTERNAL_PR, INTERNAL_PR}
