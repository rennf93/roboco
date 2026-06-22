"""ConventionsService: cache-by-SHA, fallback, baseline/ambient, scaffold/restore."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.services.conventions import get_conventions_service

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from sqlalchemy.ext.asyncio import AsyncSession

_AMBIENT_CAP = 1200
_FAKE_PR_NUMBER = 7


class _FakeGit:
    """Captures the scaffold/restore publish call instead of hitting git."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def open_conventions_pr(
        self, project_slug: str, *, content: str, **_kwargs: object
    ) -> dict[str, Any]:
        self.calls.append({"slug": project_slug, "content": content})
        return {
            "branch": "chore/roboco-conventions-scaffold",
            "pr_number": _FAKE_PR_NUMBER,
            "pr_url": "u",
        }


async def _seed_project(
    db: AsyncSession, *, head_commit: str, workspace_path: str
) -> ProjectTable:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db.add(agent)
    await db.flush()
    project = ProjectTable(
        id=uuid4(),
        name="C-Proj",
        slug=f"c-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
        head_commit=head_commit,
        workspace_path=workspace_path,
    )
    db.add(project)
    await db.flush()
    return project


async def test_get_map_caches_per_head_sha(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    project = await _seed_project(
        db_session, head_commit="sha1", workspace_path=str(tmp_path)
    )
    svc = get_conventions_service(db_session)
    first = await svc.get_map(project)
    # Mutate the workspace AFTER the first call — a cache hit must ignore it.
    (tmp_path / "app" / "routers").mkdir(parents=True)
    second = await svc.get_map(project)
    assert second == first
    assert [m.path for m in second.modules] == []


async def test_missing_file_yields_missing_status_and_derived_map(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    (tmp_path / "app" / "routers").mkdir(parents=True)
    project = await _seed_project(
        db_session, head_commit="s", workspace_path=str(tmp_path)
    )
    svc = get_conventions_service(db_session)
    mapping = await svc.get_map(project)
    assert any(m.path == "app/routers" for m in mapping.modules)
    health = await svc.health(project)
    assert health.status == "missing"


async def test_corrupt_file_falls_back_to_last_ok(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    project = await _seed_project(
        db_session, head_commit="ok1", workspace_path=str(tmp_path)
    )
    conv = tmp_path / ".roboco"
    conv.mkdir()
    (conv / "conventions.yml").write_text(
        "modules:\n  - path: lib/special\n    purpose: special things\n"
    )
    svc = get_conventions_service(db_session)
    ok_map = await svc.get_map(project)
    assert any(m.path == "lib/special" for m in ok_map.modules)

    project.head_commit = "bad1"
    await db_session.flush()
    (conv / "conventions.yml").write_text("modules: [unterminated\n")
    degraded = await svc.get_map(project)
    assert any(m.path == "lib/special" for m in degraded.modules)

    health = await svc.health(project)
    assert health.status == "degraded"
    assert health.last_ok_sha == "ok1"


async def test_baseline_constraints_include_block_rules(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    project = await _seed_project(
        db_session, head_commit="s", workspace_path=str(tmp_path)
    )
    constraints = await get_conventions_service(db_session).baseline_constraints(
        project
    )
    assert any("no models in routers" in c for c in constraints)


async def test_render_ambient_block_is_bounded(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    project = await _seed_project(
        db_session, head_commit="s", workspace_path=str(tmp_path)
    )
    block = await get_conventions_service(db_session).render_ambient_block(project)
    assert block.startswith("## Architectural Standard")
    assert len(block) <= _AMBIENT_CAP


async def test_scaffold_opens_pr_with_rendered_map(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app" / "routers").mkdir(parents=True)
    project = await _seed_project(
        db_session, head_commit="s", workspace_path=str(tmp_path)
    )
    fake = _FakeGit()
    monkeypatch.setattr(
        "roboco.services.conventions.get_git_service", lambda _session: fake
    )
    result = await get_conventions_service(db_session).scaffold(project)
    assert result.created is True
    assert result.pr_number == _FAKE_PR_NUMBER
    assert fake.calls and "app/routers" in fake.calls[0]["content"]


async def test_restore_uses_last_good_map(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _seed_project(
        db_session, head_commit="ok1", workspace_path=str(tmp_path)
    )
    conv = tmp_path / ".roboco"
    conv.mkdir()
    (conv / "conventions.yml").write_text(
        "modules:\n  - path: lib/special\n    purpose: special\n"
    )
    svc = get_conventions_service(db_session)
    await svc.get_map(project)  # caches an 'ok' row containing lib/special

    fake = _FakeGit()
    monkeypatch.setattr(
        "roboco.services.conventions.get_git_service", lambda _session: fake
    )
    result = await svc.restore(project)
    assert result.created is True
    assert "lib/special" in fake.calls[0]["content"]
