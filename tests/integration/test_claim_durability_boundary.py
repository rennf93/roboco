"""Real-Postgres regression: the claim commit survives a mid-evidence-assembly
cancellation for all three claim-then-assemble verbs.

The durability boundary fix commits the claim BEFORE the advisory evidence
assembly begins. If the request times out at the 120s bound, ``get_db``
catches the ``CancelledError`` and invalidates the session — but the claim
was already committed in a prior transaction, so it persists. The retry
resumes into an already-claimed task instead of re-racing for it.

Each test seeds a task, commits the claim, simulates the cancellation by
discarding the session, opens a fresh session, and asserts the claim
(``active_claimant_id`` + ``qa_evidence_inspected`` where applicable) is
still in the DB.
"""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.task import TaskService
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


async def _fresh_session(url: str) -> tuple[AsyncSession, AsyncEngine]:
    engine = create_async_engine(url, future=True)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    return factory(), engine


async def _dispose(session: AsyncSession, engine: AsyncEngine) -> None:
    with contextlib.suppress(Exception):
        await session.rollback()
    await engine.dispose()


async def _seed(
    session: AsyncSession, *, status: TaskStatus, assignee_id: UUID | None = None
) -> dict[str, Any]:
    """Seed a system agent, project, a QA agent, and a task in ``status``."""
    system = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(system)
    await session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="Durability Test",
        slug=f"dur-{uuid4().hex[:8]}",
        git_url="https://github.com/example/dur.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system.id,
        is_active=True,
    )
    session.add(project)
    await session.flush()

    qa_agent = AgentTable(
        id=uuid4(),
        name="BE QA",
        slug="be-qa",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(qa_agent)
    await session.flush()

    task = TaskTable(
        id=uuid4(),
        title="Test task",
        description="test",
        status=status,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=system.id,
        assigned_to=assignee_id,
        branch_name="feature/backend/test",
        acceptance_criteria=["AC1"],
    )
    session.add(task)
    await session.flush()

    return {
        "system": system,
        "project": project,
        "qa_agent": qa_agent,
        "task": task,
    }


# ---------------------------------------------------------------------------
# claim_review — claim survives session invalidation post-commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_claim_survives_post_commit_cancellation(
    _test_database_url: str,
) -> None:
    """claim_review commits the claim + mark_evidence_inspected, then the
    evidence assembly is cancelled (simulating a 120s timeout). The claim
    must persist — a fresh session reads active_claimant_id + qa_evidence_inspected."""
    url = _test_database_url
    claim_session, claim_engine = await _fresh_session(url)
    try:
        seeded = await _seed(claim_session, status=TaskStatus.AWAITING_QA)
        qa_id = seeded["qa_agent"].id
        task_id = seeded["task"].id
        await claim_session.commit()

        # Commit the claim + mark evidence inspected (the durability boundary).
        ts = TaskService(claim_session)
        t = await ts.qa_claim(qa_id, task_id)
        assert t is not None, "qa_claim should succeed on an unclaimed task"
        await ts.mark_evidence_inspected(task_id)
        await claim_session.commit()

        # Simulate the cancellation: the evidence assembly is interrupted.
        # get_db catches CancelledError and calls session.invalidate(),
        # discarding any uncommitted state. Since the claim was already
        # committed, it survives. We simulate this by just discarding the
        # session — no rollback needed since the commit already landed.
    finally:
        await _dispose(claim_session, claim_engine)

    # A fresh session (the retry request) reads the committed claim.
    verify_session, verify_engine = await _fresh_session(url)
    try:
        ts2 = TaskService(verify_session)
        t2 = await ts2.get(task_id)
        assert t2 is not None, "task should exist"
        assert str(t2.status) == TaskStatus.AWAITING_QA.value
        assert t2.active_claimant_id == qa_id, (
            "active_claimant_id must survive the cancellation — the claim "
            "was committed before the evidence assembly was interrupted"
        )
        assert t2.qa_evidence_inspected is True, (
            "qa_evidence_inspected must survive the cancellation — it was "
            "committed alongside the claim"
        )
    finally:
        await _dispose(verify_session, verify_engine)


# ---------------------------------------------------------------------------
# claim_gate_review — claim survives session invalidation post-commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_gate_claim_survives_post_commit_cancellation(
    _test_database_url: str,
) -> None:
    """claim_gate_review commits the pr_gate_claim, then the evidence assembly
    is cancelled. The claim must persist — a fresh session reads
    active_claimant_id."""
    url = _test_database_url
    claim_session, claim_engine = await _fresh_session(url)
    try:
        seeded = await _seed(claim_session, status=TaskStatus.AWAITING_PR_REVIEW)
        reviewer_id = seeded["qa_agent"].id
        task_id = seeded["task"].id
        await claim_session.commit()

        ts = TaskService(claim_session)
        t = await ts.pr_gate_claim(reviewer_id, task_id)
        assert t is not None, "pr_gate_claim should succeed on an unclaimed task"
        await claim_session.commit()
    finally:
        await _dispose(claim_session, claim_engine)

    verify_session, verify_engine = await _fresh_session(url)
    try:
        ts2 = TaskService(verify_session)
        t2 = await ts2.get(task_id)
        assert t2 is not None
        assert str(t2.status) == TaskStatus.AWAITING_PR_REVIEW.value
        assert t2.active_claimant_id == reviewer_id, (
            "active_claimant_id must survive the cancellation"
        )
    finally:
        await _dispose(verify_session, verify_engine)


# ---------------------------------------------------------------------------
# claim_doc_task — claim survives session invalidation post-commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doc_claim_survives_post_commit_cancellation(
    _test_database_url: str,
) -> None:
    """claim_doc_task commits the doc_claim, then the evidence assembly is
    cancelled. The claim must persist — a fresh session reads
    active_claimant_id."""
    url = _test_database_url
    claim_session, claim_engine = await _fresh_session(url)
    try:
        seeded = await _seed(claim_session, status=TaskStatus.AWAITING_DOCUMENTATION)
        doc_id = seeded["qa_agent"].id
        task_id = seeded["task"].id
        await claim_session.commit()

        ts = TaskService(claim_session)
        t = await ts.doc_claim(doc_id, task_id)
        assert t is not None, "doc_claim should succeed on an unclaimed task"
        await claim_session.commit()
    finally:
        await _dispose(claim_session, claim_engine)

    verify_session, verify_engine = await _fresh_session(url)
    try:
        ts2 = TaskService(verify_session)
        t2 = await ts2.get(task_id)
        assert t2 is not None
        assert str(t2.status) == TaskStatus.AWAITING_DOCUMENTATION.value
        assert t2.active_claimant_id == doc_id, (
            "active_claimant_id must survive the cancellation"
        )
    finally:
        await _dispose(verify_session, verify_engine)


# ---------------------------------------------------------------------------
# Same-agent retry: the committed claim is detected and the claim is skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_claim_same_agent_reclaim_is_idempotent(
    _test_database_url: str,
) -> None:
    """A second qa_claim by the SAME agent on an already-claimed task returns
    the task (not None) — the idempotency guard in _qa_or_doc_claim allows
    same-agent re-claims. This is what makes the same-agent retry path work."""
    url = _test_database_url
    s1, e1 = await _fresh_session(url)
    try:
        seeded = await _seed(s1, status=TaskStatus.AWAITING_QA)
        qa_id = seeded["qa_agent"].id
        task_id = seeded["task"].id
        await s1.commit()

        ts = TaskService(s1)
        t1 = await ts.qa_claim(qa_id, task_id)
        assert t1 is not None
        await s1.commit()
    finally:
        await _dispose(s1, e1)

    # Fresh session, same agent re-claims.
    s2, e2 = await _fresh_session(url)
    try:
        ts2 = TaskService(s2)
        t2 = await ts2.qa_claim(qa_id, task_id)
        assert t2 is not None, (
            "same-agent re-claim must be idempotent (return the task, not None)"
        )
        assert t2.active_claimant_id == qa_id
    finally:
        await _dispose(s2, e2)


# ---------------------------------------------------------------------------
# Different-agent refusal: a different agent's claim is refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_claim_different_agent_refused(
    _test_database_url: str,
) -> None:
    """A different agent's qa_claim on an already-claimed task returns None —
    the competing-claimant guard blocks it."""
    url = _test_database_url
    s1, e1 = await _fresh_session(url)
    try:
        seeded = await _seed(s1, status=TaskStatus.AWAITING_QA)
        qa_a = seeded["qa_agent"].id
        task_id = seeded["task"].id
        await s1.commit()

        ts = TaskService(s1)
        t1 = await ts.qa_claim(qa_a, task_id)
        assert t1 is not None
        await s1.commit()
    finally:
        await _dispose(s1, e1)

    # Fresh session, different agent tries to claim.
    s2, e2 = await _fresh_session(url)
    try:
        # Seed a second agent.
        qa_b = uuid4()
        qa_b_agent = AgentTable(
            id=qa_b,
            name="BE QA 2",
            slug="be-qa-2",
            role=AgentRole.QA,
            team=Team.BACKEND,
            status=AgentStatus.ACTIVE,
            model_config={},
            system_prompt="",
            capabilities=[],
            permissions={},
            metrics={},
        )
        s2.add(qa_b_agent)
        await s2.flush()

        ts2 = TaskService(s2)
        t2 = await ts2.qa_claim(qa_b, seeded["task"].id)
        assert t2 is None, "different-agent claim must be refused (return None)"
    finally:
        await _dispose(s2, e2)
