"""An admin status override that clears a live claim must evict the
stranded agent's container, regardless of ``force``.

Live incident: a non-forced ``PATCH /api/tasks/{id}`` with
``{"status": "needs_revision"}`` needs no ``force`` (NEEDS_REVISION is not a
hatch/resurrect state), so ``TaskService.admin_set_status`` clears
``active_claimant_id`` for the review-queue target but the eviction used to
be gated on ``force`` at the API-route layer alone. A dev's container kept
flailing ``not_authorized`` on every content verb against a task it no
longer legitimately held. The same gap applied to
``SecretaryService``'s ``"override"`` action, which calls
``TaskService.admin_set_status`` directly and never evicted anything.

The fix moved the eviction machinery (``_evict_stranded_agent`` /
``_schedule_stranded_eviction``) from the API route layer
(``roboco/api/utils/tasks.py``) into ``TaskService.admin_set_status`` itself
(``roboco/services/task.py``) -- the chokepoint every caller of a status
override routes through -- and made the trigger the fact that a live claim
actually got cleared, not the ``force`` flag. Every caller (the PATCH route,
the Secretary's override action) gets the eviction for free.

``_evict_stranded_agent`` itself is unchanged: best-effort, scheduled via
``defer_after_commit`` so it only ever runs post-commit (never inline,
holding the just-updated rows lock-held across slow container-stop I/O --
the #721/chown-storm lock-convoy class), and opens its own fresh DB session
since the caller's session may already be closing by the time it runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.api.deps import clear_orchestrator, set_orchestrator
from roboco.models.base import TaskStatus
from roboco.services.task import TaskService, _evict_stranded_agent

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_orchestrator() -> Iterator[None]:
    clear_orchestrator()
    yield
    clear_orchestrator()


def _build_task(**overrides: object) -> MagicMock:
    base: dict[str, object] = {
        "id": uuid4(),
        "status": TaskStatus.IN_PROGRESS,
        "assigned_to": None,
        "claimed_by": None,
        "claimed_at": None,
        "active_claimant_id": None,
        "pre_block_state": None,
        "pre_block_assignee": None,
        "pre_block_metadata": None,
        "blocker_resolver_type": None,
        "blocker_raised_by": None,
    }
    base.update(overrides)
    return MagicMock(**base)


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


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
    `roboco.db.base.get_session_factory` (a local import), so patch it at
    its source module so the local re-import picks up the fake."""
    session_factory = MagicMock(
        return_value=_FakeSessionCM(fake_session or MagicMock())
    )
    return patch("roboco.db.base.get_session_factory", return_value=session_factory)


# ---------------------------------------------------------------------------
# _evict_stranded_agent mechanics (moved verbatim from the API-layer test)
# ---------------------------------------------------------------------------


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
    """No orchestrator handle set (e.g. tests): must not raise or look up
    an agent at all (returns before ever opening a DB session)."""
    task_id = uuid4()
    prior_holder = uuid4()

    with patch("roboco.services.agent.get_agent_service") as agent_service_fn:
        await _evict_stranded_agent(task_id, prior_holder)
        agent_service_fn.assert_not_called()


@pytest.mark.asyncio
async def test_noops_when_live_instance_is_on_a_different_task() -> None:
    """A same-slug live instance exists but is not actually running this
    task, so it must not be stopped (a stale assignee pointer must never
    kill unrelated in-flight work)."""
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
    """A raising orchestrator call must not propagate: the caller's own
    write must succeed regardless of eviction trouble."""
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


# ---------------------------------------------------------------------------
# admin_set_status: the actual bug fix, eviction fires regardless of `force`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_set_status_schedules_eviction_without_force() -> None:
    """MAJOR bug fix: a non-forced admin override into a review-queue state
    (e.g. NEEDS_REVISION, which needs no `force`) must still schedule the
    eviction of the live claimant's container. `force` only gates whether
    the STATUS bypass itself is acknowledged; it must not gate whether a
    stranded agent gets cleaned up. Previously only `force=True` scheduled
    this, so an ordinary unforced override stranded the agent forever."""
    dev = uuid4()
    task = _build_task(
        status=TaskStatus.IN_PROGRESS,
        assigned_to=dev,
        claimed_by=dev,
        active_claimant_id=dev,
    )
    session = MagicMock(flush=AsyncMock(), get=AsyncMock(return_value=None))
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))

    with patch("roboco.services.notification_delivery.defer_after_commit") as deferred:
        out = await svc.admin_set_status(task.id, TaskStatus.NEEDS_REVISION)

    assert out is task
    assert task.active_claimant_id is None
    deferred.assert_called_once()
    called_session, deferred_work = deferred.call_args.args
    assert called_session is session

    # The scheduled work targets the PRE-clear claimant.
    with patch(
        "roboco.services.task._evict_stranded_agent", new=AsyncMock()
    ) as evict_mock:
        await deferred_work()
    evict_mock.assert_awaited_once_with(task.id, dev)


@pytest.mark.asyncio
async def test_admin_set_status_schedules_eviction_with_force_too() -> None:
    """The forced path (a hatch-state override, e.g. -> COMPLETED with
    force=True) also schedules the eviction: the fix is additive, not a
    force -> no-force swap."""
    dev = uuid4()
    task = _build_task(
        status=TaskStatus.AWAITING_PM_REVIEW,
        assigned_to=dev,
        claimed_by=dev,
        active_claimant_id=dev,
    )
    session = MagicMock(flush=AsyncMock(), get=AsyncMock(return_value=None))
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))

    with patch("roboco.services.notification_delivery.defer_after_commit") as deferred:
        await svc.admin_set_status(
            task.id, TaskStatus.AWAITING_QA, actor_id=dev, force=True
        )

    deferred.assert_called_once()


@pytest.mark.asyncio
async def test_admin_set_status_no_live_claimant_schedules_no_eviction() -> None:
    """A review-queue override on a task with no active_claimant_id (nobody
    actually holds a live claim) must not schedule an eviction: there is
    nothing stranded."""
    task = _build_task(
        status=TaskStatus.IN_PROGRESS,
        active_claimant_id=None,
    )
    session = MagicMock(flush=AsyncMock(), get=AsyncMock(return_value=None))
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))

    with patch("roboco.services.notification_delivery.defer_after_commit") as deferred:
        await svc.admin_set_status(task.id, TaskStatus.NEEDS_REVISION)

    deferred.assert_not_called()


@pytest.mark.asyncio
async def test_admin_set_status_blocked_target_schedules_no_eviction() -> None:
    """A target outside `_REVIEW_QUEUE_STATES` (e.g. BLOCKED) clears no
    claim, so it must schedule no eviction: a must-not-change-behavior
    case from the bug report."""
    dev = uuid4()
    task = _build_task(
        status=TaskStatus.IN_PROGRESS,
        assigned_to=dev,
        claimed_by=dev,
        active_claimant_id=dev,
    )
    session = MagicMock(flush=AsyncMock(), get=AsyncMock(return_value=None))
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))

    with patch("roboco.services.notification_delivery.defer_after_commit") as deferred:
        await svc.admin_set_status(task.id, TaskStatus.BLOCKED)

    deferred.assert_not_called()
    assert task.active_claimant_id == dev


@pytest.mark.asyncio
async def test_admin_set_status_ceo_approval_target_schedules_eviction() -> None:
    """AWAITING_CEO_APPROVAL joined ``_REVIEW_QUEUE_STATES`` (2026-08 fix):
    a stale ``active_claimant_id`` surviving an admin override into this
    state is just as wrong as any other queue target, even though the CEO
    approves directly with no claim() edge of its own - live evidence was a
    task in awaiting_ceo_approval with assigned_to=ceo but
    active_claimant_id=main-pm. Previously this state was explicitly
    excluded (see git history) and left the stale claim + a stranded
    container in place."""
    dev = uuid4()
    task = _build_task(
        status=TaskStatus.AWAITING_PM_REVIEW,
        assigned_to=dev,
        claimed_by=dev,
        active_claimant_id=dev,
    )
    session = MagicMock(flush=AsyncMock(), get=AsyncMock(return_value=None))
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))

    with patch("roboco.services.notification_delivery.defer_after_commit") as deferred:
        await svc.admin_set_status(task.id, TaskStatus.AWAITING_CEO_APPROVAL)

    assert task.active_claimant_id is None
    deferred.assert_called_once()
