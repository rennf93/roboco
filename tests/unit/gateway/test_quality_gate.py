"""Pre-submit quality gate: lint + typecheck run in the dev's workspace on
i_am_done, blocking a red submit before it reaches QA. Full tests stay on CI.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer._impl import _IAmDoneContext
from roboco.services.gateway.quality_gate import GateResult, run_quality_commands
from roboco.services.git import GitService

# --- the runner (real subprocess) -------------------------------------------


@pytest.mark.asyncio
async def test_no_commands_is_a_skipped_pass(tmp_path) -> None:
    result = await run_quality_commands(tmp_path, [])
    assert result.passed is True
    assert result.skipped is True


@pytest.mark.asyncio
async def test_all_commands_pass(tmp_path) -> None:
    result = await run_quality_commands(
        tmp_path, [("lint", "echo lint-ok"), ("typecheck", "true")]
    )
    assert result.passed is True
    assert result.failures == ()


@pytest.mark.asyncio
async def test_a_failing_command_blocks_and_is_named(tmp_path) -> None:
    result = await run_quality_commands(
        tmp_path, [("lint", "echo problem-here; exit 1"), ("typecheck", "true")]
    )
    assert result.passed is False
    assert "lint" in result.failures
    assert "problem-here" in result.output


@pytest.mark.asyncio
async def test_every_command_runs_even_after_a_failure(tmp_path) -> None:
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
