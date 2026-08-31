"""Release-manager route coverage — CEO-only GET / approve / reject."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import roboco.services.release_proposal as rp
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes import release as release_route
from roboco.api.routes.release import router as release_router
from roboco.api.routes.releases import router as releases_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation.policy.content import Finding, Severity, markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.release_executor import ReleaseResult
from roboco.services.release_proposal import ReleaseProposalService
from roboco.services.release_readiness import (
    Gap,
    ReleaseReadinessReport,
    report_to_dict,
)
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.services.task import RELEASE_MANAGER_SOURCE, TaskService
from sqlalchemy import delete

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession

_VERSION = "0.13.0"


def _report() -> ReleaseReadinessReport:
    return ReleaseReadinessReport(
        proposed_version=_VERSION,
        bump_kind="minor",
        change_summary=["feat: a thing"],
        drafted_changelog=f"## [{_VERSION}] - 2026-06-25\n\n### Added\n- a thing\n",
        version_bump_plan=["pyproject.toml"],
        gaps=[],
        migration_notes=[],
        gate_state="green",
    )


async def _seed_agent(session: AsyncSession, role: AgentRole, slug: str) -> AgentTable:
    agent = AgentTable(
        id=uuid4(),
        name=slug,
        slug=f"{slug}-{uuid4().hex[:6]}",
        role=role,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    return agent


async def _seed_proposal(session: AsyncSession) -> TaskTable:
    system = await _seed_agent(session, AgentRole.SYSTEM, "system")
    secretary = await _seed_agent(session, AgentRole.SECRETARY, "secretary")
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        git_url="https://example.com/roboco.git",
        assigned_cell=Team.BACKEND,
        created_by=system.id,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title=f"Release proposal: v{_VERSION}",
        description="proposal body",
        acceptance_criteria=["CEO approves"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        project_id=project.id,
        created_by=system.id,
        assigned_to=secretary.id,
        team=Team.MAIN_PM,
        source=RELEASE_MANAGER_SOURCE,
        confirmed_by_human=False,
        orchestration_markers={"release_report": report_to_dict(_report())},
    )
    session.add(task)
    await session.flush()
    return task


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(release_router, prefix="/api/release")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent_id, role=role, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _build_app(db_session, AgentRole.CEO, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    # The approve/reject routes call db.commit() (real behavior), so a held
    # proposal persists past the per-test rollback. list_open_release_proposals()
    # is global (source-scoped, all projects), so clean up to avoid leaking into
    # the engine tests that assert on it.
    await db_session.execute(
        delete(TaskTable).where(TaskTable.source == RELEASE_MANAGER_SOURCE)
    )
    await db_session.commit()
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_proposal_returns_open_proposal(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    await _seed_proposal(db_session)
    resp = await ceo_client.get("/api/release/proposal")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["report"]["proposed_version"] == _VERSION
    assert body["report"]["bump_kind"] == "minor"


@pytest.mark.asyncio
async def test_get_proposal_404_when_none(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get("/api/release/proposal")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_approve_dispatches_async_and_completes(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """#324: the approve route dispatches the ~40min execute in a background
    task and returns 202 immediately (a synchronous request would 504 at nginx
    before the fail-closed gate/CI/publish finished). The panel polls
    GET /proposal for the final status; here we await the dispatched task and
    assert the proposal transitions to COMPLETED once the (faked) publish
    succeeds."""
    task = await _seed_proposal(db_session)
    published = ReleaseResult(
        status="published",
        version=_VERSION,
        files_changed=["pyproject.toml"],
        commit_sha="abc123",
        release_url=f"https://github.com/x/roboco/releases/tag/v{_VERSION}",
        detail="ok",
    )
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock(return_value=published)
    captured: dict[str, asyncio.Task[None]] = {}
    real_dispatch = release_route.dispatch_approve

    def _capturing_dispatch(task_id: UUID, factory: Any) -> asyncio.Task[None]:
        bg = real_dispatch(task_id, factory)
        captured["task"] = bg
        return bg

    with (
        patch(
            "roboco.services.release_proposal.get_release_executor",
            AsyncMock(return_value=fake_executor),
        ),
        patch(
            "roboco.api.routes.release.dispatch_approve",
            side_effect=_capturing_dispatch,
        ),
        patch.object(
            ReleaseProposalService, "_acquire_release_lock", AsyncMock(return_value="t")
        ),
        patch.object(
            ReleaseProposalService,
            "_release_release_lock",
            AsyncMock(return_value=None),
        ),
        patch.object(
            ReleaseProposalService,
            "_heartbeat_release_lock",
            AsyncMock(return_value=True),
        ),
    ):
        resp = await ceo_client.post("/api/release/proposal/approve")
        assert resp.status_code == HTTPStatus.ACCEPTED
        assert resp.json()["status"] == "accepted"
        # Await the background execute WHILE the executor patch is still active
        # (the dispatched task runs the faked publish).
        bg = captured["task"]
        await bg
    fake_executor.execute.assert_awaited_once()
    await db_session.refresh(task)
    assert task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_approve_gate_failure_keeps_proposal_open_async(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """A gate failure in the background execute leaves the proposal open (the
    CEO retries after the cause is fixed); the route still returned 202."""
    task = await _seed_proposal(db_session)
    failed = ReleaseResult(
        status="gate_failed",
        version=_VERSION,
        files_changed=["pyproject.toml"],
        commit_sha=None,
        release_url=None,
        detail="make quality failed",
    )
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock(return_value=failed)
    captured: dict[str, asyncio.Task[None]] = {}
    real_dispatch = release_route.dispatch_approve

    def _capturing_dispatch(task_id: UUID, factory: Any) -> asyncio.Task[None]:
        bg = real_dispatch(task_id, factory)
        captured["task"] = bg
        return bg

    with (
        patch(
            "roboco.services.release_proposal.get_release_executor",
            AsyncMock(return_value=fake_executor),
        ),
        patch(
            "roboco.api.routes.release.dispatch_approve",
            side_effect=_capturing_dispatch,
        ),
        patch.object(
            ReleaseProposalService, "_acquire_release_lock", AsyncMock(return_value="t")
        ),
        patch.object(
            ReleaseProposalService,
            "_release_release_lock",
            AsyncMock(return_value=None),
        ),
        patch.object(
            ReleaseProposalService,
            "_heartbeat_release_lock",
            AsyncMock(return_value=True),
        ),
    ):
        resp = await ceo_client.post("/api/release/proposal/approve")
        assert resp.status_code == HTTPStatus.ACCEPTED
        assert resp.json()["status"] == "accepted"
        await captured["task"]
    await db_session.refresh(task)
    assert task.status == TaskStatus.PENDING  # still held for retry
    # The failure reason is persisted as a marker + surfaced via GET /proposal
    # so a failed ~40min execute isn't a silent PENDING.
    outcome = markers.get_release_execute_outcome(task)
    assert outcome is not None
    assert outcome[0] == "gate_failed"
    assert "make quality failed" in outcome[1]
    poll = await ceo_client.get("/api/release/proposal")
    assert poll.status_code == HTTPStatus.OK
    body = poll.json()
    assert body["execute_status"] == "gate_failed"
    assert "make quality failed" in (body["execute_detail"] or "")


@pytest.mark.asyncio
async def test_approve_exception_records_error_marker(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """An unexpected crash in the background execute (not a structured
    ReleaseResult failure) is recorded as an ``error`` marker so the CEO sees a
    reason instead of a silent PENDING."""
    task = await _seed_proposal(db_session)
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock(side_effect=RuntimeError("boom"))
    captured: dict[str, asyncio.Task[None]] = {}
    real_dispatch = release_route.dispatch_approve

    def _capturing_dispatch(task_id: UUID, factory: Any) -> asyncio.Task[None]:
        bg = real_dispatch(task_id, factory)
        captured["task"] = bg
        return bg

    with (
        patch(
            "roboco.services.release_proposal.get_release_executor",
            AsyncMock(return_value=fake_executor),
        ),
        patch(
            "roboco.api.routes.release.dispatch_approve",
            side_effect=_capturing_dispatch,
        ),
        patch.object(
            ReleaseProposalService, "_acquire_release_lock", AsyncMock(return_value="t")
        ),
        patch.object(
            ReleaseProposalService,
            "_release_release_lock",
            AsyncMock(return_value=None),
        ),
        patch.object(
            ReleaseProposalService,
            "_heartbeat_release_lock",
            AsyncMock(return_value=True),
        ),
    ):
        await ceo_client.post("/api/release/proposal/approve")
        await captured["task"]
    await db_session.refresh(task)
    assert task.status == TaskStatus.PENDING  # still held for retry
    outcome = markers.get_release_execute_outcome(task)
    assert outcome is not None
    assert outcome[0] == "error"
    assert "boom" in outcome[1]


@pytest.mark.asyncio
async def test_get_proposal_surfaces_in_flight(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """execute_in_flight is derived from the in-memory _INFLIGHT_APPROVES
    registry — True while a background execute is registered."""
    await _seed_proposal(db_session)
    resp = await ceo_client.get("/api/release/proposal")
    tid = UUID(resp.json()["task_id"])
    # Register a real pending task under the proposal id, as dispatch_approve does.
    sentinel = asyncio.create_task(asyncio.sleep(3600))
    rp._INFLIGHT_APPROVES[tid] = sentinel
    try:
        in_flight_resp = await ceo_client.get("/api/release/proposal")
        assert in_flight_resp.json()["execute_in_flight"] is True
    finally:
        rp._INFLIGHT_APPROVES.pop(tid, None)
        sentinel.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sentinel
    idle_resp = await ceo_client.get("/api/release/proposal")
    assert idle_resp.json()["execute_in_flight"] is False


@pytest.mark.asyncio
async def test_reject_records_changes_and_cancels_frees_dedup(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """Reject cancels the proposal (not holds it) so the one-open-proposal dedup
    frees and the release manager can re-assess next cycle. The required-changes
    marker stays on the cancelled row for history."""
    task = await _seed_proposal(db_session)
    with (
        patch.object(
            ReleaseProposalService, "_acquire_release_lock", AsyncMock(return_value="t")
        ),
        patch.object(
            ReleaseProposalService,
            "_release_release_lock",
            AsyncMock(return_value=None),
        ),
    ):
        resp = await ceo_client.post(
            "/api/release/proposal/reject",
            json={
                "required_changes": "Tighten the CHANGELOG wording for the API change."
            },
        )
    assert resp.status_code == HTTPStatus.OK
    assert "Tighten the CHANGELOG" in (resp.json()["required_changes"] or "")
    refreshed = await db_session.get(TaskTable, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.CANCELLED  # cancelled, not held
    # The dedup no longer counts it as open — a fresh proposal can originate.
    open_proposals = await TaskService(db_session).list_open_release_proposals()
    assert task.id not in {t.id for t in open_proposals}


@pytest.mark.asyncio
async def test_reject_refused_while_approve_lock_held(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """A concurrent approve holds the release mutex (mid ~40min execute);
    reject must fail closed with 409 instead of racing an unguarded write
    under it — previously reject() never even attempted the lock."""
    await _seed_proposal(db_session)
    with patch.object(
        ReleaseProposalService, "_acquire_release_lock", AsyncMock(return_value=None)
    ):
        resp = await ceo_client.post(
            "/api/release/proposal/reject",
            json={"required_changes": "Tighten the CHANGELOG wording."},
        )
    assert resp.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed_proposal(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_resp = await client.get("/api/release/proposal")
        approve_resp = await client.post("/api/release/proposal/approve")
        reject_resp = await client.post(
            "/api/release/proposal/reject", json={"required_changes": "x" * 20}
        )
    assert get_resp.status_code == HTTPStatus.FORBIDDEN
    assert approve_resp.status_code == HTTPStatus.FORBIDDEN
    assert reject_resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /releases/{version}/certificate — the exportable gate-chain artifact
# ---------------------------------------------------------------------------

_PUBLISHED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


async def _seed_published_proposal(
    session: AsyncSession,
    *,
    completed_at: datetime = _PUBLISHED_AT,
    report: ReleaseReadinessReport | None = None,
) -> tuple[ProjectTable, TaskTable]:
    """A COMPLETED (= published) release proposal; returns (project, task)."""
    system = await _seed_agent(session, AgentRole.SYSTEM, "system")
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        git_url="https://example.com/roboco.git",
        assigned_cell=Team.BACKEND,
        created_by=system.id,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title=f"Release proposal: v{_VERSION}",
        description="proposal body",
        acceptance_criteria=["CEO approves"],
        status=TaskStatus.COMPLETED,
        completed_at=completed_at,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        project_id=project.id,
        created_by=system.id,
        team=Team.MAIN_PM,
        source=RELEASE_MANAGER_SOURCE,
        confirmed_by_human=False,
        orchestration_markers={"release_report": report_to_dict(report or _report())},
    )
    session.add(task)
    await session.flush()
    return project, task


async def _seed_delivery_task(
    session: AsyncSession,
    project: ProjectTable,
    completed_at: datetime,
    **spec: Any,
) -> TaskTable:
    """A COMPLETED delivery task; optional spec keys ``title`` / ``source`` /
    ``criteria`` / ``qa_notes`` override the defaults."""
    task = TaskTable(
        id=uuid4(),
        title=spec.get("title", "Delivery work"),
        description="body",
        acceptance_criteria=spec.get("criteria") or [],
        status=TaskStatus.COMPLETED,
        completed_at=completed_at,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=project.created_by,
        team=Team.BACKEND,
        source=spec.get("source", "manual"),
        confirmed_by_human=True,
        qa_notes=spec.get("qa_notes"),
    )
    session.add(task)
    await session.flush()
    return task


@pytest_asyncio.fixture
async def cert_ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(releases_router, prefix="/api/releases")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=AgentRole.CEO, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_certificate_packages_release_gate_chain(
    db_session: AsyncSession, cert_ceo_client: AsyncClient
) -> None:
    project, _proposal = await _seed_published_proposal(db_session)
    delivered = await _seed_delivery_task(
        db_session,
        project,
        _PUBLISHED_AT - timedelta(hours=1),
        title="Ship certificate endpoint",
        criteria=["route exists", "CEO gated"],
        # Only 1 of 2 criteria stamped by QA → qa_passed False.
        qa_notes="[AC] route exists — verified: routes/releases.py:30\n",
    )
    all_clear = await _seed_delivery_task(
        db_session,
        project,
        _PUBLISHED_AT - timedelta(hours=2),
        title="Docs sweep",
    )
    # Engine-held artifact: not delivered work, must stay out of task_states.
    await _seed_delivery_task(
        db_session,
        project,
        _PUBLISHED_AT - timedelta(hours=3),
        source=RELEASE_MANAGER_SOURCE,
        title="X post draft",
    )
    # Completed after this release's publication: belongs to the next window.
    await _seed_delivery_task(
        db_session,
        project,
        _PUBLISHED_AT + timedelta(hours=1),
        title="Too late",
    )
    repo = ReviewFindingsRepository(db_session)
    await repo.insert_many(
        task_id=UUID(str(delivered.id)),
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[
            Finding(
                severity=Severity.MAJOR,
                expected="thin route",
                actual="DB query in handler",
            )
        ],
    )
    await repo.insert_many(
        task_id=UUID(str(all_clear.id)),
        origin="pr_gate",
        round=1,
        author_slug="pr-reviewer",
        findings=[
            Finding(
                severity=Severity.MINOR,
                expected="docstring",
                actual="missing",
            )
        ],
    )
    row = (await repo.list_for_task(UUID(str(all_clear.id))))[0]
    row.status = "addressed"
    await db_session.flush()

    resp = await cert_ceo_client.get(f"/api/releases/{_VERSION}/certificate")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["version"] == _VERSION
    assert body["ci_verdict"] == "green"
    assert body["conventions_clean"] is True  # report carries no gaps
    assert body["changelog_excerpt"].startswith("## [0.13.0]")
    assert body["ceo_approved_at"] is not None
    titles = {t["title"] for t in body["task_states"]}
    assert titles == {"Ship certificate endpoint", "Docs sweep"}
    failed = next(
        t for t in body["task_states"] if t["title"] == "Ship certificate endpoint"
    )
    assert failed["qa_passed"] is False
    assert (failed["criteria_total"], failed["criteria_verified"]) == (2, 1)
    passed = next(t for t in body["task_states"] if t["title"] == "Docs sweep")
    assert passed["qa_passed"] is True  # no criterion to verify
    findings = body["findings_summary"]
    assert findings["open"]["major"] == 1
    assert findings["open"]["minor"] == 0
    assert findings["closed"]["minor"] == 1
    assert findings["waived"] == {"blocker": 0, "major": 0, "minor": 0, "nit": 0}
    # The v-prefixed tag form resolves to the same certificate.
    assert (
        await cert_ceo_client.get(f"/api/releases/v{_VERSION}/certificate")
    ).status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_certificate_excludes_other_projects_tasks(
    db_session: AsyncSession, cert_ceo_client: AsyncClient
) -> None:
    project, _proposal = await _seed_published_proposal(db_session)
    # A sibling project's delivery task completed inside this release's window:
    # the certificate is scoped to the proposal's project, so neither its task
    # state nor its ledger rows may appear.
    other_project = ProjectTable(
        id=uuid4(),
        name="Other Repo",
        slug=f"other-{uuid4().hex[:6]}",
        git_url="https://example.com/other.git",
        assigned_cell=Team.BACKEND,
        created_by=project.created_by,
    )
    db_session.add(other_project)
    await db_session.flush()
    foreign_task = await _seed_delivery_task(
        db_session,
        other_project,
        _PUBLISHED_AT - timedelta(hours=1),
        title="Foreign project work",
        criteria=["one"],
    )
    repo = ReviewFindingsRepository(db_session)
    await repo.insert_many(
        task_id=UUID(str(foreign_task.id)),
        origin="qa",
        round=1,
        author_slug="be-qa",
        findings=[Finding(severity=Severity.MAJOR, expected="fixed", actual="broken")],
    )

    resp = await cert_ceo_client.get(f"/api/releases/{_VERSION}/certificate")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["task_states"] == []
    assert body["findings_summary"]["open"]["major"] == 0


@pytest.mark.asyncio
async def test_certificate_conventions_dirty_when_classification_gap(
    db_session: AsyncSession, cert_ceo_client: AsyncClient
) -> None:
    report = ReleaseReadinessReport(
        proposed_version=_VERSION,
        bump_kind="patch",
        change_summary=[],
        drafted_changelog="## [0.13.0]\n",
        version_bump_plan=[],
        gaps=[Gap(category="classification", detail="1 manual subject")],
        migration_notes=[],
        gate_state="green",
    )
    await _seed_published_proposal(db_session, report=report)
    resp = await cert_ceo_client.get(f"/api/releases/{_VERSION}/certificate")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["conventions_clean"] is False


@pytest.mark.asyncio
async def test_certificate_404_for_unpublished_version(
    cert_ceo_client: AsyncClient,
) -> None:
    resp = await cert_ceo_client.get("/api/releases/9.9.9/certificate")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_certificate_requires_ceo(db_session: AsyncSession) -> None:
    await _seed_published_proposal(db_session)  # the row must exist; no read follows
    app = FastAPI()
    app.include_router(releases_router, prefix="/api/releases")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=AgentRole.DEVELOPER, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/releases/{_VERSION}/certificate")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()
