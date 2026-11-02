"""The release-proposal verification rollup is read in bulk, not per task.

2026-10-28: the panel composed every member task's receipt from four per-task
requests (task, findings, task metrics, attestation). The release on the
Overview card carried 74 member tasks: 74 x /api/tasks/<id>, 74 x
/api/tasks/<id>/findings and 74 x /api/dashboard/metrics/task/<id> at 05:23,
177-310 requests a minute at 05:34-05:36, then "QueuePool limit of size 10
overflow 20 reached" and the guard's Redis "Too many connections". These
routes give the rollup one request for every receipt and one for every CI
verdict, with a database cost that does not grow with the member count.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from roboco.db.tables import (
    AgentSpawnSessionTable,
    ProjectConventionFindingTable,
    ProjectTable,
    TaskTable,
)
from roboco.foundation.policy.content import Finding, Severity
from roboco.models import AgentRole
from roboco.services import release_verification
from roboco.services.attestation import CiVerdict
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.services.task import RELEASE_MANAGER_SOURCE
from sqlalchemy import delete, event

from tests.integration.test_release_routes import (
    _build_app,
    _seed_delivery_task,
    _seed_proposal,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_DONE_AT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
_ADDED_MEMBERS = 6


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _build_app(db_session, AgentRole.CEO, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db_session.execute(
        delete(TaskTable).where(TaskTable.source == RELEASE_MANAGER_SOURCE)
    )
    await db_session.commit()
    app.dependency_overrides.clear()


async def _project_of(session: AsyncSession, proposal: TaskTable) -> ProjectTable:
    project = await session.get(ProjectTable, proposal.project_id)
    assert project is not None
    return project


async def _seed_members(
    session: AsyncSession, proposal: TaskTable, count: int
) -> list[TaskTable]:
    project = await _project_of(session, proposal)
    return [
        await _seed_delivery_task(
            session,
            project,
            _DONE_AT + timedelta(minutes=i),
            title=f"Member {i}",
            criteria=["route stays thin"],
            qa_notes="[AC] route stays thin — verified: tests/test_x.py::test_thin",
        )
        for i in range(count)
    ]


async def _seed_review_history(session: AsyncSession, member: TaskTable) -> None:
    member_id = UUID(str(member.id))
    await ReviewFindingsRepository(session).insert_many(
        task_id=member_id,
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[Finding(severity=Severity.MAJOR, expected="thin", actual="fat")],
    )
    session.add(
        ProjectConventionFindingTable(
            project_id=member.project_id,
            task_id=member_id,
            file="roboco/api/routes/x.py",
            line=12,
            rule="thin_routes",
            level="warn",
            kind="route",
            message="data access in a route",
        )
    )
    for minutes, role in ((0, "developer"), (5, "qa"), (9, "pr_reviewer")):
        session.add(
            AgentSpawnSessionTable(
                id=uuid4(),
                agent_slug=f"{role}-1",
                team="backend",
                role=role,
                model="claude-sonnet",
                task_id=str(member_id),
                started_at=_DONE_AT - timedelta(minutes=30 - minutes),
            )
        )
    await session.flush()


@pytest.mark.asyncio
async def test_verification_returns_every_member_receipt_in_one_response(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    proposal = await _seed_proposal(db_session)
    first, second = await _seed_members(db_session, proposal, 2)
    await _seed_review_history(db_session, first)

    resp = await ceo_client.get("/api/release/proposal/verification")

    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["proposal_task_id"] == str(proposal.id)
    assert [m["task_id"] for m in body["members"]] == [str(first.id), str(second.id)]
    receipt = body["members"][0]
    assert receipt["title"] == "Member 0"
    assert receipt["acceptance_criteria"] == ["route stays thin"]
    assert "verified: tests/test_x.py::test_thin" in receipt["qa_notes"]
    assert [(f["severity"], f["status"]) for f in receipt["findings"]] == [
        ("major", "open")
    ]
    assert [c["rule"] for c in receipt["conventions_findings"]] == ["thin_routes"]
    assert [(r["round"], r["role"]) for r in receipt["reviewer_chain"]] == [
        (1, "qa"),
        (2, "pr_reviewer"),
    ]
    assert body["members"][1]["findings"] == []
    assert body["members"][1]["reviewer_chain"] == []


@pytest.mark.asyncio
async def test_verification_database_cost_does_not_grow_with_members(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    statements: list[str] = []

    def _count(orm_execute_state: Any) -> None:
        statements.append(str(orm_execute_state.statement))

    proposal = await _seed_proposal(db_session)
    members = await _seed_members(db_session, proposal, 1)
    await _seed_review_history(db_session, members[0])
    event.listen(db_session.sync_session, "do_orm_execute", _count)
    try:
        assert (
            await ceo_client.get("/api/release/proposal/verification")
        ).status_code == HTTPStatus.OK
        one_member = len(statements)
        for member in await _seed_members(db_session, proposal, _ADDED_MEMBERS):
            await _seed_review_history(db_session, member)
        statements.clear()
        resp = await ceo_client.get("/api/release/proposal/verification")
    finally:
        event.remove(db_session.sync_session, "do_orm_execute", _count)

    assert resp.status_code == HTTPStatus.OK
    assert len(resp.json()["members"]) == 1 + _ADDED_MEMBERS
    assert len(statements) == one_member


@pytest.mark.asyncio
async def test_verification_404_without_an_open_proposal(
    ceo_client: AsyncClient,
) -> None:
    resp = await ceo_client.get("/api/release/proposal/verification")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_ci_verdicts_read_each_member_pr_with_bounded_concurrency(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    proposal = await _seed_proposal(db_session)
    members = await _seed_members(db_session, proposal, 7)
    for number, member in enumerate(members[1:], start=100):
        member.pr_number = number
    await db_session.flush()
    in_flight = 0
    peak = 0
    looked_up: list[int | None] = []

    async def _fake_pr_ci_verdict(
        git_service: Any, project_slug: str | None, pr_number: int | None
    ) -> CiVerdict:
        nonlocal in_flight, peak
        assert project_slug is not None
        if git_service is None:
            return CiVerdict(state="not_available")
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        looked_up.append(pr_number)
        return CiVerdict(state="success", head_sha=f"sha{pr_number}")

    @contextlib.asynccontextmanager
    async def _fake_db_context(pool: str = "primary") -> AsyncIterator[MagicMock]:
        assert pool == "background"
        yield MagicMock()

    with (
        patch.object(release_verification, "pr_ci_verdict", _fake_pr_ci_verdict),
        patch.object(release_verification, "get_db_context", _fake_db_context),
        patch.object(release_verification, "get_git_service", MagicMock()),
    ):
        resp = await ceo_client.get("/api/release/proposal/ci-verdicts")

    assert resp.status_code == HTTPStatus.OK
    verdicts = resp.json()["verdicts"]
    assert [v["task_id"] for v in verdicts] == [str(m.id) for m in members]
    assert verdicts[0]["ci"]["state"] == "not_available"
    assert {v["ci"]["state"] for v in verdicts[1:]} == {"success"}
    assert sorted(n for n in looked_up if n is not None) == list(range(100, 106))
    assert peak <= release_verification.CI_LOOKUP_CONCURRENCY


@pytest.mark.asyncio
async def test_list_for_tasks_caps_each_task_and_keeps_newest_rounds(
    db_session: AsyncSession,
) -> None:
    proposal = await _seed_proposal(db_session)
    first, second = await _seed_members(db_session, proposal, 2)
    repo = ReviewFindingsRepository(db_session)
    for round_number in (1, 2, 3):
        await repo.insert_many(
            task_id=UUID(str(first.id)),
            origin="qa",
            round=round_number,
            author_slug="be-qa",
            findings=[Finding(severity=Severity.MINOR, expected="e", actual="a")],
        )
    await repo.insert_many(
        task_id=UUID(str(second.id)),
        origin="pr_gate",
        round=1,
        author_slug="pr-reviewer",
        findings=[Finding(severity=Severity.MAJOR, expected="e", actual="a")],
    )
    await db_session.flush()

    rows = await repo.list_for_tasks(
        [UUID(str(first.id)), UUID(str(second.id))], limit_per_task=2
    )

    by_task: dict[str, list[int]] = {}
    for row in rows:
        by_task.setdefault(str(row.task_id), []).append(row.round)
    assert by_task == {str(first.id): [3, 2], str(second.id): [1]}
