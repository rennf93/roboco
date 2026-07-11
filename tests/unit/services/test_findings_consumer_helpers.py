"""Real-DB tests for the consumer-side findings helpers in
``choreographer/findings.py`` (``open_findings_for_task``,
``full_ledger_for_task``, ``stamp_addressed_verified``) — the fetch/stamp
layer every evidence/handoff/claim surface and the pass_review/pr_pass
verified-stamp thread through.

Follows ``test_review_findings_repository.py``'s pattern: real Postgres via
the session-scoped test DB (local: ROBOCO_TEST_DB_PORT=55432
ROBOCO_TEST_DB_USER=renzof).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, TaskTable
from roboco.foundation.policy.content import Finding, Severity
from roboco.models.base import AgentRole, AgentStatus, TaskStatus, TaskType, Team
from roboco.services.gateway.choreographer import findings as findings_lib
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
        name="Consumer Helper Test Agent",
        slug=f"consumer-helper-test-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="consumer helper test",
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
        title="consumer helper seed task",
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


# ---------------------------------------------------------------------------
# open_findings_for_task / full_ledger_for_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_findings_for_task_excludes_addressed(
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
        findings=[_finding(), _finding(actual="second")],
    )
    await repo.mark_addressed(task_id, str(rows[0].id), commit="abc", note="fixed")

    open_rows = await findings_lib.open_findings_for_task(db_session, task_id)
    assert len(open_rows) == 1
    assert open_rows[0].id == rows[1].id


@pytest.mark.asyncio
async def test_open_findings_for_task_caps(db_session: AsyncSession) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)
    findings = [_finding(actual=f"issue {i}") for i in range(3)]
    await repo.insert_many(
        task_id=task_id, origin="qa", round=1, author_slug="be-qa", findings=findings
    )

    cap = _EXPECTED_TWO
    rows = await findings_lib.open_findings_for_task(db_session, task_id, limit=cap)
    assert len(rows) == cap


@pytest.mark.asyncio
async def test_full_ledger_for_task_includes_every_status(
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
        findings=[_finding(), _finding(actual="second")],
    )
    await repo.mark_addressed(task_id, str(rows[0].id), commit="abc", note="fixed")

    full = await findings_lib.full_ledger_for_task(db_session, task_id)
    assert len(full) == _EXPECTED_TWO
    statuses = {r.status for r in full}
    assert statuses == {STATUS_ADDRESSED, STATUS_OPEN}


@pytest.mark.asyncio
async def test_open_and_full_ledger_empty_for_unknown_task(
    db_session: AsyncSession,
) -> None:
    assert await findings_lib.open_findings_for_task(db_session, uuid4()) == []
    assert await findings_lib.full_ledger_for_task(db_session, uuid4()) == []


class _BoomSession:
    """A session stand-in whose ``execute`` always raises — simulates a
    ledger-read failure so the fetch helpers' fail-open posture is provable
    without a real outage."""

    async def execute(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("db unavailable")


@pytest.mark.asyncio
async def test_open_findings_for_task_fails_open_on_db_error() -> None:
    assert await findings_lib.open_findings_for_task(_BoomSession(), uuid4()) == []


@pytest.mark.asyncio
async def test_full_ledger_for_task_fails_open_on_db_error() -> None:
    assert await findings_lib.full_ledger_for_task(_BoomSession(), uuid4()) == []


# ---------------------------------------------------------------------------
# stamp_addressed_verified — origin-scoped, status-scoped verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stamp_verifies_only_addressed_rows_of_the_given_origin(
    db_session: AsyncSession,
) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    repo = ReviewFindingsRepository(db_session)

    qa_rows = await repo.insert_many(
        task_id=task_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[_finding(actual="qa addressed"), _finding(actual="qa still open")],
    )
    gate_rows = await repo.insert_many(
        task_id=task_id,
        origin="pr_gate",
        round=1,
        author_slug="be-pr-reviewer",
        findings=[_finding(actual="gate addressed")],
    )
    # Mark one qa finding + the one pr_gate finding addressed; leave the
    # second qa finding open.
    await repo.mark_addressed(task_id, str(qa_rows[0].id), commit="c1", note="fixed")
    await repo.mark_addressed(task_id, str(gate_rows[0].id), commit="c2", note="fixed")

    count = await findings_lib.stamp_addressed_verified(
        db_session, task_id, origin="qa"
    )

    assert count == 1
    all_rows = await repo.list_for_task(task_id)
    by_id = {r.id: r for r in all_rows}
    # The addressed qa finding is now verified.
    assert by_id[qa_rows[0].id].status == STATUS_VERIFIED
    # The still-open qa finding is untouched.
    assert by_id[qa_rows[1].id].status == STATUS_OPEN
    # The addressed pr_gate finding is untouched — different origin.
    assert by_id[gate_rows[0].id].status == STATUS_ADDRESSED


@pytest.mark.asyncio
async def test_stamp_does_not_touch_waived_rows(db_session: AsyncSession) -> None:
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
    await repo.mark_waived(UUID(str(rows[0].id)), "not a real defect")

    count = await findings_lib.stamp_addressed_verified(
        db_session, task_id, origin="qa"
    )

    assert count == 0
    waived = await repo.list_for_task(task_id, status=STATUS_WAIVED)
    assert len(waived) == 1


@pytest.mark.asyncio
async def test_stamp_is_a_noop_when_nothing_addressed(
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

    count = await findings_lib.stamp_addressed_verified(
        db_session, task_id, origin="qa"
    )

    assert count == 0
    open_rows = await repo.list_for_task(task_id, status=STATUS_OPEN)
    assert len(open_rows) == 1


@pytest.mark.asyncio
async def test_stamp_propagates_on_repo_error() -> None:
    """Not best-effort — a repo error must propagate so the caller (pass_review /
    pr_pass) fails the whole verb cleanly instead of silently landing a
    passed/gated task against a stale ledger."""
    with pytest.raises(RuntimeError):
        await findings_lib.stamp_addressed_verified(
            _BoomSession(), uuid4(), origin="qa"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
