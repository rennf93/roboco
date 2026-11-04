"""i_am_done's quality gate runs as a background job with a callback.

Covers the registry (dedupe, crash, timeout, swallowed callback failure), the
callback's transitions (pass reaches QA; fail, crash and timeout never do;
duplicate callbacks are no-ops) and the job bodies' session handling.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gate_jobs import GateJobRegistry, failed_verdict
from roboco.services.gateway.choreographer import Choreographer, gate_job
from roboco.services.gateway.quality_gate import GateResult
from roboco.services.task import TaskService
from tests.unit.gateway.conftest import RecordingGateRegistry
from tests.unit.gateway.test_choreographer_submit_qa_gates import (
    _make_deps,
    _ready_task,
)

_TIMEOUT = 5.0


async def _drain(registry: GateJobRegistry, task_id: Any) -> None:
    while registry.is_running(task_id):
        await asyncio.sleep(0)


def _choreo_for(task: Any) -> tuple[Choreographer, AsyncMock]:
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.submit_qa.return_value = MagicMock(
        **{**task.__dict__, "status": "awaiting_qa", "assigned_to": None}
    )
    task_svc.qa_agent_for_team.return_value = MagicMock(
        id=uuid4(), skills=[{"id": "code_review"}]
    )
    return Choreographer(_make_deps(task=task_svc)), task_svc


# --- registry --------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_runs_job_then_finish_with_its_verdict() -> None:
    registry, task_id = GateJobRegistry(), uuid4()
    seen: list[GateResult] = []

    async def run() -> GateResult:
        return GateResult(passed=True)

    async def finish(verdict: GateResult) -> None:
        seen.append(verdict)

    assert registry.launch(task_id, run, finish, timeout_seconds=_TIMEOUT)
    await _drain(registry, task_id)
    assert [v.passed for v in seen] == [True]


@pytest.mark.asyncio
async def test_registry_refuses_a_second_job_while_one_runs() -> None:
    registry, task_id = GateJobRegistry(), uuid4()
    release = asyncio.Event()

    async def run() -> GateResult:
        await release.wait()
        return GateResult(passed=True)

    finish = AsyncMock()
    assert registry.launch(task_id, run, finish, timeout_seconds=_TIMEOUT)
    assert registry.is_running(task_id)
    assert not registry.launch(task_id, run, finish, timeout_seconds=_TIMEOUT)
    release.set()
    await _drain(registry, task_id)
    finish.assert_awaited_once()
    assert registry.launch(task_id, run, finish, timeout_seconds=_TIMEOUT)
    await _drain(registry, task_id)


@pytest.mark.asyncio
async def test_a_crashed_job_finishes_with_a_red_verdict_not_a_pass() -> None:
    registry, task_id = GateJobRegistry(), uuid4()
    finish = AsyncMock()

    async def run() -> GateResult:
        raise RuntimeError("container exploded")

    registry.launch(task_id, run, finish, timeout_seconds=_TIMEOUT)
    await _drain(registry, task_id)
    assert finish.await_args is not None
    verdict = finish.await_args.args[0]
    assert not verdict.passed
    assert "container exploded" in verdict.output


@pytest.mark.asyncio
async def test_a_timed_out_job_finishes_with_a_red_verdict() -> None:
    registry, task_id = GateJobRegistry(), uuid4()
    finish = AsyncMock()

    async def run() -> GateResult:
        await asyncio.sleep(60)
        return GateResult(passed=True)

    registry.launch(task_id, run, finish, timeout_seconds=0.01)
    await _drain(registry, task_id)
    assert finish.await_args is not None
    verdict = finish.await_args.args[0]
    assert not verdict.passed
    assert "timed out" in verdict.output


@pytest.mark.asyncio
async def test_a_raising_finish_is_swallowed_and_the_job_is_forgotten() -> None:
    registry, task_id = GateJobRegistry(), uuid4()

    async def run() -> GateResult:
        return GateResult(passed=True)

    finish = AsyncMock(side_effect=RuntimeError("db down"))
    registry.launch(task_id, run, finish, timeout_seconds=_TIMEOUT)
    await _drain(registry, task_id)
    assert not registry.is_running(task_id)


def test_failed_verdict_is_red() -> None:
    assert not failed_verdict("boom").passed


# --- callback transitions --------------------------------------------------


@pytest.mark.asyncio
async def test_gate_pass_callback_moves_the_task_to_qa() -> None:
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task.status = "verifying"
    c, task_svc = _choreo_for(task)

    outcome = await c.apply_gate_verdict(
        agent_id, task_id, "done", "tok", GateResult(True)
    )

    assert outcome == "submitted"
    task_svc.consume_gate_run.assert_awaited_once_with(task_id, "tok")
    task_svc.submit_qa.assert_awaited_once()
    task_svc.reassign.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict",
    [
        GateResult(passed=False, failures=("lint",), output="E501 line too long"),
        failed_verdict("quality gate job crashed: boom"),
        failed_verdict("quality gate timed out after 900s"),
    ],
)
async def test_gate_fail_callback_never_reaches_qa_and_records_the_failure(
    verdict: GateResult,
) -> None:
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task.status = "verifying"
    c, task_svc = _choreo_for(task)

    outcome = await c.apply_gate_verdict(agent_id, task_id, "done", "tok", verdict)

    assert outcome == "rejected"
    task_svc.submit_qa.assert_not_awaited()
    task_svc.reassign.assert_not_awaited()
    message = task_svc.add_progress.await_args.kwargs["message"]
    assert "quality gate failed before QA" in message
    assert verdict.output_excerpt in message


@pytest.mark.asyncio
async def test_a_verifying_task_with_a_matching_token_submits_without_reverifying() -> (
    None
):
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task.status = "verifying"
    c, task_svc = _choreo_for(task)

    assert await c.apply_gate_verdict(
        agent_id, task_id, "x", "tok", GateResult(True)
    ) == ("submitted")
    task_svc.submit_verification.assert_not_awaited()
    task_svc.submit_qa.assert_awaited_once()


@pytest.mark.asyncio
async def test_gate_fail_callback_on_a_verifying_task_returns_it_to_in_progress() -> (
    None
):
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task.status = "verifying"
    c, task_svc = _choreo_for(task)

    outcome = await c.apply_gate_verdict(
        agent_id, task_id, "x", "tok", GateResult(passed=False, failures=("lint",))
    )

    assert outcome == "rejected"
    task_svc.reopen_after_failed_gate.assert_awaited_once_with(agent_id, task.id)
    task_svc.submit_qa.assert_not_awaited()
    assert "quality gate failed" in task_svc.add_progress.await_args.kwargs["message"]


@pytest.mark.asyncio
async def test_i_am_done_moves_the_task_to_verifying_before_launching_the_gate(
    gate_registry: RecordingGateRegistry,
) -> None:
    """No pausable in_progress window while the gate runs: verification is
    submitted first, and the gate launches without touching QA."""
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.submit_verification.return_value = MagicMock(status="verifying")
    journal = AsyncMock()
    journal.has_reflect_for_task.return_value = True
    journal.has_decision_for_task.return_value = True
    journal.has_learning_for_task.return_value = False
    journal.has_struggle_for_task.return_value = False
    c = Choreographer(_make_deps(task=task_svc, journal=journal))

    env = await c.i_am_done(agent_id, task_id, "done")

    assert env.error is None
    assert env.as_dict()["status"] == "verifying"
    task_svc.submit_verification.assert_awaited_once()
    assert len(gate_registry.launches) == 1
    task_svc.submit_qa.assert_not_awaited()


# --- lost-job (restart) recovery -------------------------------------------

_MIN_SWEEPS = 3


def _verifying_task(*, run: dict[str, Any] | None = None) -> MagicMock:
    markers = {"gate_run": run} if run is not None else None
    return MagicMock(id=uuid4(), assigned_to=uuid4(), orchestration_markers=markers)


def _recovery_env(
    monkeypatch: pytest.MonkeyPatch, tasks: list[MagicMock]
) -> tuple[MagicMock, RecordingGateRegistry, AsyncMock]:
    choreo = MagicMock()
    choreo.task.list_verifying_with_assignee = AsyncMock(return_value=tasks)
    choreo.task.stamp_gate_run = AsyncMock(return_value="fresh-token")
    choreo.apply_gate_verdict = AsyncMock(return_value="submitted")
    db = _patch_session(monkeypatch, choreo)
    registry = RecordingGateRegistry()
    monkeypatch.setattr(
        "roboco.services.gate_jobs.get_gate_job_registry", lambda: registry
    )
    return choreo, registry, db


@pytest.mark.asyncio
async def test_relaunch_lost_gate_jobs_launches_one_job_per_stranded_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stranded = [_verifying_task(), _verifying_task()]
    choreo, registry, db = _recovery_env(monkeypatch, stranded)

    assert await gate_job.relaunch_lost_gate_jobs() == len(stranded)
    assert [launch.task_id for launch in registry.launches] == [t.id for t in stranded]
    db.commit.assert_awaited_once()
    assert choreo.task.stamp_gate_run.await_count == len(stranded)


@pytest.mark.asyncio
async def test_relaunched_job_reuses_the_persisted_notes_and_fresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = {"token": "old", "notes": "dev self-check notes", "started_at": 0.0}
    task = _verifying_task(run=old)
    choreo, registry, _ = _recovery_env(monkeypatch, [task])

    await gate_job.relaunch_lost_gate_jobs()
    choreo.task.stamp_gate_run.assert_awaited_once_with(task.id, "dev self-check notes")
    await registry.launches[0].finish(GateResult(True))

    args = choreo.apply_gate_verdict.await_args.args
    assert args[:4] == (
        task.assigned_to,
        task.id,
        "dev self-check notes",
        "fresh-token",
    )


@pytest.mark.asyncio
async def test_relaunch_skips_a_run_young_enough_to_be_live_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blue-green overlap: the old color's job started moments ago."""
    young = {"token": "t", "notes": "n", "started_at": time.time()}
    choreo, registry, _ = _recovery_env(monkeypatch, [_verifying_task(run=young)])

    assert await gate_job.relaunch_lost_gate_jobs() == 0
    choreo.task.stamp_gate_run.assert_not_awaited()
    assert registry.launches == []


@pytest.mark.asyncio
async def test_relaunch_takes_over_a_run_older_than_the_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = {"token": "t", "notes": "n", "started_at": time.time() - 10_000}
    _, registry, _ = _recovery_env(monkeypatch, [_verifying_task(run=stale)])

    assert await gate_job.relaunch_lost_gate_jobs() == 1
    assert len(registry.launches) == 1


@pytest.mark.asyncio
async def test_young_run_is_skipped_then_relaunched_by_a_later_sweep_once_aged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old blue-green color's run is live at the first sweep, gone later."""
    clock = [1_000.0]
    monkeypatch.setattr(gate_job.time, "time", lambda: clock[0])
    run = {"token": "t", "notes": "n", "started_at": 1_000.0}
    task = _verifying_task(run=run)
    choreo, registry, _ = _recovery_env(monkeypatch, [task])

    assert await gate_job.relaunch_lost_gate_jobs() == 0
    choreo.task.stamp_gate_run.assert_not_awaited()

    clock[0] += 10_000
    assert await gate_job.relaunch_lost_gate_jobs() == 1
    assert [launch.task_id for launch in registry.launches] == [task.id]


@pytest.mark.asyncio
async def test_recovery_loop_sweeps_repeatedly_and_stops_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweeps = 0

    async def fake_sweep() -> int:
        nonlocal sweeps
        sweeps += 1
        if sweeps == 1:
            raise RuntimeError("transient")
        return 0

    monkeypatch.setattr(gate_job, "relaunch_lost_gate_jobs", fake_sweep)
    loop_task = asyncio.create_task(gate_job.run_gate_recovery_loop(0.01))
    while sweeps < _MIN_SWEEPS:
        await asyncio.sleep(0.01)

    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task
    assert loop_task.cancelled()


@pytest.mark.asyncio
async def test_relaunch_skips_tasks_that_already_have_a_live_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choreo, registry, _ = _recovery_env(monkeypatch, [_verifying_task()])
    registry.running = True

    assert await gate_job.relaunch_lost_gate_jobs() == 0
    choreo.task.stamp_gate_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_red_verdict_then_a_stale_pass_leaves_the_task_in_progress() -> None:
    """Duplicate jobs (blue-green): the red run reopens the task and consumes
    its token; the second job's late pass finds a non-verifying task."""
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task.status = "verifying"
    c, task_svc = _choreo_for(task)

    async def reopen(_agent: Any, _task: Any) -> None:
        task.status = "in_progress"

    task_svc.reopen_after_failed_gate.side_effect = reopen

    red = GateResult(passed=False, failures=("lint",))
    assert await c.apply_gate_verdict(agent_id, task_id, "x", "t1", red) == "rejected"
    stale = await c.apply_gate_verdict(agent_id, task_id, "x", "t2", GateResult(True))

    assert stale == "skipped"
    assert task.status == "in_progress"
    task_svc.submit_qa.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_verdict_with_a_mismatched_token_is_skipped() -> None:
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task.status = "verifying"
    c, task_svc = _choreo_for(task)
    task_svc.consume_gate_run.return_value = False

    outcome = await c.apply_gate_verdict(
        agent_id, task_id, "x", "stale", GateResult(True)
    )

    assert outcome == "skipped"
    task_svc.submit_qa.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_am_done_stamps_a_gate_run_carrying_the_submit_notes(
    gate_registry: RecordingGateRegistry,
) -> None:
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.submit_verification.return_value = MagicMock(status="verifying")
    task_svc.stamp_gate_run.return_value = "tok-1"
    journal = AsyncMock()
    journal.has_reflect_for_task.return_value = True
    journal.has_decision_for_task.return_value = True
    journal.has_learning_for_task.return_value = False
    journal.has_struggle_for_task.return_value = False
    c = Choreographer(_make_deps(task=task_svc, journal=journal))

    await c.i_am_done(agent_id, task_id, "my notes")

    task_svc.stamp_gate_run.assert_awaited_once_with(task_id, "my notes")
    assert len(gate_registry.launches) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda t, _agent: setattr(t, "status", "awaiting_qa"),
        lambda t, _agent: setattr(t, "assigned_to", uuid4()),
        lambda t, _agent: setattr(t, "status", "cancelled"),
        lambda t, _agent: setattr(t, "status", "in_progress"),
        lambda t, _agent: setattr(t, "status", "paused"),
    ],
    ids=["already-in-qa", "reassigned", "cancelled", "in-progress", "paused"],
)
async def test_duplicate_or_stale_callback_is_a_no_op(mutate: Any) -> None:
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    mutate(task, agent_id)
    c, task_svc = _choreo_for(task)

    outcome = await c.apply_gate_verdict(
        agent_id, task_id, "x", "tok", GateResult(True)
    )

    assert outcome == "skipped"
    task_svc.submit_qa.assert_not_awaited()
    task_svc.add_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_for_a_missing_task_is_a_no_op() -> None:
    c, task_svc = _choreo_for(MagicMock())
    task_svc.get.return_value = None

    assert await c.apply_gate_verdict(
        uuid4(), uuid4(), "x", "tok", GateResult(True)
    ) == ("skipped")


@pytest.mark.asyncio
async def test_duplicate_callback_after_a_real_submit_is_skipped() -> None:
    """First callback submits; the task then reads as in QA, second is a no-op."""
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task.status = "verifying"
    c, task_svc = _choreo_for(task)

    assert await c.apply_gate_verdict(
        agent_id, task_id, "x", "tok", GateResult(True)
    ) == ("submitted")
    task.status, task.assigned_to = "awaiting_qa", None
    assert await c.apply_gate_verdict(
        agent_id, task_id, "x", "tok", GateResult(True)
    ) == ("skipped")
    task_svc.submit_qa.assert_awaited_once()


# --- i_am_done returns promptly --------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_while_a_job_runs_reports_it_and_launches_nothing(
    gate_registry: RecordingGateRegistry,
) -> None:
    agent_id, task_id = uuid4(), uuid4()
    task = _ready_task(task_id, agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    journal = AsyncMock()
    journal.has_reflect_for_task.return_value = True
    journal.has_decision_for_task.return_value = True
    journal.has_learning_for_task.return_value = False
    journal.has_struggle_for_task.return_value = False
    c = Choreographer(_make_deps(task=task_svc, journal=journal))
    gate_registry.running = True

    env = await c.i_am_done(agent_id, task_id, "done")

    assert env.error is None
    assert "already running" in env.as_dict()["next"]
    assert gate_registry.launches == []
    task_svc.submit_qa.assert_not_awaited()


# --- job bodies ------------------------------------------------------------


def _patch_session(monkeypatch: pytest.MonkeyPatch, choreo: Any) -> AsyncMock:
    db = AsyncMock()

    @asynccontextmanager
    async def session() -> Any:
        yield db

    monkeypatch.setattr(gate_job, "get_session_factory", lambda: session)
    monkeypatch.setattr(
        "roboco.api.deps.get_choreographer", AsyncMock(return_value=choreo)
    )
    return db


@pytest.mark.asyncio
async def test_run_gate_job_returns_the_gate_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choreo = MagicMock()
    choreo.task.get = AsyncMock(return_value=MagicMock())
    choreo.git.run_pre_submit_quality_gate = AsyncMock(
        return_value=GateResult(passed=False, failures=("lint",))
    )
    _patch_session(monkeypatch, choreo)

    result = await gate_job.run_gate_job(uuid4(), uuid4())

    assert not result.passed


@pytest.mark.asyncio
async def test_run_gate_job_is_fail_open_on_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choreo = MagicMock()
    choreo.task.get = AsyncMock(return_value=MagicMock())
    choreo.git.run_pre_submit_quality_gate = AsyncMock(
        side_effect=RuntimeError("workspace not found")
    )
    _patch_session(monkeypatch, choreo)

    assert (await gate_job.run_gate_job(uuid4(), uuid4())).passed


@pytest.mark.asyncio
async def test_run_gate_job_for_a_vanished_task_passes_without_running_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choreo = MagicMock()
    choreo.task.get = AsyncMock(return_value=None)
    choreo.git.run_pre_submit_quality_gate = AsyncMock()
    _patch_session(monkeypatch, choreo)

    assert (await gate_job.run_gate_job(uuid4(), uuid4())).passed
    choreo.git.run_pre_submit_quality_gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_finish_gate_job_applies_the_verdict_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choreo = MagicMock()
    choreo.apply_gate_verdict = AsyncMock(return_value="submitted")
    db = _patch_session(monkeypatch, choreo)
    agent_id, task_id = uuid4(), uuid4()

    outcome = await gate_job.finish_gate_job(
        agent_id, task_id, "n", "tok", GateResult(True)
    )

    assert outcome == "submitted"
    db.commit.assert_awaited_once()


# --- gate-run marker (TaskService) -----------------------------------------


def _marker_service(monkeypatch: pytest.MonkeyPatch, task: Any) -> Any:
    svc = TaskService.__new__(TaskService)
    monkeypatch.setattr(svc, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(svc, "session", AsyncMock(), raising=False)
    return svc


@pytest.mark.asyncio
async def test_stamp_then_consume_is_one_shot_and_token_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = MagicMock(orchestration_markers=None)
    svc = _marker_service(monkeypatch, task)

    token = await svc.stamp_gate_run(uuid4(), "notes")

    assert task.orchestration_markers["gate_run"]["notes"] == "notes"
    assert not await svc.consume_gate_run(uuid4(), "wrong")
    assert await svc.consume_gate_run(uuid4(), token)
    assert not await svc.consume_gate_run(uuid4(), token)
