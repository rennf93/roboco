"""Pre-submit quality gate: lint + typecheck run in the dev's workspace on
i_am_done, blocking a red submit before it reaches QA. Full tests stay on CI.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    import pathlib
from uuid import uuid4

import pytest
from roboco.services.gateway import quality_gate
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer._impl import _IAmDoneContext
from roboco.services.gateway.quality_gate import GateResult, run_quality_commands
from roboco.services.git import GitService

# --- the runner (real subprocess) -------------------------------------------


@pytest.mark.asyncio
async def test_no_commands_is_a_skipped_pass(tmp_path: pathlib.Path) -> None:
    result = await run_quality_commands(tmp_path, [])
    assert result.passed is True
    assert result.skipped is True


@pytest.mark.asyncio
async def test_all_commands_pass(tmp_path: pathlib.Path) -> None:
    result = await run_quality_commands(
        tmp_path, [("lint", "echo lint-ok"), ("typecheck", "true")]
    )
    assert result.passed is True
    assert result.failures == ()


@pytest.mark.asyncio
async def test_a_failing_command_blocks_and_is_named(tmp_path: pathlib.Path) -> None:
    result = await run_quality_commands(
        tmp_path, [("lint", "echo problem-here; exit 1"), ("typecheck", "true")]
    )
    assert result.passed is False
    assert "lint" in result.failures
    assert "problem-here" in result.output


@pytest.mark.asyncio
async def test_every_command_runs_even_after_a_failure(tmp_path: pathlib.Path) -> None:
    result = await run_quality_commands(
        tmp_path, [("lint", "echo AAA; exit 1"), ("typecheck", "echo BBB; exit 2")]
    )
    assert result.passed is False
    assert set(result.failures) == {"lint", "typecheck"}
    assert "AAA" in result.output
    assert "BBB" in result.output


def test_gate_result_summary_and_excerpt() -> None:
    assert GateResult(passed=True).summary == "all checks passed"
    assert GateResult(passed=True, skipped=True).summary.startswith("no quality")
    failed = GateResult(passed=False, failures=("lint",), output="x" * 5000)
    assert "lint" in failed.summary
    # The excerpt is the truncated tail, shorter than the full output.
    assert 0 < len(failed.output_excerpt) < len(failed.output)


# --- _run_one None-returncode fail-closed -----------------------------------

_TIMEOUT_EXIT_CODE = 124


@pytest.mark.asyncio
async def test_run_one_treats_none_returncode_as_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A None returncode (process terminated without a recorded exit code, e.g.
    killed out-of-band during communicate) must NOT be masked as 0 / success.
    The gate is fail-closed — an unknown exit status is a failure, not a pass.
    """
    fake_proc = MagicMock()
    fake_proc.returncode = None  # abnormal: communicate returned, no code set
    fake_proc.communicate = AsyncMock(return_value=(b"some output", b""))

    async def _fake_shell(*_args: object, **_kwargs: object) -> object:
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_shell)

    rc, out = await quality_gate._run_one(tmp_path, "anything")
    assert rc != 0, "a None returncode masked as 0 lets a red gate pass"
    assert "some output" in out


@pytest.mark.asyncio
async def test_run_one_reaps_killed_timeout_process(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On timeout, _run_one must kill AND await wait() to reap the killed
    process and close the stdout/stderr pipe transports. kill alone leaves a
    transient zombie + leaked FDs (communicate() was cancelled, so it never
    closed the pipes)."""
    fake_proc = MagicMock()
    fake_proc.returncode = -9  # killed by SIGKILL

    # communicate() never completes on its own — wait_for cancels it.
    async def _communicate() -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return (b"", b"")

    fake_proc.communicate = _communicate
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock(return_value=-9)

    async def _fake_shell(*_args: object, **_kwargs: object) -> object:
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_shell)
    monkeypatch.setattr(quality_gate, "_GATE_TIMEOUT_SECONDS", 0.01)

    rc, _msg = await quality_gate._run_one(tmp_path, "slow-command")
    assert rc == _TIMEOUT_EXIT_CODE
    fake_proc.kill.assert_called_once()
    fake_proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_one_kills_child_on_outer_cancellation(
    tmp_path: pathlib.Path,
) -> None:
    """An outer cancellation (e.g. FlowVerbTimeoutMiddleware's own
    asyncio.timeout firing around the whole i_am_done submit) throws
    CancelledError into the wait_for, bypassing the TimeoutError handler
    above. Without a dedicated handler the child is orphaned and keeps
    running past the cancelled request; _run_one must kill + reap it and
    re-raise.
    """
    real_create_subprocess_shell = asyncio.create_subprocess_shell
    spawned: dict[str, asyncio.subprocess.Process] = {}

    async def _capturing_create(*args: Any, **kwargs: Any) -> Any:
        proc = await real_create_subprocess_shell(*args, **kwargs)
        spawned["proc"] = proc
        return proc

    with patch.object(asyncio, "create_subprocess_shell", _capturing_create):
        task = asyncio.ensure_future(quality_gate._run_one(tmp_path, "sleep 30"))
        while "proc" not in spawned:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)  # let the shell actually exec sleep
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    proc = spawned["proc"]
    assert proc.returncode is not None, "child was not reaped after cancellation"
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)


# --- GitService command selection -------------------------------------------


def test_fast_gate_commands_picks_lint_and_typecheck() -> None:
    project = SimpleNamespace(
        quality_command=None,  # not set → fall back to lint + typecheck
        lint_command="uv run ruff check .",
        typecheck_command="uv run mypy roboco/",
        format_command="uv run ruff format .",  # excluded (mutating)
        test_command="uv run pytest",  # excluded (slow; CI only)
    )
    commands = GitService._fast_gate_commands(project)
    assert commands == [
        ("lint", "uv run ruff check ."),
        ("typecheck", "uv run mypy roboco/"),
    ]


def test_quality_command_takes_precedence_and_runs_alone() -> None:
    """A configured quality_command is the complete fast gate (e.g. it also runs
    complexity/xenon) — it runs alone, not alongside lint/typecheck."""
    project = SimpleNamespace(
        quality_command="make gate",
        lint_command="uv run ruff check .",
        typecheck_command="uv run mypy roboco/",
    )
    assert GitService._fast_gate_commands(project) == [("quality", "make gate")]


def test_fast_gate_commands_empty_when_unconfigured() -> None:
    project = SimpleNamespace(
        quality_command=None, lint_command=None, typecheck_command=None
    )
    assert GitService._fast_gate_commands(project) == []


# --- choreographer glue -----------------------------------------------------


def _choreo(mock_git: MagicMock) -> Choreographer:
    return Choreographer(
        ChoreographerDeps(
            task=MagicMock(),
            work_session=MagicMock(),
            git=mock_git,
            a2a=MagicMock(),
            journal=MagicMock(),
            audit=MagicMock(),
            evidence_repo=MagicMock(),
        )
    )


def _ctx() -> _IAmDoneContext:
    return _IAmDoneContext(
        agent_id=uuid4(),
        task_id=uuid4(),
        task=MagicMock(),
        role_str="developer",
        briefing={},
        notes="self-verification summary",
    )


@pytest.mark.asyncio
async def test_gate_pass_does_not_block() -> None:
    git = MagicMock()
    git.run_pre_submit_quality_gate = AsyncMock(return_value=GateResult(passed=True))
    assert await _choreo(git)._check_quality_gate(_ctx()) is None


@pytest.mark.asyncio
async def test_gate_failure_blocks_with_output_in_remediate() -> None:
    git = MagicMock()
    git.run_pre_submit_quality_gate = AsyncMock(
        return_value=GateResult(
            passed=False, failures=("lint",), output="roboco/x.py:1 E501 line too long"
        )
    )
    env = await _choreo(git)._check_quality_gate(_ctx())
    assert env is not None
    assert env.error == "invalid_state"
    assert "E501" in (env.remediate or "")


@pytest.mark.asyncio
async def test_gate_is_fail_open_on_infrastructure_error() -> None:
    """A missing workspace / toolchain must never block a submit."""
    git = MagicMock()
    git.run_pre_submit_quality_gate = AsyncMock(
        side_effect=RuntimeError("workspace not found")
    )
    assert await _choreo(git)._check_quality_gate(_ctx()) is None
