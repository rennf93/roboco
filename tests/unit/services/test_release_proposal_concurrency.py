"""Concurrent CEO approve races on the shared release clone are serialized by a
Redis ``SET NX`` mutex keyed by the proposal id; a second concurrent approve
sees the lock held and refuses instead of racing on the writable clone.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services import release_proposal as rp
from roboco.services.release_executor import ReleaseResult
from roboco.services.release_proposal import (
    _RELEASE_LOCK_PREFIX,
    ReleaseProposalService,
)


def _task(*, source: str = "release_manager") -> MagicMock:
    t = MagicMock()
    t.id = uuid4()
    t.source = source
    t.status = TaskStatus.AWAITING_CEO_APPROVAL.value
    return t


def _session() -> MagicMock:
    s = MagicMock()
    s.flush = AsyncMock()
    return s


class _FakeRedis:
    """In-memory single-key store backing the release-proposal lock.

    Models ``SET NX EX``, ``GET``, ``EXPIRE`` and the two Lua scripts the
    service uses (compare-and-del release, compare-and-expire heartbeat) so the
    fencing token and heartbeat are observable without a real Redis.
    """

    def __init__(self, *, held: bool = False) -> None:
        self._force_held = held
        self._store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, bool, int]] = []
        self.eval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.expire_calls: list[tuple[str, Any]] = []

    async def set(
        self, name: str, value: str, *, nx: bool = False, ex: int = 0
    ) -> bool:
        self.set_calls.append((name, value, nx, ex))
        if nx and (self._force_held or name in self._store):
            return False
        self._store[name] = value
        return True

    async def get(self, name: str) -> str | None:
        return self._store.get(name)

    async def expire(self, name: str, seconds: int) -> bool:
        if name in self._store:
            self.expire_calls.append((name, seconds))
            return True
        return False

    async def delete(self, name: str) -> int:
        return 1 if self._store.pop(name, None) is not None else 0

    async def eval(self, script: str, _numkeys: int, *args: Any) -> int:
        self.eval_calls.append((script, args))
        key = args[0]
        token = args[1]
        if "expire" in script:
            if self._store.get(key) == token:
                self.expire_calls.append((key, args[2]))
                return 1
            return 0
        # compare-and-del release
        if self._store.get(key) == token:
            self._store.pop(key, None)
            return 1
        return 0

    async def aclose(self) -> None:
        return None


def _wire(
    task: MagicMock,
    report_dict: dict,
    executor_result: ReleaseResult,
    fake_redis: _FakeRedis,
) -> dict[str, Any]:
    """Patch every collaborator ``approve`` touches. Returns the mocks."""
    task_svc = MagicMock()
    task_svc.get = AsyncMock(return_value=task)

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=executor_result)

    markers_mod = MagicMock()
    markers_mod.get_release_report = MagicMock(return_value=report_dict)
    markers_mod.set_release_required_changes = MagicMock()

    report = MagicMock()
    return {
        "task_svc": task_svc,
        "executor": executor,
        "markers": markers_mod,
        "report": report,
        "patches": [
            patch(
                "roboco.services.release_proposal.get_task_service",
                return_value=task_svc,
            ),
            patch(
                "roboco.services.release_proposal.get_release_executor",
                AsyncMock(return_value=executor),
            ),
            patch("roboco.services.release_proposal.markers", markers_mod),
            patch(
                "roboco.services.release_proposal.report_from_dict", return_value=report
            ),
            patch(
                "roboco.services.release_proposal.redis.from_url",
                return_value=fake_redis,
            ),
        ],
    }


_REPORT = {"proposed_version": "0.13.0", "version_bump_plan": ["pyproject.toml"]}
# The CI poll ceiling is ~40 min (ReleaseExecutor._CI_MAX_POLLS * 30s); the
# lock TTL must exceed it so a crashed execute can't hold the release hostage.
_FORTY_MIN_SECONDS = 2400


@pytest.mark.asyncio
async def test_approve_acquires_lock_then_runs_executor_and_releases() -> None:
    """A single approve acquires the NX lock, runs execute, releases on success."""
    task = _task()
    fake_redis = _FakeRedis()
    published = ReleaseResult(
        status="published",
        version="0.13.0",
        files_changed=["pyproject.toml"],
        commit_sha="abc",
        release_url="https://x",
        detail="ok",
    )
    w = _wire(task, _REPORT, published, fake_redis)
    svc = ReleaseProposalService(_session())

    with (
        w["patches"][0],
        w["patches"][1],
        w["patches"][2],
        w["patches"][3],
        w["patches"][4],
    ):
        result = await svc.approve(task.id)

    assert result is not None
    assert result.status == "published"
    assert task.status == TaskStatus.COMPLETED.value
    # Lock acquired with NX + a TTL > the 40min CI ceiling, then released.
    assert len(fake_redis.set_calls) == 1
    name, _value, nx, ex = fake_redis.set_calls[0]
    assert nx is True
    assert ex >= _FORTY_MIN_SECONDS  # > the 40 min CI ceiling
    assert name.endswith(str(task.id))
    # Released via the fenced compare-and-del (a bare delete would not record
    # an eval); the lock is no longer held.
    assert await fake_redis.get(name) is None
    assert any("del" in s for s, _ in fake_redis.eval_calls)
    w["executor"].execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_approve_refused_while_lock_held() -> None:
    """A second approve while the lock is held refuses and never runs execute."""
    task = _task()
    fake_redis = _FakeRedis(held=True)  # another approve already holds the lock
    published = ReleaseResult(
        status="published",
        version="0.13.0",
        files_changed=[],
        commit_sha=None,
        release_url=None,
        detail="ok",
    )
    w = _wire(task, _REPORT, published, fake_redis)
    svc = ReleaseProposalService(_session())

    with (
        w["patches"][0],
        w["patches"][1],
        w["patches"][2],
        w["patches"][3],
        w["patches"][4],
    ):
        result = await svc.approve(task.id)

    assert result is not None
    assert result.status == "already_in_progress"
    # The executor MUST NOT run — that's the whole point of the guard.
    w["executor"].execute.assert_not_awaited()
    # Nothing committed the task to COMPLETED.
    assert task.status != TaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_failed_execute_releases_lock_so_ceo_can_retry() -> None:
    """A gate/CI failure leaves the proposal open AND releases the lock for retry."""
    task = _task()
    fake_redis = _FakeRedis()
    gate_failed = ReleaseResult(
        status="gate_failed",
        version="0.13.0",
        files_changed=["pyproject.toml"],
        commit_sha=None,
        release_url=None,
        detail="make quality failed",
    )
    w = _wire(task, _REPORT, gate_failed, fake_redis)
    svc = ReleaseProposalService(_session())

    with (
        w["patches"][0],
        w["patches"][1],
        w["patches"][2],
        w["patches"][3],
        w["patches"][4],
    ):
        result = await svc.approve(task.id)

    assert result is not None
    assert result.status == "gate_failed"
    # Proposal stays open (not COMPLETED) for retry...
    assert task.status != TaskStatus.COMPLETED.value
    # ...and the lock is released via the fenced compare-and-del so the retry
    # can acquire it.
    assert len(fake_redis.set_calls) == 1
    name, _value, _nx, _ex = fake_redis.set_calls[0]
    assert await fake_redis.get(name) is None
    assert any("del" in s for s, _ in fake_redis.eval_calls)


@pytest.mark.asyncio
async def test_fenced_release_does_not_delete_a_usurper_lock() -> None:
    """The first execute's late finally must not release a lock a usurper
    re-acquired after TTL expiry — the fencing token makes release compare-and-del."""
    task = _task()
    fake_redis = _FakeRedis()
    lock_key = f"{_RELEASE_LOCK_PREFIX}{task.id}"
    svc = ReleaseProposalService(_session())

    with patch(
        "roboco.services.release_proposal.redis.from_url", return_value=fake_redis
    ):
        token_a = await svc._acquire_release_lock(lock_key)
        assert token_a is not None
        assert await fake_redis.get(lock_key) == token_a

        # TTL expired mid-execute and a second approve re-acquired with its own token.
        fake_redis._store[lock_key] = "usurper-token"

        # The first execute's finally runs late and tries to release its stale token.
        await svc._release_release_lock(lock_key, token_a)

    # The usurper's lock survives — a bare DEL would have deleted it.
    assert await fake_redis.get(lock_key) == "usurper-token"

    # And the positive case: releasing with the owning token does clear it.
    with patch(
        "roboco.services.release_proposal.redis.from_url", return_value=fake_redis
    ):
        await svc._release_release_lock(lock_key, "usurper-token")
    assert await fake_redis.get(lock_key) is None


@pytest.mark.asyncio
async def test_redis_outage_returns_redis_unavailable_not_already_in_progress() -> None:
    """#89: when Redis itself is unreachable, ``approve`` stays fail-closed
    (execute never runs) but returns a distinct ``redis_unavailable`` result so
    the CEO sees the real cause — not a misleading ``already_in_progress``
    (which would imply a concurrent approve to wait out)."""
    task = _task()
    published = ReleaseResult(
        status="published",
        version="0.13.0",
        files_changed=[],
        commit_sha=None,
        release_url=None,
        detail="ok",
    )

    broken_redis = MagicMock()
    broken_redis.set = AsyncMock(side_effect=ConnectionError("redis down"))
    broken_redis.aclose = AsyncMock()

    task_svc = MagicMock()
    task_svc.get = AsyncMock(return_value=task)
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=published)
    markers_mod = MagicMock()
    markers_mod.get_release_report = MagicMock(return_value=_REPORT)
    report = MagicMock()
    report.proposed_version = "0.13.0"
    report_from_dict_mock = MagicMock(return_value=report)

    svc = ReleaseProposalService(_session())
    with (
        patch(
            "roboco.services.release_proposal.get_task_service", return_value=task_svc
        ),
        patch(
            "roboco.services.release_proposal.get_release_executor",
            AsyncMock(return_value=executor),
        ),
        patch("roboco.services.release_proposal.markers", markers_mod),
        patch(
            "roboco.services.release_proposal.report_from_dict",
            report_from_dict_mock,
        ),
        patch(
            "roboco.services.release_proposal.redis.from_url", return_value=broken_redis
        ),
    ):
        result = await svc.approve(task.id)

    assert result is not None
    assert result.status == "redis_unavailable"
    assert result.release_url is None
    # Fail-closed: the executor MUST NOT run without the mutex.
    executor.execute.assert_not_awaited()
    # The report is built before the lock attempt so the result carries the
    # proposed version even on an infra failure (the CEO sees which version
    # the approval was for).
    report_from_dict_mock.assert_called_once_with(_REPORT)
    # And the proposal is not marked COMPLETED.
    assert task.status != TaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_heartbeat_refreshes_lock_and_is_cancelled_in_finally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heartbeat refreshes the TTL while the execute owns the lock and is
    cancelled (no leaked task) before the fenced release."""
    monkeypatch.setattr(rp, "_RELEASE_LOCK_HEARTBEAT_SECONDS", 0.001)

    task = _task()
    fake_redis = _FakeRedis()
    published = ReleaseResult(
        status="published",
        version="0.13.0",
        files_changed=["pyproject.toml"],
        commit_sha="abc",
        release_url="https://x",
        detail="ok",
    )

    async def _slow_execute(_report: Any) -> ReleaseResult:
        # Yield long enough for ≥1 heartbeat refresh to land.
        await asyncio.sleep(0.02)
        return published

    w = _wire(task, _REPORT, published, fake_redis)
    w["executor"].execute = AsyncMock(side_effect=_slow_execute)
    svc = ReleaseProposalService(_session())

    with (
        w["patches"][0],
        w["patches"][1],
        w["patches"][2],
        w["patches"][3],
        w["patches"][4],
    ):
        result = await svc.approve(task.id)

    assert result is not None
    assert result.status == "published"
    name, _token, _nx, _ex = fake_redis.set_calls[0]
    # At least one compare-and-expire refreshed the TTL — the fake only records
    # an expire when the stored value still equals our fencing token, so this
    # proves the heartbeat extended a lock we still owned.
    assert len(fake_redis.expire_calls) >= 1
    assert all(k == name for k, _ttl in fake_redis.expire_calls)
    # And the lock was released at the end.
    assert await fake_redis.get(name) is None


@pytest.mark.asyncio
async def test_already_published_closes_proposal_not_wedges_open() -> None:
    """#149: when a prior publish landed (tag exists) but the route commit
    failed / 504'd, a retry sees the tag and execute returns ``already_published``.
    approve() must STILL mark the proposal COMPLETED — the release shipped —
    else the old ``if status == 'published'`` check wedged it open forever (every
    retry returns already_published and never closes it; only a manual cancel
    unsticks it)."""
    task = _task()
    fake_redis = _FakeRedis()
    already = ReleaseResult(
        status="already_published",
        version="0.13.0",
        files_changed=[],
        commit_sha=None,
        release_url=None,
        detail="v0.13.0 is already published; nothing to do.",
    )
    w = _wire(task, _REPORT, already, fake_redis)
    svc = ReleaseProposalService(_session())

    with (
        w["patches"][0],
        w["patches"][1],
        w["patches"][2],
        w["patches"][3],
        w["patches"][4],
    ):
        result = await svc.approve(task.id)

    assert result is not None
    assert result.status == "already_published"
    # The proposal is CLOSED — the release shipped — not wedged open.
    assert task.status == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_heartbeat_lock_loss_cancels_execute_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#218: when the heartbeat finds the lock no longer owned (a >TTL Redis
    outage let the mutex expire and a usurper re-acquired), it must CANCEL the
    in-flight execute fail-closed — not ``return`` silently and leave execute
    running unguarded, which lets the usurper's approve ``rm -rf`` the in-flight
    release clone (re-opening the race the mutex+heartbeat exist to prevent).
    approve() surfaces a structured ``lock_lost`` result."""
    monkeypatch.setattr(rp, "_RELEASE_LOCK_HEARTBEAT_SECONDS", 0.001)

    task = _task()
    fake_redis = _FakeRedis()
    published = ReleaseResult(
        status="published",
        version="0.13.0",
        files_changed=[],
        commit_sha=None,
        release_url=None,
        detail="ok",
    )

    execute_started = asyncio.Event()

    async def _blocking_execute(_report: Any) -> ReleaseResult:
        # Block until the heartbeat cancels us — proves execute was running and
        # got cancelled, not never-started.
        execute_started.set()
        await asyncio.sleep(60)
        return published

    w = _wire(task, _REPORT, published, fake_redis)
    w["executor"].execute = AsyncMock(side_effect=_blocking_execute)
    svc = ReleaseProposalService(_session())
    # The heartbeat's compare-and-expire reports the lock lost (token mismatch —
    # a usurper re-acquired after TTL expiry).
    svc._heartbeat_release_lock = AsyncMock(return_value=False)

    with (
        w["patches"][0],
        w["patches"][1],
        w["patches"][2],
        w["patches"][3],
        w["patches"][4],
    ):
        result = await svc.approve(task.id)

    assert result is not None
    assert result.status == "lock_lost"
    assert execute_started.is_set()  # execute did start, then was cancelled
    # Fail-closed: the proposal is NOT marked COMPLETED.
    assert task.status != TaskStatus.COMPLETED.value
