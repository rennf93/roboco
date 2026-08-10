"""A forced admin status override must evict the stranded live agent —
deferred to run AFTER the request's transaction commits.

Live incident: `_apply_forced_status_override` (PATCH /api/tasks/{id},
force=true) is pure-DB — `TaskService.admin_set_status` clears
`active_claimant_id` for a review-queue target but never touches the
agent's still-running container, which keeps flailing `not_authorized` on
every content verb against a task it no longer legitimately holds.
`_evict_stranded_agent` mirrors the budget sweep's own
admin_set_status + stop_agent(graceful=True, ...) pairing
(`AgentOrchestrator._stop_budget_exceeded_agent`), best-effort: an absent
orchestrator (tests run without one) or no live container for this exact
task are silent no-ops.

A prior version ran the eviction (DB read + HTTP round-trip + up to a 10s
`docker stop`) INLINE, before the route's `db.commit()` — holding the
just-updated task/agent rows lock-held across slow I/O, the #721/
chown-storm lock-convoy class. It is now scheduled via `defer_after_commit`
so it only ever runs post-commit, and opens its own fresh DB session
(mirroring `XPostService._schedule_redraft`) since the request session may
already be closing by the time the deferred callable runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.api.deps import clear_orchestrator, set_orchestrator
from roboco.api.utils.tasks import (
    _apply_forced_status_override,
    _evict_stranded_agent,
    _StatusOverride,
)
from roboco.models.base import TaskStatus

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_orchestrator() -> Iterator[None]:
    clear_orchestrator()
    yield
    clear_orchestrator()


class _FakeSessionCM:
    """Minimal async context manager mimicking `session_factory()`."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _patch_session_factory(fake_session: Any = None) -> Any:
    """`_evict_stranded_agent` opens a FRESH session via
    `roboco.db.base.get_session_factory` (a local import) — patch it at its
    source module so the local re-import picks up the fake."""
    session_factory = MagicMock(
        return_value=_FakeSessionCM(fake_session or MagicMock())
    )
    return patch("roboco.db.base.get_session_factory", return_value=session_factory)


@pytest.mark.asyncio
async def test_evicts_live_agent_holding_the_overridden_task() -> None:
    task_id = uuid4()
    prior_holder = uuid4()

    agent = MagicMock()
    agent.slug = "be-dev-1"
    instance = MagicMock()
    instance.current_task_id = str(task_id)

    orchestrator = MagicMock()
    orchestrator.get_instance.return_value = instance
    orchestrator.stop_agent = AsyncMock()
    set_orchestrator(orchestrator)

    fake_agent_service = MagicMock()
    fake_agent_service.get_by_uuid = AsyncMock(return_value=agent)

    with (
        _patch_session_factory(),
        patch(
            "roboco.services.agent.get_agent_service",
            return_value=fake_agent_service,
        ),
    ):
        await _evict_stranded_agent(task_id, prior_holder)

    orchestrator.get_instance.assert_called_once_with("be-dev-1")
    orchestrator.stop_agent.assert_awaited_once_with(
        "be-dev-1",
        graceful=True,
        release_claim=False,
        stop_reason="admin_status_override",
    )


@pytest.mark.asyncio
async def test_noops_silently_when_orchestrator_absent() -> None:
    """No orchestrator handle set (e.g. tests) — must not raise or look up
    an agent at all (returns before ever opening a DB session)."""
    task_id = uuid4()
    prior_holder = uuid4()

    with patch("roboco.services.agent.get_agent_service") as agent_service_fn:
        await _evict_stranded_agent(task_id, prior_holder)
        agent_service_fn.assert_not_called()


@pytest.mark.asyncio
async def test_noops_when_live_instance_is_on_a_different_task() -> None:
    """A same-slug live instance exists but is not actually running this
    task — must not be stopped (a stale assignee pointer must never kill
    unrelated in-flight work)."""
    task_id = uuid4()
    prior_holder = uuid4()

    agent = MagicMock()
    agent.slug = "be-dev-1"
    instance = MagicMock()
    instance.current_task_id = str(uuid4())  # a different task

    orchestrator = MagicMock()
    orchestrator.get_instance.return_value = instance
    orchestrator.stop_agent = AsyncMock()
    set_orchestrator(orchestrator)

    fake_agent_service = MagicMock()
    fake_agent_service.get_by_uuid = AsyncMock(return_value=agent)

    with (
        _patch_session_factory(),
        patch(
            "roboco.services.agent.get_agent_service",
            return_value=fake_agent_service,
        ),
    ):
        await _evict_stranded_agent(task_id, prior_holder)

    orchestrator.stop_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_eviction_failure_is_swallowed() -> None:
    """A raising orchestrator call must not propagate — the PATCH must
    succeed regardless of eviction trouble."""
    task_id = uuid4()
    prior_holder = uuid4()

    orchestrator = MagicMock()
    orchestrator.get_instance.side_effect = RuntimeError("docker unreachable")
    set_orchestrator(orchestrator)

    fake_agent_service = MagicMock()
    fake_agent_service.get_by_uuid = AsyncMock(return_value=MagicMock(slug="be-dev-1"))

    with (
        _patch_session_factory(),
        patch(
            "roboco.services.agent.get_agent_service",
            return_value=fake_agent_service,
        ),
    ):
        # Must not raise.
        await _evict_stranded_agent(task_id, prior_holder)


@pytest.mark.asyncio
async def test_eviction_is_deferred_not_run_inline_before_commit() -> None:
    """`_apply_forced_status_override` must capture the prior holder BEFORE
    `admin_set_status` mutates the row, and must SCHEDULE the eviction via
    `defer_after_commit` — never await it inline. This pins the FIX-FIRST
    defect: eviction used to run inside the request's still-open
    transaction, holding the just-updated rows lock-held across slow I/O.
    """
    task_id = uuid4()
    prior_holder = uuid4()

    pre_task = MagicMock()
    pre_task.status = TaskStatus.IN_PROGRESS
    pre_task.active_claimant_id = prior_holder
    pre_task.assigned_to = None

    # admin_set_status clears active_claimant_id on the returned row — the
    # capture above must have already happened by the time this returns.
    post_task = MagicMock()
    post_task.active_claimant_id = None

    fake_session = MagicMock()
    fake_service = MagicMock()
    fake_service.session = fake_session
    fake_service.admin_set_status = AsyncMock(return_value=post_task)

    req = _StatusOverride(
        service=fake_service,
        task_id=task_id,
        task=pre_task,
        new_status=TaskStatus.NEEDS_REVISION,
        force=True,
        has_higher_perms=True,
        agent=MagicMock(agent_id=uuid4()),
    )

    with (
        patch("roboco.api.utils.tasks.defer_after_commit") as deferred,
        patch(
            "roboco.api.utils.tasks._evict_stranded_agent", new=AsyncMock()
        ) as evict_mock,
    ):
        result = await _apply_forced_status_override(req)

        assert result is post_task
        # Scheduled, not awaited inline.
        evict_mock.assert_not_called()
        deferred.assert_called_once()
        called_session, deferred_work = deferred.call_args.args
        assert called_session is fake_session

        # The scheduled work itself only fires post-commit; running it now
        # (still under patch, standing in for the real post-commit fire)
        # proves it targets the pre-mutation holder captured up front.
        await deferred_work()
        evict_mock.assert_awaited_once_with(task_id, prior_holder)
