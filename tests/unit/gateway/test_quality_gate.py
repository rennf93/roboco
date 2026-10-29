"""Pre-submit quality gate: lint + typecheck run in the dev's workspace on
i_am_done, blocking a red submit before it reaches QA. Full tests stay on CI.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    import pathlib
from uuid import uuid4

import pytest
from roboco.services.gateway import quality_gate
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer._impl import _IAmDoneContext
from roboco.services.gateway.quality_gate import GateResult, run_quality_commands
from roboco.services.git import GitService
from roboco.services.workspace_container import WorkspaceContainerResult
from tests.unit.local_workspace_container import run_in_local_shell

# --- the runner -------------------------------------------------------------
# Commands run in a throwaway container (roboco.services.workspace_container,
# tested on its own). The gate's own logic - aggregation, naming, output - is
# exercised with real commands in a local shell standing in for the container.


@pytest.fixture
def _local_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quality_gate, "run_in_workspace_container", run_in_local_shell)


@pytest.mark.asyncio
async def test_no_commands_is_a_skipped_pass(tmp_path: pathlib.Path) -> None:
    result = await run_quality_commands(tmp_path, [])
    assert result.passed is True
    assert result.skipped is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("_local_container")
async def test_all_commands_pass(tmp_path: pathlib.Path) -> None:
    result = await run_quality_commands(
        tmp_path, [("lint", "echo lint-ok"), ("typecheck", "true")]
    )
    assert result.passed is True
    assert result.failures == ()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_local_container")
async def test_a_failing_command_blocks_and_is_named(tmp_path: pathlib.Path) -> None:
    result = await run_quality_commands(
        tmp_path, [("lint", "echo problem-here; exit 1"), ("typecheck", "true")]
    )
    assert result.passed is False
    assert "lint" in result.failures
    assert "problem-here" in result.output


@pytest.mark.asyncio
@pytest.mark.usefixtures("_local_container")
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


_TIMEOUT_EXIT_CODE = 124


@pytest.mark.asyncio
async def test_run_one_runs_the_command_in_the_workspace_container(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate command executes the clone's own Makefile/tooling, which any
    agent can rewrite: it must never run in the orchestrator (root)."""
    runner = AsyncMock(return_value=WorkspaceContainerResult(0, "ok\n", ""))
    monkeypatch.setattr(quality_gate, "run_in_workspace_container", runner)
    assert not hasattr(quality_gate, "asyncio"), "no local subprocess path left"

    rc, out = await quality_gate._run_one(tmp_path, "make gate")

    assert (rc, out) == (0, "ok\n")
    runner.assert_awaited_once_with(
        tmp_path,
        ["sh", "-c", "make gate"],
        timeout=quality_gate._GATE_TIMEOUT_SECONDS,
        merge_stderr=True,
    )


@pytest.mark.asyncio
async def test_run_one_reports_a_timeout_as_exit_124(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        quality_gate,
        "run_in_workspace_container",
        AsyncMock(
            return_value=WorkspaceContainerResult(
                _TIMEOUT_EXIT_CODE, "", "timed out", timed_out=True
            )
        ),
    )
    rc, msg = await quality_gate._run_one(tmp_path, "slow-command")
    assert rc == _TIMEOUT_EXIT_CODE
    assert "timed out" in msg


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
