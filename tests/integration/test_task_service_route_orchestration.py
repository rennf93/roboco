"""TaskService coverage — route-level orchestration helpers.

Covers `claim_task_for_agent`, `soft_block_task_for_agent`,
`docs_complete_for_task`, `complete_task_for_agent`,
`escalate_to_ceo_for_agent`, `substitute_task_for_agent`, and the
PM-substitute helper functions. These methods compose the lower-level
service primitives + permission checks + notification delivery.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    Complexity,
    SubstituteReason,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.permissions import AgentContext
from roboco.models.task import TaskCreateRequest
from roboco.services.base import (
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.task import (
    SoftBlockInput,
    TaskService,
    notify_pm_for_substitute,
    resolve_pm_for_substitute,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


# Bypass commits so the test rollback isolation works.
@pytest_asyncio.fixture
async def task_setup(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[dict]:
    # Patch session.commit to no-op so the route-orchestration methods don't
    # break the conftest's transaction-rollback isolation.
    original_commit = db_session.commit

    async def _no_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _no_commit)

    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "agent_id": agent.id,
        "agent_slug": agent.slug,
        "project_id": project.id,
        "db": db_session,
        "_original_commit": original_commit,
    }


def _req(setup: dict, **overrides) -> TaskCreateRequest:
    return TaskCreateRequest(
        title=overrides.pop("title", "t"),
        description=overrides.pop("description", "d"),
        acceptance_criteria=overrides.pop("acceptance_criteria", ["ac"]),
        team=overrides.pop("team", Team.BACKEND),
        created_by=setup["agent_id"],
        project_id=setup["project_id"],
        task_type=overrides.pop("task_type", TaskType.CODE),
        nature=overrides.pop("nature", TaskNature.TECHNICAL),
        estimated_complexity=overrides.pop("estimated_complexity", Complexity.MEDIUM),
        **overrides,
    )


def _ctx(agent_id, role: AgentRole, team: Team = Team.BACKEND) -> AgentContext:
    return AgentContext(
        agent_id=agent_id,
        role=role,
        team=team,
        slug="ctx-slug",
    )


class _Permissions:
    """Minimal permissions stub matching the production service's interface."""

    def __init__(
        self, *, can_claim: bool = True, can_assign: bool = True, can_close: bool = True
    ) -> None:
        self._can_claim = can_claim
        self._can_assign = can_assign
        self._can_close = can_close

    def can_perform_task_action(
        self,
        agent: AgentContext,
        action: str,
        team=None,  # noqa: ARG002
    ) -> bool:
        del agent
        if action == "claim":
            return self._can_claim
        if action == "assign":
            return self._can_assign
        if action == "close":
            return self._can_close
        return False


@asynccontextmanager
async def _stub_delivery_factory() -> AsyncIterator[AsyncMock]:
    """Patch notification_delivery so no real delivery happens."""
    yield AsyncMock()


# ---------------------------------------------------------------------------
# claim_task_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_task_for_agent_raises_for_missing(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    perms = _Permissions(can_claim=True)
    with pytest.raises(NotFoundError):
        await svc.claim_task_for_agent(uuid4(), agent_ctx, perms, None)


@pytest.mark.asyncio
async def test_claim_task_for_agent_unauthorized_when_no_claim_perm(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    perms = _Permissions(can_claim=False)
    with pytest.raises(UnauthorizedError):
        await svc.claim_task_for_agent(task.id, agent_ctx, perms, None)


@pytest.mark.asyncio
async def test_claim_task_for_agent_self_review_rejected(
    task_setup: dict,
    db_session: AsyncSession,
) -> None:
    svc = task_setup["svc"]
    qa_id = uuid4()
    task = await svc.create(_req(task_setup))
    task.quick_context = f"original_developer:{qa_id}"
    await db_session.flush()
    agent_ctx = _ctx(qa_id, AgentRole.QA)
    perms = _Permissions(can_claim=True)
    with pytest.raises(UnauthorizedError, match="SELF_REVIEW"):
        await svc.claim_task_for_agent(task.id, agent_ctx, perms, None)


@pytest.mark.asyncio
async def test_claim_task_for_agent_succeeds_for_self_claim(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/x"
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    perms = _Permissions(can_claim=True, can_assign=False)
    out = await svc.claim_task_for_agent(task.id, agent_ctx, perms, None)
    assert out.id == task.id


@pytest.mark.asyncio
async def test_claim_task_for_agent_validation_error_when_not_pending(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.COMPLETED
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    perms = _Permissions(can_claim=True)
    with pytest.raises(ValidationError):
        await svc.claim_task_for_agent(task.id, agent_ctx, perms, None)


@pytest.mark.asyncio
async def test_claim_task_for_agent_with_target_slug(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """PM claims a task on behalf of another agent via slug."""
    svc = task_setup["svc"]
    target_dev = AgentTable(
        id=uuid4(),
        name="OtherDev",
        slug=f"be-dev-target-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(target_dev)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/x"
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.CELL_PM)
    perms = _Permissions(can_claim=True, can_assign=True)
    out = await svc.claim_task_for_agent(
        task.id, agent_ctx, perms, claim_target_slug=target_dev.slug
    )
    assert out.assigned_to == target_dev.id


# ---------------------------------------------------------------------------
# soft_block_task_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_block_task_for_agent_unauthorized_when_not_assignee(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="O",
        slug=f"o-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="o",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = other.id
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    req = SoftBlockInput(
        blocker_type="external",
        reason="api creds",
        what_needed="aws key",
        resolver_type_raw="agent",
    )
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: AsyncMock(),
    )
    with pytest.raises(UnauthorizedError):
        await svc.soft_block_task_for_agent(task.id, agent_ctx, req)


@pytest.mark.asyncio
async def test_soft_block_task_for_agent_invalid_resolver_falls_back(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bad resolver_type_raw → AGENT default, soft_block still succeeds."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)

    fake_delivery = AsyncMock()
    fake_delivery.notify_pm_of_block = AsyncMock()

    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: fake_delivery,
    )

    req = SoftBlockInput(
        blocker_type="external",
        reason="x",
        what_needed="y",
        resolver_type_raw="garbage",
    )
    out = await svc.soft_block_task_for_agent(task.id, agent_ctx, req)
    assert out.status == TaskStatus.BLOCKED
    fake_delivery.notify_pm_of_block.assert_awaited_once()


@pytest.mark.asyncio
async def test_soft_block_task_for_agent_pm_blocks_other_task(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()
    agent_ctx = _ctx(pm.id, AgentRole.CELL_PM)

    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: AsyncMock(),
    )
    req = SoftBlockInput(
        blocker_type="external",
        reason="r",
        what_needed="w",
        resolver_type_raw="human",
    )
    out = await svc.soft_block_task_for_agent(task.id, agent_ctx, req)
    assert out.status == TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_soft_block_task_for_agent_validation_when_not_in_progress(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    # PENDING — soft_block returns None
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: AsyncMock(),
    )
    req = SoftBlockInput(
        blocker_type="external",
        reason="r",
        what_needed="w",
        resolver_type_raw="agent",
    )
    with pytest.raises(ValidationError):
        await svc.soft_block_task_for_agent(task.id, agent_ctx, req)


# ---------------------------------------------------------------------------
# docs_complete_for_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_complete_for_task_only_documenter(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: AsyncMock(),
    )
    with pytest.raises(UnauthorizedError, match="documenters"):
        await svc.docs_complete_for_task(task.id, agent_ctx, "notes")


@pytest.mark.asyncio
async def test_docs_complete_for_task_self_documentation_blocked(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    doc_id = task_setup["agent_id"]
    task = await svc.create(_req(task_setup))
    task.quick_context = f"original_developer:{doc_id}"
    await db_session.flush()
    agent_ctx = _ctx(doc_id, AgentRole.DOCUMENTER)
    audit_mock = AsyncMock()

    class _Audit:
        async def log_task_action_denial(self, **_kwargs) -> None:
            await audit_mock(_kwargs)

    audit_instance = _Audit()
    monkeypatch.setattr(
        "roboco.services.audit.get_audit_service",
        lambda: audit_instance,
    )
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: AsyncMock(),
    )
    with pytest.raises(UnauthorizedError, match="own task"):
        await svc.docs_complete_for_task(task.id, agent_ctx, "notes" * 20)


@pytest.mark.asyncio
async def test_docs_complete_for_task_missing_notes(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DOCUMENTER)
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: AsyncMock(),
    )
    with pytest.raises(ValidationError, match="DOC_NOTES_REQUIRED"):
        await svc.docs_complete_for_task(task.id, agent_ctx, "tiny")


@pytest.mark.asyncio
async def test_docs_complete_for_task_validation_when_invalid_status(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # PENDING - docs_complete will reject
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DOCUMENTER)
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: AsyncMock(),
    )
    with pytest.raises(ValidationError, match="invalid status"):
        await svc.docs_complete_for_task(
            task.id, agent_ctx, "Substantial notes about what was documented and where."
        )


@pytest.mark.asyncio
async def test_docs_complete_for_task_succeeds(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    doc = AgentTable(
        id=uuid4(),
        name="Doc",
        slug=f"be-doc-{uuid4().hex[:8]}",
        role=AgentRole.DOCUMENTER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="d",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(doc)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.assigned_to = doc.id
    task.pr_number = 1
    task.pr_url = "u"
    task.pr_created = True
    await db_session.flush()
    agent_ctx = _ctx(doc.id, AgentRole.DOCUMENTER)
    fake_delivery = AsyncMock()
    fake_delivery.notify_pm_of_docs_complete = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: fake_delivery,
    )
    out = await svc.docs_complete_for_task(
        task.id,
        agent_ctx,
        "Substantial notes about what was documented in detail.",
    )
    assert out is not None
    assert out.docs_complete is True


# ---------------------------------------------------------------------------
# complete_task_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_task_for_agent_unauthorized_no_close(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    perms = _Permissions(can_close=False)
    with pytest.raises(UnauthorizedError):
        await svc.complete_task_for_agent(task.id, agent_ctx, perms)


@pytest.mark.asyncio
async def test_complete_task_for_agent_self_approval_blocked(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """PM who created the task can't complete it from awaiting_pm_review."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.created_by = task_setup["agent_id"]
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.CELL_PM)
    perms = _Permissions(can_close=True)
    with pytest.raises(UnauthorizedError, match="SELF_APPROVAL"):
        await svc.complete_task_for_agent(task.id, agent_ctx, perms)


@pytest.mark.asyncio
async def test_complete_task_for_agent_force_requires_ceo(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.CELL_PM)
    perms = _Permissions(can_close=True)
    with pytest.raises(UnauthorizedError, match="force_complete"):
        await svc.complete_task_for_agent(
            task.id,
            agent_ctx,
            perms,
            force_with_cancelled=True,
            justification="x",
        )


@pytest.mark.asyncio
async def test_complete_task_for_agent_force_requires_justification(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.CEO)
    perms = _Permissions(can_close=True)
    with pytest.raises(ValidationError, match="justification"):
        await svc.complete_task_for_agent(
            task.id,
            agent_ctx,
            perms,
            force_with_cancelled=True,
            justification=None,
        )


@pytest.mark.asyncio
async def test_complete_task_for_agent_validation_when_not_completable(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))  # PENDING — cannot complete
    await db_session.flush()
    agent_ctx = _ctx(pm.id, AgentRole.CELL_PM)
    perms = _Permissions(can_close=True)
    with pytest.raises(ValidationError):
        await svc.complete_task_for_agent(task.id, agent_ctx, perms)


@pytest.mark.asyncio
async def test_complete_task_for_agent_succeeds_for_pm(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = pm.id
    await db_session.flush()
    agent_ctx = _ctx(pm.id, AgentRole.CELL_PM)
    perms = _Permissions(can_close=True)
    out = await svc.complete_task_for_agent(task.id, agent_ctx, perms)
    assert out.status == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# escalate_to_ceo_for_agent + _validate_escalation_preconditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_to_ceo_for_agent_unauthorized_when_no_close(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    perms = _Permissions(can_close=False)
    with pytest.raises(UnauthorizedError):
        await svc.escalate_to_ceo_for_agent(task.id, agent_ctx, perms, "notes")


@pytest.mark.asyncio
async def test_escalate_to_ceo_for_agent_no_pr_validation(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = None
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.MAIN_PM, Team.MAIN_PM)
    perms = _Permissions(can_close=True)
    with pytest.raises(ValidationError, match="NO_PR"):
        await svc.escalate_to_ceo_for_agent(task.id, agent_ctx, perms, "notes")


@pytest.mark.asyncio
async def test_escalate_to_ceo_for_agent_pr_not_confirmed(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = 1
    task.pr_created = False  # Must also be confirmed
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.MAIN_PM, Team.MAIN_PM)
    perms = _Permissions(can_close=True)
    with pytest.raises(ValidationError, match="PR_NOT_CONFIRMED"):
        await svc.escalate_to_ceo_for_agent(task.id, agent_ctx, perms, "notes")


@pytest.mark.asyncio
async def test_escalate_to_ceo_for_agent_active_subtasks_block(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = 1
    task.pr_created = True
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=task.id))
    await db_session.flush()
    assert child is not None
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.MAIN_PM, Team.MAIN_PM)
    perms = _Permissions(can_close=True)
    with pytest.raises(ValidationError, match="ACTIVE_SUBTASKS"):
        await svc.escalate_to_ceo_for_agent(task.id, agent_ctx, perms, "notes" * 10)


@pytest.mark.asyncio
async def test_escalate_to_ceo_for_agent_short_notes(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = 1
    task.pr_created = True
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.MAIN_PM, Team.MAIN_PM)
    perms = _Permissions(can_close=True)
    with pytest.raises(ValidationError, match="ESCALATION_NOTES_REQUIRED"):
        await svc.escalate_to_ceo_for_agent(task.id, agent_ctx, perms, "x")


@pytest.mark.asyncio
async def test_escalate_to_ceo_for_agent_succeeds(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = 1
    task.pr_url = "u"
    task.pr_created = True
    task.docs_complete = True
    await db_session.flush()
    agent_ctx = _ctx(pm.id, AgentRole.MAIN_PM, Team.MAIN_PM)
    perms = _Permissions(can_close=True)
    fake_delivery = AsyncMock()
    fake_delivery.notify_ceo_of_escalation = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: fake_delivery,
    )
    out = await svc.escalate_to_ceo_for_agent(
        task.id,
        agent_ctx,
        perms,
        "Substantial reasons for CEO review and escalation explanation.",
    )
    assert out.status == TaskStatus.AWAITING_CEO_APPROVAL
    fake_delivery.notify_ceo_of_escalation.assert_awaited_once()


# ---------------------------------------------------------------------------
# substitute_task_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substitute_task_for_agent_invalid_reason(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    with pytest.raises(ValidationError, match="Invalid reason"):
        await svc.substitute_task_for_agent(
            uuid4(), agent_ctx, "garbage_reason", "details"
        )


@pytest.mark.asyncio
async def test_substitute_task_for_agent_must_own(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    other = AgentTable(
        id=uuid4(),
        name="X",
        slug=f"x-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = other.id
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    with pytest.raises(UnauthorizedError, match="substitute"):
        await svc.substitute_task_for_agent(
            task.id, agent_ctx, SubstituteReason.LOW_CONTEXT.value, "no context"
        )


@pytest.mark.asyncio
async def test_substitute_task_for_agent_low_context_routes_to_pending(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    monkeypatch.setattr("roboco.agents_config.get_pm_for_agent", lambda _slug: pm.slug)
    out = await svc.substitute_task_for_agent(
        task.id, agent_ctx, SubstituteReason.LOW_CONTEXT.value, "ran out of context"
    )
    assert out.status == TaskStatus.PENDING.value


@pytest.mark.asyncio
async def test_substitute_task_for_agent_qa_task_complete_routes_to_pm_review(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    qa = AgentTable(
        id=uuid4(),
        name="QA",
        slug=f"be-qa-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([qa, pm])
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.assigned_to = qa.id
    await db_session.flush()
    agent_ctx = _ctx(qa.id, AgentRole.QA)
    monkeypatch.setattr("roboco.agents_config.get_pm_for_agent", lambda _slug: pm.slug)

    # Patch notify_pm_for_substitute so we don't hit the notification stack
    async def _fake_notify(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("roboco.services.task.notify_pm_for_substitute", _fake_notify)
    out = await svc.substitute_task_for_agent(
        task.id, agent_ctx, SubstituteReason.TASK_COMPLETE.value, "qa done"
    )
    # awaiting_pm_review for QA's task_complete substitute
    assert out.status == TaskStatus.AWAITING_PM_REVIEW.value


@pytest.mark.asyncio
async def test_substitute_task_for_agent_blocked_external_routes_to_blocked(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    monkeypatch.setattr("roboco.agents_config.get_pm_for_agent", lambda _slug: None)
    monkeypatch.setattr("roboco.agents_config.get_pm_for_team", lambda _team: None)
    out = await svc.substitute_task_for_agent(
        task.id,
        agent_ctx,
        SubstituteReason.BLOCKED_EXTERNAL.value,
        "needs new skills",
    )
    assert out.status == TaskStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_substitute_task_for_agent_update_failure_raises(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If update returns None, ServiceError is raised."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()
    agent_ctx = _ctx(task_setup["agent_id"], AgentRole.DEVELOPER)
    monkeypatch.setattr("roboco.agents_config.get_pm_for_agent", lambda _slug: None)
    monkeypatch.setattr("roboco.agents_config.get_pm_for_team", lambda _team: None)

    # Force update() to return None
    object.__setattr__(svc, "update", AsyncMock(return_value=None))
    with pytest.raises(ServiceError, match="Update failed"):
        await svc.substitute_task_for_agent(
            task.id,
            agent_ctx,
            SubstituteReason.MAX_RETRIES.value,
            "x",
        )


# ---------------------------------------------------------------------------
# build_substitute_update — pm uuid path with team-fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_substitute_update_resolves_pm_via_team(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = task_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    monkeypatch.setattr(
        "roboco.agents_config.get_pm_for_agent",
        lambda _slug: None,
    )
    monkeypatch.setattr(
        "roboco.agents_config.get_pm_for_team",
        lambda _team: pm.slug,
    )
    update_data, target_pm_slug = await svc.build_substitute_update(
        agent_id=task_setup["agent_id"],
        task=task,
        new_status=TaskStatus.AWAITING_PM_REVIEW,
        reason="task_complete",
        details="d",
    )
    assert target_pm_slug == pm.slug
    assert update_data["assigned_to"] == pm.id


# ---------------------------------------------------------------------------
# resolve_pm_for_substitute / notify_pm_for_substitute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_pm_for_substitute_returns_none_when_no_match(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("roboco.agents_config.get_pm_for_agent", lambda _s: None)
    monkeypatch.setattr("roboco.agents_config.get_pm_for_team", lambda _t: None)
    slug, pm_uuid = await resolve_pm_for_substitute(
        db_session, agent_slug="x", task_team=Team.BACKEND
    )
    assert slug is None
    assert pm_uuid is None


@pytest.mark.asyncio
async def test_resolve_pm_for_substitute_returns_none_when_pm_not_in_db(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PM slug exists in config but no AgentTable row matches → uuid is None."""
    monkeypatch.setattr(
        "roboco.agents_config.get_pm_for_agent",
        lambda _s: "nonexistent-pm",
    )
    monkeypatch.setattr("roboco.agents_config.get_pm_for_team", lambda _t: None)
    slug, pm_uuid = await resolve_pm_for_substitute(
        db_session, agent_slug="x", task_team=Team.BACKEND
    )
    assert slug == "nonexistent-pm"
    assert pm_uuid is None


@pytest.mark.asyncio
async def test_resolve_pm_for_substitute_returns_uuid_when_found(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"resolve-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    monkeypatch.setattr(
        "roboco.agents_config.get_pm_for_agent",
        lambda _s: pm.slug,
    )
    monkeypatch.setattr("roboco.agents_config.get_pm_for_team", lambda _t: None)
    slug, pm_uuid = await resolve_pm_for_substitute(
        db_session, agent_slug="x", task_team=Team.BACKEND
    )
    assert slug == pm.slug
    assert pm_uuid == pm.id


@pytest.mark.asyncio
async def test_notify_pm_for_substitute_skips_when_pm_not_in_db(
    db_session: AsyncSession,
) -> None:
    """No-op when PM slug doesn't resolve to an Agent."""
    # Should not raise
    await notify_pm_for_substitute(
        db_session,
        pm_slug="nonexistent-pm",
        task_id=uuid4(),
        from_agent_id=uuid4(),
        message=("subj", "body"),
    )


@pytest.mark.asyncio
async def test_notify_pm_for_substitute_creates_notification(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session = task_setup["db"]
    svc = task_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"notify-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))

    fake_delivery = AsyncMock()
    fake_delivery.deliver = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: fake_delivery,
    )
    await notify_pm_for_substitute(
        db_session,
        pm_slug=pm.slug,
        task_id=task.id,
        from_agent_id=task_setup["agent_id"],
        message=("Subject", "body content"),
    )
    fake_delivery.deliver.assert_awaited_once()


# ---------------------------------------------------------------------------
# _explain_complete_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_complete_failure_with_incomplete_subtasks(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    assert child is not None
    msg = await svc._explain_complete_failure(parent.id, "awaiting_pm_review")
    assert "subtask" in msg.lower()


@pytest.mark.asyncio
async def test_explain_complete_failure_with_wrong_status(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await db_session.flush()
    msg = await svc._explain_complete_failure(task.id, "pending")
    assert "pending" in msg.lower()


@pytest.mark.asyncio
async def test_explain_complete_failure_with_missing_task(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    msg = await svc._explain_complete_failure(uuid4(), "in_progress")
    assert "Cannot complete task" in msg


# ---------------------------------------------------------------------------
# _format_id_list
# ---------------------------------------------------------------------------


def test_format_id_list_short(task_setup: dict) -> None:
    svc = task_setup["svc"]
    out = svc._format_id_list(["abcd1234", "efgh5678"])
    assert out == "abcd1234, efgh5678"


def test_format_id_list_long_truncates(task_setup: dict) -> None:
    svc = task_setup["svc"]
    ids = [f"id{i:04d}" for i in range(10)]
    out = svc._format_id_list(ids)
    assert "+5 more" in out
