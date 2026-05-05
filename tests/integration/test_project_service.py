"""ProjectService coverage — register/list/update/delete + token round-trip.

Driven by the real Postgres ``db_session`` fixture so the test exercises
the same SQLAlchemy paths the production code does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.project import ProjectCreate, ProjectUpdate
from roboco.services.base import ConflictError, NotFoundError
from roboco.services.project import ProjectService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def project_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    """Seed a system agent so created_by FK is satisfied."""
    system = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(system)
    await db_session.flush()
    svc = ProjectService(db_session)
    yield {"svc": svc, "creator_id": system.id}


def _project_payload(slug_suffix: str) -> ProjectCreate:
    return ProjectCreate(
        name=f"Project {slug_suffix}",
        slug=f"proj-{slug_suffix}",
        git_url=f"https://github.com/example/{slug_suffix}.git",
        assigned_cell=Team.BACKEND,
    )


@pytest.mark.asyncio
async def test_create_project(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    assert project.id is not None


@pytest.mark.asyncio
async def test_create_project_with_git_token_encrypts(project_setup: dict) -> None:
    svc = project_setup["svc"]
    payload = _project_payload(uuid4().hex[:6])
    payload_dict = payload.model_dump()
    payload_dict["git_token"] = "ghp_test_token"
    project = await svc.create(
        ProjectCreate(**payload_dict), project_setup["creator_id"]
    )
    assert project.git_token_encrypted is not None
    assert project.git_token_encrypted != "ghp_test_token"


@pytest.mark.asyncio
async def test_create_project_duplicate_slug_raises(project_setup: dict) -> None:
    svc = project_setup["svc"]
    payload = _project_payload(uuid4().hex[:6])
    await svc.create(payload, project_setup["creator_id"])
    with pytest.raises(ConflictError):
        await svc.create(payload, project_setup["creator_id"])


@pytest.mark.asyncio
async def test_get_returns_project(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    fetched = await svc.get(project.id)
    assert fetched is not None
    assert fetched.id == project.id


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(project_setup: dict) -> None:
    svc = project_setup["svc"]
    assert await svc.get(uuid4()) is None


@pytest.mark.asyncio
async def test_get_by_slug(project_setup: dict) -> None:
    svc = project_setup["svc"]
    payload = _project_payload(uuid4().hex[:6])
    created = await svc.create(payload, project_setup["creator_id"])
    fetched = await svc.get_by_slug(payload.slug)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_or_raise_raises(project_setup: dict) -> None:
    svc = project_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.get_or_raise(uuid4())


@pytest.mark.asyncio
async def test_update_changes_name(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    new_name = f"renamed-{uuid4().hex[:6]}"
    updated = await svc.update(project.id, ProjectUpdate(name=new_name))
    assert updated is not None
    assert updated.name == new_name


@pytest.mark.asyncio
async def test_update_clear_git_token(project_setup: dict) -> None:
    svc = project_setup["svc"]
    payload = _project_payload(uuid4().hex[:6])
    pd = payload.model_dump()
    pd["git_token"] = "ghp_initial"
    project = await svc.create(ProjectCreate(**pd), project_setup["creator_id"])
    assert project.git_token_encrypted is not None
    updated = await svc.update(project.id, ProjectUpdate(git_token=""))
    assert updated is not None
    assert updated.git_token_encrypted is None


@pytest.mark.asyncio
async def test_update_set_git_token(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    updated = await svc.update(project.id, ProjectUpdate(git_token="ghp_new"))
    assert updated is not None
    assert updated.git_token_encrypted is not None


@pytest.mark.asyncio
async def test_update_returns_none_for_missing(project_setup: dict) -> None:
    svc = project_setup["svc"]
    assert (await svc.update(uuid4(), ProjectUpdate(name="ghost"))) is None


@pytest.mark.asyncio
async def test_delete_project(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    await svc.delete(project.id)
    assert await svc.get(project.id) is None


@pytest.mark.asyncio
async def test_list_all(project_setup: dict) -> None:
    svc = project_setup["svc"]
    a = await svc.create(_project_payload(uuid4().hex[:6]), project_setup["creator_id"])
    b = await svc.create(_project_payload(uuid4().hex[:6]), project_setup["creator_id"])
    rows = await svc.list_all()
    ids = {p.id for p in rows}
    assert a.id in ids
    assert b.id in ids


@pytest.mark.asyncio
async def test_list_by_cell(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    rows = await svc.list_by_cell(Team.BACKEND)
    assert project.id in {p.id for p in rows}


@pytest.mark.asyncio
async def test_set_workspace_path(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    updated = await svc.set_workspace_path(project.id, "/tmp/test-ws")
    assert updated is not None
    assert updated.workspace_path == "/tmp/test-ws"


@pytest.mark.asyncio
async def test_get_decrypted_token_round_trip(project_setup: dict) -> None:
    svc = project_setup["svc"]
    payload = _project_payload(uuid4().hex[:6])
    pd = payload.model_dump()
    pd["git_token"] = "ghp_secret"
    project = await svc.create(ProjectCreate(**pd), project_setup["creator_id"])
    decrypted = await svc.get_decrypted_token(project.id)
    assert decrypted == "ghp_secret"


@pytest.mark.asyncio
async def test_get_decrypted_token_returns_none_when_unset(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    assert await svc.get_decrypted_token(project.id) is None


@pytest.mark.asyncio
async def test_get_decrypted_token_by_slug_round_trip(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    payload = _project_payload(uuid4().hex[:6])
    pd = payload.model_dump()
    pd["git_token"] = "ghp_slug_secret"
    await svc.create(ProjectCreate(**pd), project_setup["creator_id"])
    decrypted = await svc.get_decrypted_token_by_slug(payload.slug)
    assert decrypted == "ghp_slug_secret"


@pytest.mark.asyncio
async def test_check_agent_access_returns_bool(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    has_access = await svc.check_agent_access(project.id, uuid4(), Team.BACKEND)
    assert isinstance(has_access, bool)


@pytest.mark.asyncio
async def test_add_and_remove_allowed_agent(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    new_agent_id = uuid4()
    added = await svc.add_allowed_agent(project.id, new_agent_id)
    assert added is not None
    removed = await svc.remove_allowed_agent(project.id, new_agent_id)
    assert removed is not None
