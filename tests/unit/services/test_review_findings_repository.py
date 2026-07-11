"""Real-DB tests for ReviewFindingsRepository — the revision-findings ledger.

Follows the ``test_audit_real_query.py`` / ``test_vault_task_queries_real.py``
pattern: real Postgres via the session-scoped test DB (local:
ROBOCO_TEST_DB_PORT=55432 ROBOCO_TEST_DB_USER=renzof). ``Base.metadata.create_all``
builds the schema from live ORM metadata, so ``TaskReviewFindingTable`` needs no
migration replay here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, TaskTable
from roboco.foundation.policy.content import Finding, Severity
from roboco.models.base import AgentRole, AgentStatus, TaskStatus, TaskType, Team
from roboco.services.repositories.review_findings import (
    STATUS_ADDRESSED,
    STATUS_OPEN,
    STATUS_VERIFIED,
    STATUS_WAIVED,
    ReviewFindingsRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_EXPECTED_TWO = 2


async def _seed_agent(session: AsyncSession) -> UUID:
    agent = AgentTable(
        id=uuid4(),
        name="Ledger Test Agent",
        slug=f"ledger-test-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="ledger test",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    return UUID(str(agent.id))


async def _seed_task(session: AsyncSession, created_by: UUID) -> UUID:
    task = TaskTable(
        id=uuid4(),
        title="ledger seed task",
        description="seed",
        acceptance_criteria=["seeded"],
        status=TaskStatus.NEEDS_REVISION,
        priority=2,
        task_type=TaskType.CODE,
        team=Team.BACKEND,
        created_by=created_by,
    )
    session.add(task)
    await session.flush()
    return UUID(str(task.id))


def _finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "file": "roboco/services/task.py",
        "line": 10,
        "severity": Severity.MAJOR,
        "expected": "raises on bad input",
        "actual": "swallows the error",
    }
    base.update(overrides)
    return Finding.model_validate(base)


@pytest.mark.asyncio
async def test_insert_many_persists_one_row_per_finding(
    db_session: AsyncSession,
) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)

    rows = await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[_finding(), _finding(criterion="AC1")],
    )

    assert len(rows) == _EXPECTED_TWO
    assert all(r.id is not None for r in rows)
    assert all(r.status == STATUS_OPEN for r in rows)
    assert all(r.round == 1 for r in rows)
    assert all(r.origin == "qa" for r in rows)
    assert rows[1].criterion == "AC1"


@pytest.mark.asyncio
async def test_list_for_task_orders_newest_round_first(
    db_session: AsyncSession,
) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)

    await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[_finding()],
    )
    await repo.insert_many(
        task_id=task_id,
        origin="pr_gate",
        round=2,
        author_slug="be-pr-reviewer",
        findings=[_finding(actual="round 2 issue")],
    )

    rows = await repo.list_for_task(task_id)
    assert [r.round for r in rows] == [2, 1]


@pytest.mark.asyncio
async def test_list_for_task_filters_by_status(db_session: AsyncSession) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[_finding()],
    )
    await repo.mark_addressed(task_id, str(rows[0].id), commit="abc123", note="fixed")

    open_rows = await repo.list_for_task(task_id, status=STATUS_OPEN)
    addressed_rows = await repo.list_for_task(task_id, status=STATUS_ADDRESSED)
    assert open_rows == []
    assert len(addressed_rows) == 1


@pytest.mark.asyncio
async def test_list_for_task_scoped_to_one_task(db_session: AsyncSession) -> None:
    agent_id = await _seed_agent(db_session)
    task_a = await _seed_task(db_session, agent_id)
    task_b = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)
    await repo.insert_many(
        task_id=task_a, origin="qa", round=1, author_slug="be-qa", findings=[_finding()]
    )

    assert len(await repo.list_for_task(task_a)) == 1
    assert await repo.list_for_task(task_b) == []


@pytest.mark.asyncio
async def test_mark_addressed_by_full_id(db_session: AsyncSession) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[_finding()],
    )

    updated = await repo.mark_addressed(
        task_id, str(rows[0].id), commit="deadbeef", note="fixed the guard"
    )
    assert updated is not None
    assert updated.status == STATUS_ADDRESSED
    assert updated.addressed_by_commit == "deadbeef"
    assert updated.resolution_note == "fixed the guard"


@pytest.mark.asyncio
async def test_mark_addressed_by_8_char_prefix(db_session: AsyncSession) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[_finding()],
    )
    prefix = str(rows[0].id)[:8]

    updated = await repo.mark_addressed(task_id, prefix, commit=None, note=None)
    assert updated is not None
    assert updated.id == rows[0].id


@pytest.mark.asyncio
async def test_mark_addressed_unknown_ref_is_a_noop(db_session: AsyncSession) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)
    await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[_finding()],
    )

    result = await repo.mark_addressed(task_id, "ffffffff", commit=None, note=None)
    assert result is None
    assert len(await repo.list_for_task(task_id, status=STATUS_OPEN)) == 1


@pytest.mark.asyncio
async def test_mark_addressed_wrong_task_is_a_noop(db_session: AsyncSession) -> None:
    """A finding_ref that exists but belongs to a DIFFERENT task must not match —
    an agent can never address another task's finding."""
    agent_id = await _seed_agent(db_session)
    task_a = await _seed_task(db_session, agent_id)
    task_b = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task_a, origin="qa", round=1, author_slug="be-qa", findings=[_finding()]
    )

    result = await repo.mark_addressed(task_b, str(rows[0].id), commit=None, note=None)
    assert result is None
    assert len(await repo.list_for_task(task_a, status=STATUS_OPEN)) == 1


@pytest.mark.asyncio
async def test_mark_verified_bulk_by_id(db_session: AsyncSession) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[_finding(), _finding(actual="second")],
    )
    ids = [UUID(str(r.id)) for r in rows]

    count = await repo.mark_verified(ids)
    assert count == _EXPECTED_TWO
    verified = await repo.list_for_task(task_id, status=STATUS_VERIFIED)
    assert len(verified) == _EXPECTED_TWO


@pytest.mark.asyncio
async def test_mark_verified_empty_list_is_a_noop(db_session: AsyncSession) -> None:
    repo = ReviewFindingsRepository(db_session)
    assert await repo.mark_verified([]) == 0


@pytest.mark.asyncio
async def test_mark_waived_requires_note(db_session: AsyncSession) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)
    rows = await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[_finding()],
    )

    ok = await repo.mark_waived(UUID(str(rows[0].id)), "not a real defect")
    assert ok is True
    waived = await repo.list_for_task(task_id, status=STATUS_WAIVED)
    assert len(waived) == 1
    assert waived[0].resolution_note == "not a real defect"


@pytest.mark.asyncio
async def test_mark_waived_unknown_id_returns_false(db_session: AsyncSession) -> None:
    repo = ReviewFindingsRepository(db_session)
    assert await repo.mark_waived(uuid4(), "note") is False
