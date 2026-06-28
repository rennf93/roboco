"""F013 — concurrent approve races on the shared release clone.

The approve flow ran the ~40min ``ReleaseExecutor.execute`` with no guard, so
two concurrent CEO ``POST /proposal/approve`` calls (double-click, panel retry)
both found the same held proposal and raced on the shared, ``rm -rf``'d writable
release clone — interleaving ``git add``/``commit``/``push`` and corrupting the
release. The fix acquires a Redis ``SET NX`` mutex keyed by the proposal id
before execute (TTL > the 40min CI ceiling) and releases it on completion; a
second concurrent approve sees the lock held and refuses instead of racing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services.release_executor import ReleaseResult
from roboco.services.release_proposal import ReleaseProposalService


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
    """Single-key SET NX / DEL recorder for the release-proposal lock."""

    def __init__(self, *, held: bool = False) -> None:
        self._held = held
        self.set_calls: list[tuple[str, str, bool, int]] = []
        self.del_calls: list[str] = []

    async def set(
        self, name: str, value: str, *, nx: bool = False, ex: int = 0
    ) -> bool:
        self.set_calls.append((name, value, nx, ex))
        if nx and self._held:
            return False
        self._held = True
        return True

    async def delete(self, name: str) -> int:
        self.del_calls.append(name)
        self._held = False
        return 1

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
    assert fake_redis.del_calls == [name]
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
    # ...and the lock is released so the retry can acquire it.
    assert len(fake_redis.del_calls) == 1
