"""Cross-task review-path outage detection."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from roboco.api.routes import health as health_routes
from roboco.api.routes.git import get_git_status
from roboco.api.routes.health import review_path_health
from roboco.api.utils.git import (
    _translate_read_error,
    resolve_review_path_key,
    review_path_key,
    review_path_read_outcome,
)
from roboco.config import settings
from roboco.exceptions import GitCommandError, GitTimeoutError
from roboco.services.base import NotFoundError, ServiceError
from roboco.services.gateway.choreographer.evidence_legs import (
    LegBudget,
    run_bounded_leg,
)
from roboco.services.review_path_health import (
    ReviewPathHealthTracker,
    is_infrastructure_failure,
    sweep_review_path_health,
)


class FakeRedis:
    """In-memory stand-in for the redis.asyncio commands the tracker uses."""

    def __init__(self) -> None:
        self.z: dict[str, dict[str, float]] = {}
        self.h: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.z.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key: str, _lo: str, hi: str) -> None:
        exclusive = hi.startswith("(")
        top = float(hi.lstrip("("))
        zset = self.z.get(key, {})
        for member in [
            m for m, sc in zset.items() if (sc < top if exclusive else sc <= top)
        ]:
            del zset[member]

    async def zrange(
        self, key: str, _lo: int, _hi: int, withscores: bool = False
    ) -> list[tuple[str, float]]:
        assert withscores
        return sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])

    async def delete(self, key: str) -> None:
        self.z.pop(key, None)

    async def hget(self, key: str, field: str) -> str | None:
        return self.h.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.h.get(key, {}))

    async def hset(
        self,
        key: str,
        field: str | None = None,
        value: str | None = None,
        mapping: dict[str, str] | None = None,
    ) -> None:
        fields = dict(mapping or {})
        if field is not None:
            fields[field] = str(value)
        self.h.setdefault(key, {}).update(fields)

    async def hsetnx(self, key: str, field: str, value: str) -> int:
        hash_ = self.h.setdefault(key, {})
        if field in hash_:
            return 0
        hash_[field] = value
        return 1

    async def hdel(self, key: str, field: str) -> int:
        return 1 if self.h.get(key, {}).pop(field, None) is not None else 0

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        hash_ = self.h.setdefault(key, {})
        hash_[field] = str(int(hash_.get(field, "0")) + amount)
        return int(hash_[field])

    async def rpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).append(value)

    async def lpop(self, key: str) -> str | None:
        queue = self.lists.get(key, [])
        return queue.pop(0) if queue else None


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """One shared fake behind every tracker, like separate processes on one
    Redis."""
    fake = FakeRedis()
    monkeypatch.setattr(
        "roboco.services.review_path_health.redis.from_url",
        lambda *_a, **_k: fake,
    )
    return fake


@pytest.fixture
def t0() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=30)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "review_path_outage_window_seconds", 300)
    monkeypatch.setattr(settings, "review_path_outage_distinct_tasks", 3)
    monkeypatch.setattr(settings, "review_path_recovery_successes", 2)


@pytest.mark.asyncio
async def test_single_task_failing_repeatedly_is_not_an_outage(
    t0: datetime,
) -> None:
    t = ReviewPathHealthTracker()
    for i in range(20):
        await t.record_failure("task-a", t0 + timedelta(seconds=i))
    assert (await t.state(t0 + timedelta(seconds=21)))["outage_active"] is False
    assert await t.pending_transition() is None


@pytest.mark.asyncio
async def test_distinct_tasks_trip_once_and_dedupe(t0: datetime) -> None:
    t = ReviewPathHealthTracker()
    for i, key in enumerate(["a", "b", "c", "a", "d"]):
        await t.record_failure(key, t0 + timedelta(seconds=i))
    state = await t.state(t0 + timedelta(seconds=10))
    assert state == {
        "outage_active": True,
        "type": "review_path_tools",
        "window_start": t0,
        "recovered": False,
    }
    assert await t.pending_transition() == "active"
    assert await t.pending_transition() is None


@pytest.mark.asyncio
async def test_failures_outside_window_do_not_count(t0: datetime) -> None:
    t = ReviewPathHealthTracker()
    await t.record_failure("a", t0)
    await t.record_failure("b", t0 + timedelta(seconds=1))
    await t.record_failure("c", t0 + timedelta(seconds=400))
    assert (await t.state(t0 + timedelta(seconds=401)))["outage_active"] is False


@pytest.mark.asyncio
async def test_consecutive_successes_recover(t0: datetime) -> None:
    t = ReviewPathHealthTracker()
    for i, key in enumerate("abc"):
        await t.record_failure(key, t0 + timedelta(seconds=i))
    assert await t.pending_transition() == "active"
    await t.record_success()
    assert (await t.state())["outage_active"] is True
    await t.record_success()
    state = await t.state()
    assert state["outage_active"] is False
    assert state["recovered"] is True
    assert await t.pending_transition() == "recovered"
    assert await t.pending_transition() is None


@pytest.mark.asyncio
async def test_sweep_sends_one_notification_per_transition() -> None:
    t = ReviewPathHealthTracker()
    now = datetime.now(UTC)
    for key in "abc":
        await t.record_failure(key, now)
    send = AsyncMock()
    with (
        patch("roboco.services.review_path_health._tracker", t),
        patch(
            "roboco.services.notification.NotificationService."
            "send_review_path_outage_notification",
            send,
        ),
    ):
        await sweep_review_path_health()
        await sweep_review_path_health()
    assert send.await_count == 1
    assert send.await_args is not None
    assert send.await_args.kwargs["active"] is True


@pytest.fixture
def tracker() -> Iterator[ReviewPathHealthTracker]:
    t = ReviewPathHealthTracker()
    with patch("roboco.services.review_path_health._tracker", t):
        yield t


async def _failed_keys(t: ReviewPathHealthTracker) -> list[str]:
    members = await t._r.zrange("roboco:review_path:failures", 0, -1, withscores=True)
    return [member.rsplit("|", 1)[0] for member, _ in members]


async def _boom(exc: BaseException) -> None:
    raise exc


async def _ok() -> str:
    return "done"


@pytest.mark.asyncio
async def test_sweep_reports_outage_that_tripped_and_cleared_between_sweeps(
    tracker: ReviewPathHealthTracker,
) -> None:
    for key in "abc":
        await tracker.record_failure(key)
    await tracker.record_success()
    await tracker.record_success()
    send = AsyncMock()
    with patch(
        "roboco.services.notification.NotificationService."
        "send_review_path_outage_notification",
        send,
    ):
        await sweep_review_path_health()
        await sweep_review_path_health()
    assert [c.kwargs["active"] for c in send.await_args_list] == [True, False]


@pytest.mark.asyncio
async def test_run_bounded_leg_records_failure_keyed_on_task_id(
    tracker: ReviewPathHealthTracker,
) -> None:
    gaps: list[str] = []
    for task_id in ("t1", "t2", "t3"):
        await run_bounded_leg(
            _boom(GitTimeoutError("git log", 5)),
            default=None,
            budget=LegBudget(30),
            leg="log",
            hint="h",
            task_id=task_id,
            gaps=gaps,
        )
    assert await _failed_keys(tracker) == ["t1", "t2", "t3"]
    assert (await tracker.state())["outage_active"] is True


@pytest.mark.asyncio
async def test_run_bounded_leg_records_unexpected_error_and_reraises(
    tracker: ReviewPathHealthTracker,
) -> None:
    with pytest.raises(RuntimeError):
        await run_bounded_leg(
            _boom(RuntimeError("x")),
            default=None,
            budget=LegBudget(30),
            leg="log",
            hint="h",
            task_id="t9",
            gaps=[],
        )
    assert await _failed_keys(tracker) == ["t9"]


@pytest.mark.asyncio
async def test_run_bounded_leg_success_counts_toward_recovery(
    tracker: ReviewPathHealthTracker,
) -> None:
    for key in "abc":
        await tracker.record_failure(key)
    for _ in range(settings.review_path_recovery_successes):
        result = await run_bounded_leg(
            _ok(),
            default="",
            budget=LegBudget(30),
            leg="log",
            hint="h",
            task_id="t1",
            gaps=[],
        )
        assert result == "done"
    state = await tracker.state()
    assert state["outage_active"] is False
    assert state["recovered"] is True


@pytest.mark.asyncio
async def test_translate_read_error_records_5xx_with_given_key(
    tracker: ReviewPathHealthTracker,
) -> None:
    exc = await _translate_read_error(ServiceError("boom"), "feature/backend/abc")
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert await _failed_keys(tracker) == ["feature/backend/abc"]


@pytest.mark.asyncio
async def test_one_task_failing_across_status_log_diff_is_not_an_outage(
    tracker: ReviewPathHealthTracker,
) -> None:
    task_id = "0123abcd-1111-2222-3333-444455556666"
    branch = "feature/backend/be2145b0--1498a7cc--0123abcd"
    keys = (
        review_path_key(task_id=task_id, project_slug="p"),  # status
        review_path_key(branch=branch, project_slug="p"),  # log
        review_path_key(task_id=task_id, project_slug="p"),  # diff
        review_path_key(branch=branch),  # file
    )
    assert set(keys) == {"0123abcd"}
    for key in keys:
        await _translate_read_error(ServiceError("boom"), key)
    assert (await tracker.state())["outage_active"] is False


def test_review_path_key_namespaces_fallbacks() -> None:
    assert review_path_key(branch="main", project_slug="p") == "branch:main"
    assert review_path_key(project_slug="p") == "project:p"


@pytest.mark.asyncio
async def test_resolve_review_path_key_falls_back_when_active_task_lookup_fails() -> (
    None
):
    agent_id = uuid4()
    failing_service = SimpleNamespace(
        get_active_task_for_agent=AsyncMock(side_effect=RuntimeError("poisoned"))
    )
    with patch("roboco.services.task.get_task_service", return_value=failing_service):
        key = await resolve_review_path_key(AsyncMock(), agent_id)
    assert key == f"agent:{agent_id}"


@pytest.mark.asyncio
async def test_translate_read_error_ignores_4xx(
    tracker: ReviewPathHealthTracker,
) -> None:
    exc = await _translate_read_error(NotFoundError("Branch", "x"), "k")
    assert exc.status_code == status.HTTP_404_NOT_FOUND
    assert await _failed_keys(tracker) == []


@pytest.mark.asyncio
async def test_health_route_returns_contract_fields(
    tracker: ReviewPathHealthTracker,
) -> None:
    idle = await review_path_health()
    assert idle.model_dump() == {
        "outage_active": False,
        "type": "review_path_tools",
        "window_start": None,
        "recovered": False,
    }
    for key in "abc":
        await tracker.record_failure(key)
    active = await review_path_health()
    assert active.outage_active is True
    assert active.window_start is not None
    assert active.type == "review_path_tools"


def test_health_route_serves_frozen_path() -> None:
    app = FastAPI()
    app.include_router(health_routes.router)
    response = TestClient(app).get("/api/system-health/review-path")
    assert response.status_code == status.HTTP_200_OK
    assert set(response.json()) == {
        "outage_active",
        "type",
        "window_start",
        "recovered",
    }


@pytest.mark.asyncio
async def test_run_bounded_leg_ignores_caller_errors(
    tracker: ReviewPathHealthTracker,
) -> None:
    for task_id in ("t1", "t2", "t3"):
        with pytest.raises(NotFoundError):
            await run_bounded_leg(
                _boom(NotFoundError("Branch", "gone")),
                default=None,
                budget=LegBudget(30),
                leg="log",
                hint="h",
                task_id=task_id,
                gaps=[],
            )
    assert await _failed_keys(tracker) == []
    assert (await tracker.state())["outage_active"] is False


def test_infrastructure_classifier_sees_wrapped_caller_error() -> None:
    wrapped = RuntimeError("wrapper")
    wrapped.__cause__ = NotFoundError("Branch", "gone")
    assert is_infrastructure_failure(wrapped) is False
    assert is_infrastructure_failure(OSError("disk")) is True


@pytest.mark.asyncio
async def test_window_expiry_closes_outage_without_claiming_recovery(
    t0: datetime,
) -> None:
    t = ReviewPathHealthTracker()
    for i, key in enumerate("abc"):
        await t.record_failure(key, t0 + timedelta(seconds=i))
    assert await t.pending_transition() == "active"
    state = await t.state(t0 + timedelta(seconds=400))
    assert state["outage_active"] is False
    assert state["recovered"] is False
    assert await t.pending_transition() == "expired"
    assert await t.pending_transition() is None


@pytest.mark.asyncio
async def test_sweep_words_expiry_as_not_recovered(
    tracker: ReviewPathHealthTracker,
) -> None:
    await tracker._r.rpush("roboco:review_path:transitions", "expired")
    send = AsyncMock()
    with patch(
        "roboco.services.notification.NotificationService."
        "send_review_path_outage_notification",
        send,
    ):
        await sweep_review_path_health()
    assert send.await_args is not None
    kwargs = send.await_args.kwargs
    assert kwargs["active"] is False
    assert kwargs["recovered"] is False


@pytest.mark.asyncio
async def test_read_outcome_dependency_records_success(
    tracker: ReviewPathHealthTracker,
) -> None:
    for key in "abc":
        await tracker.record_failure(key)
    for _ in range(settings.review_path_recovery_successes):
        gen = review_path_read_outcome()
        await gen.__anext__()
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
    assert (await tracker.state())["recovered"] is True


@pytest.mark.asyncio
async def test_three_agents_without_task_id_trip_the_outage(
    tracker: ReviewPathHealthTracker,
) -> None:
    agents = {uuid4(): uuid4() for _ in range(3)}  # agent id -> active task id

    class _Tasks:
        async def get_active_task_for_agent(self, agent_id: UUID) -> SimpleNamespace:
            return SimpleNamespace(id=agents[agent_id])

    git_service = SimpleNamespace(
        tree_for_agent_read=AsyncMock(side_effect=GitCommandError("git status", "x"))
    )
    with (
        patch(
            "roboco.api.routes.git._resolve_project_slug", AsyncMock(return_value="p")
        ),
        patch("roboco.api.routes.git.get_git_service", return_value=git_service),
        patch("roboco.services.task.get_task_service", return_value=_Tasks()),
    ):
        for agent_id in agents:
            with pytest.raises(HTTPException):
                await get_git_status(
                    db=AsyncMock(),
                    agent=_agent_ctx(agent_id),
                    project_slug="p",
                    task_id=None,
                    legacy_task_id=None,
                )
    assert len(set(await _failed_keys(tracker))) == len(agents)
    assert (await tracker.state())["outage_active"] is True


@pytest.mark.asyncio
async def test_resolver_falls_back_to_agent_key() -> None:
    agent_id = uuid4()

    class _NoTask:
        async def get_active_task_for_agent(self, _: UUID) -> None:
            return None

    with patch("roboco.services.task.get_task_service", return_value=_NoTask()):
        key = await resolve_review_path_key(AsyncMock(), agent_id, project_slug="p")
    assert key == f"agent:{agent_id}"


def _agent_ctx(agent_id: UUID) -> Any:
    return SimpleNamespace(agent_id=agent_id)


@pytest.mark.asyncio
async def test_state_is_shared_across_tracker_instances_and_delivers_once(
    t0: datetime,
) -> None:
    """The api role records, the dispatcher sweeps, any process serves the
    panel: separate tracker instances on one Redis agree, and each transition
    is handed to exactly one caller."""
    api, dispatcher, panel = (ReviewPathHealthTracker() for _ in range(3))
    for i, key in enumerate("abc"):
        await api.record_failure(key, t0 + timedelta(seconds=i))
    seen = await panel.state(t0 + timedelta(seconds=5))
    assert seen["outage_active"] is True
    assert seen["window_start"] == t0
    assert await dispatcher.pending_transition() == "active"
    assert await api.pending_transition() is None
    await api.record_success()
    await api.record_success()
    assert (await panel.state())["recovered"] is True
    assert await dispatcher.pending_transition() == "recovered"
    assert await panel.pending_transition() is None


@pytest.mark.asyncio
async def test_redis_errors_are_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _down(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr("roboco.services.review_path_health.redis.from_url", _down)
    t = ReviewPathHealthTracker()
    await t.record_failure("a")
    await t.record_success()
    assert await t.pending_transition() is None
    assert (await t.state())["outage_active"] is False
