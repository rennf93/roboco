"""MultiProjectCITelemetrySource fans out the hardened per-project CI lookup.

One red project yields a breaching sample; a green one a non-breaching sample; a
None signal or a per-project error yields NO sample (unknown, never "green") and
never aborts the sweep. Each project's ci_watch_workflow (or the configured
default) is passed through to the reused lookup. The per-project DB reads run
one at a time on the shared session; only the GitHub calls run concurrently
(2026-10-28: gathering the DB reads failed every sweep with "concurrent
operations are not permitted").
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.services.git import GitService
from roboco.services.telemetry.source import MultiProjectCITelemetrySource
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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


def _git(conclusion_for_slug: Any) -> MagicMock:
    """A GitService double: the query is the slug, the IO half answers per slug."""
    git = MagicMock()
    git.resolve_ci_query = AsyncMock(side_effect=lambda slug, **_kw: slug)
    git.get_latest_ci_conclusion_for = AsyncMock(
        side_effect=lambda query, **_kw: conclusion_for_slug(query)
    )
    return git


@pytest.mark.asyncio
async def test_fanout_red_green_and_none() -> None:
    projects: list[object] = [_project("red"), _project("green"), _project("nosig")]
    answers = {"red": _ci("failure"), "green": _ci("success"), "nosig": None}
    git = _git(answers.__getitem__)
    with patch("roboco.services.telemetry.source.GitService", return_value=git):
        samples = await MultiProjectCITelemetrySource(MagicMock()).fetch(projects)

    by_repo = {s.repo_hint: s for s in samples}
    assert by_repo["red"].is_breach is True
    assert by_repo["green"].is_breach is False
    assert "nosig" not in by_repo  # None signal → no sample (unknown, not green)


@pytest.mark.asyncio
async def test_unresolvable_project_yields_no_sample() -> None:
    projects: list[object] = [_project("no-token"), _project("ok")]
    git = _git(lambda _query: _ci("failure"))
    git.resolve_ci_query = AsyncMock(
        side_effect=lambda slug, **_kw: None if slug == "no-token" else slug
    )
    with patch("roboco.services.telemetry.source.GitService", return_value=git):
        samples = await MultiProjectCITelemetrySource(MagicMock()).fetch(projects)

    assert [s.repo_hint for s in samples] == ["ok"]


@pytest.mark.asyncio
async def test_per_project_error_isolated() -> None:
    projects: list[object] = [_project("boom"), _project("ok")]

    def conclusion(slug: str) -> Any:
        if slug == "boom":
            raise RuntimeError("github down")
        return _ci("failure")

    git = _git(conclusion)
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
    git = _git(lambda _query: _ci("success"))
    with patch("roboco.services.telemetry.source.GitService", return_value=git):
        await MultiProjectCITelemetrySource(MagicMock()).fetch(projects)

    workflows = {
        c.args[0]: c.kwargs["workflow"]
        for c in git.get_latest_ci_conclusion_for.await_args_list
    }
    assert workflows["custom"] == "release.yml"
    assert workflows["default"] == "ci.yml"


@pytest.mark.asyncio
async def test_github_calls_run_concurrently_not_sequentially() -> None:
    # Sequential would be ~a_delay + b_delay; gathered is ~max(a, b).
    a_delay, b_delay = 0.15, 0.20
    projects: list[object] = [_project("a"), _project("b")]

    async def slow_conclusion(query: str, **_kw: Any) -> Any:
        await asyncio.sleep(a_delay if query == "a" else b_delay)
        return None

    git = _git(lambda _query: None)
    git.get_latest_ci_conclusion_for = AsyncMock(side_effect=slow_conclusion)
    with patch("roboco.services.telemetry.source.GitService", return_value=git):
        start = time.monotonic()
        await MultiProjectCITelemetrySource(MagicMock()).fetch(projects)
        elapsed = time.monotonic() - start

    # Sequential ≈ 0.35s; gathered ≈ 0.20s. 0.30s splits the two cleanly.
    assert elapsed < a_delay + b_delay - 0.05


@pytest.mark.asyncio
async def test_db_reads_never_overlap_on_the_shared_session(
    db_session: AsyncSession,
) -> None:
    """The real failure: two projects' DB reads on one AsyncSession at once
    raise "concurrent operations are not permitted", and the sweep yields no
    sample at all."""

    async def resolve_with_a_real_query(self: GitService, slug: str, **_kw: Any) -> str:
        await self.session.execute(text("SELECT pg_sleep(0.05)"))
        return slug

    projects: list[object] = [_project("roboco-api"), _project("roboco-panel")]
    with (
        patch.object(GitService, "resolve_ci_query", resolve_with_a_real_query),
        patch.object(
            GitService,
            "get_latest_ci_conclusion_for",
            AsyncMock(return_value=_ci("failure")),
        ),
    ):
        samples = await MultiProjectCITelemetrySource(db_session).fetch(projects)

    assert sorted(s.repo_hint for s in samples) == ["roboco-api", "roboco-panel"]
