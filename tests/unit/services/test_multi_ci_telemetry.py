"""MultiProjectCITelemetrySource fans out the hardened per-project CI lookup.

One red project yields a breaching sample; a green one a non-breaching sample; a
None signal or a per-project error yields NO sample (unknown, never "green") and
never aborts the sweep. Each project's ci_watch_workflow (or the configured
default) is passed through to the reused lookup.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.services.telemetry.source import MultiProjectCITelemetrySource


def _project(slug: str, workflow: str | None = None) -> MagicMock:
    return MagicMock(slug=slug, ci_watch_workflow=workflow)


def _ci(conclusion: str) -> dict[str, Any]:
    return {
        "conclusion": conclusion,
        "branch": "master",
        "run_url": f"https://github.com/x/{conclusion}/actions/runs/1",
        "completed_at": "2026-06-25T00:00:00Z",
        "run_name": "CI",
    }


@pytest.mark.asyncio
async def test_fanout_red_green_and_none() -> None:
    projects: list[object] = [_project("red"), _project("green"), _project("nosig")]

    async def conclusion(slug: str, **_kwargs: Any) -> Any:
        return {"red": _ci("failure"), "green": _ci("success"), "nosig": None}[slug]

    git = MagicMock()
    git.get_latest_ci_conclusion = AsyncMock(side_effect=conclusion)
    with patch("roboco.services.telemetry.source.GitService", return_value=git):
        samples = await MultiProjectCITelemetrySource(MagicMock()).fetch(projects)

    by_repo = {s.repo_hint: s for s in samples}
    assert by_repo["red"].is_breach is True
    assert by_repo["green"].is_breach is False
    assert "nosig" not in by_repo  # None signal → no sample (unknown, not green)


@pytest.mark.asyncio
async def test_per_project_error_isolated() -> None:
    projects: list[object] = [_project("boom"), _project("ok")]

    async def conclusion(slug: str, **_kwargs: Any) -> Any:
        if slug == "boom":
            raise RuntimeError("github down")
        return _ci("failure")

    git = MagicMock()
    git.get_latest_ci_conclusion = AsyncMock(side_effect=conclusion)
    with patch("roboco.services.telemetry.source.GitService", return_value=git):
        samples = await MultiProjectCITelemetrySource(MagicMock()).fetch(projects)

    by_repo = {s.repo_hint: s for s in samples}
    assert "boom" not in by_repo  # error → no sample, never aborts the sweep
    assert by_repo["ok"].is_breach is True  # others still returned


@pytest.mark.asyncio
async def test_per_project_workflow_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ci_watch_default_workflow", "ci.yml")
    projects: list[object] = [
        _project("custom", workflow="release.yml"),
        _project("default"),
    ]
    git = MagicMock()
    git.get_latest_ci_conclusion = AsyncMock(return_value=_ci("success"))
    with patch("roboco.services.telemetry.source.GitService", return_value=git):
        await MultiProjectCITelemetrySource(MagicMock()).fetch(projects)

    workflows = {
        c.args[0]: c.kwargs["workflow"]
        for c in git.get_latest_ci_conclusion.await_args_list
    }
    assert workflows["custom"] == "release.yml"
    assert workflows["default"] == "ci.yml"


@pytest.mark.asyncio
async def test_fetch_gathers_concurrently_not_sequentially() -> None:
    # Sequential would be ~a_delay + b_delay; gathered is ~max(a, b).
    a_delay, b_delay = 0.15, 0.20
    projects: list[object] = [_project("a"), _project("b")]

    async def slow_sample(
        _self: Any, _git: Any, slug: str, _workflow: str | None
    ) -> Any:
        delay = a_delay if slug == "a" else b_delay
        await asyncio.sleep(delay)
        return None

    with patch.object(MultiProjectCITelemetrySource, "_sample_for", slow_sample):
        start = time.monotonic()
        await MultiProjectCITelemetrySource(MagicMock()).fetch(projects)
        elapsed = time.monotonic() - start

    # Sequential ≈ 0.35s; gathered ≈ 0.20s. 0.30s splits the two cleanly.
    assert elapsed < a_delay + b_delay - 0.05
