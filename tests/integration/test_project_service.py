"""ProjectService coverage — register/list/update/delete + token round-trip.

Driven by the real Postgres ``db_session`` fixture so the test exercises
the same SQLAlchemy paths the production code does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.config import settings
from roboco.db.tables import AgentTable
from roboco.exceptions import ValidationError
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.project import ProjectCreate, ProjectUpdate
from roboco.services.base import ConflictError, NotFoundError
from roboco.services.project import ProjectService, get_project_service
from roboco.utils.crypto import EncryptionError

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
async def test_create_project_rejects_protected_git_url(
    project_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project may not point at a denylisted repo (keeps agent merges out of it)."""
    monkeypatch.setattr(settings, "protected_git_urls", ["github.com/owner/roboco"])
    svc = project_setup["svc"]
    payload_dict = _project_payload(uuid4().hex[:6]).model_dump()
    payload_dict["git_url"] = "https://github.com/owner/roboco.git"
    with pytest.raises(ValidationError):
        await svc.create(ProjectCreate(**payload_dict), project_setup["creator_id"])


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


# ---------------------------------------------------------------------------
# create — encryption error during initial creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_project_encryption_error(project_setup: dict) -> None:
    """encrypt_token raising EncryptionError on create propagates."""

    svc = project_setup["svc"]
    payload = _project_payload(uuid4().hex[:6])
    pd = payload.model_dump()
    pd["git_token"] = "ghp_test"
    with (
        patch(
            "roboco.services.project.encrypt_token",
            side_effect=EncryptionError("bad key"),
        ),
        pytest.raises(EncryptionError),
    ):
        await svc.create(ProjectCreate(**pd), project_setup["creator_id"])


# ---------------------------------------------------------------------------
# get_or_raise — happy path returns project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_raise_returns_project(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    fetched = await svc.get_or_raise(project.id)
    assert fetched.id == project.id


# ---------------------------------------------------------------------------
# update — encryption error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_project_token_encryption_error(
    project_setup: dict,
) -> None:
    """EncryptionError on update token propagates."""

    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    with (
        patch(
            "roboco.services.project.encrypt_token",
            side_effect=EncryptionError("bad"),
        ),
        pytest.raises(EncryptionError),
    ):
        await svc.update(project.id, ProjectUpdate(git_token="ghp_x"))


# ---------------------------------------------------------------------------
# delete — full path with active sessions + workspace cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_project_returns_false_when_missing(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    assert await svc.delete(uuid4()) is False


@pytest.mark.asyncio
async def test_delete_project_with_active_work_session(
    project_setup: dict,
) -> None:
    """Active work sessions are abandoned before delete.

    Tasks reference the project with RESTRICT, so the task is created
    CANCELLED and then deleted before the project delete. The work
    session has CASCADE on its task_id, so deleting the task also
    cascades the ws — meaning no ws remains for the abandon loop to find.
    To exercise the abandon loop directly, we patch list to return a
    fake active session and mock abandon.
    """

    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )

    # Build a mock session that intercepts the active-sessions select.
    fake_ws = MagicMock()
    fake_ws.id = uuid4()
    mock_ws_svc = AsyncMock()
    mock_ws_svc.abandon = AsyncMock(return_value=None)

    real_execute = svc.session.execute

    async def _intercepting_execute(stmt, *args, **kwargs):
        # When the active-sessions select runs, return a stub with our fake ws.
        # Identify by substring in the SQL — the only WorkSession query in
        # delete() filters by project_id and status.
        stmt_str = str(stmt)
        if "work_sessions" in stmt_str.lower() and "status" in stmt_str.lower():
            stub = MagicMock()
            scalars_obj = MagicMock()
            scalars_obj.all.return_value = [fake_ws]
            stub.scalars.return_value = scalars_obj
            return stub
        return await real_execute(stmt, *args, **kwargs)

    with (
        patch(
            "roboco.services.work_session.get_work_session_service",
            return_value=mock_ws_svc,
        ),
        patch.object(svc.session, "execute", side_effect=_intercepting_execute),
    ):
        result = await svc.delete(project.id)
    assert result is True
    mock_ws_svc.abandon.assert_awaited()


@pytest.mark.asyncio
async def test_delete_project_with_workspace_cleanup(
    project_setup: dict, tmp_path
) -> None:
    """delete_workspaces=True triggers filesystem cleanup branch."""

    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    fake_path = tmp_path / "workspace-x"
    fake_path.mkdir()

    mock_ws_svc = AsyncMock()
    mock_ws_svc.list_workspaces = AsyncMock(return_value=[{"path": str(fake_path)}])
    with patch(
        "roboco.services.workspace.get_workspace_service",
        return_value=mock_ws_svc,
    ):
        result = await svc.delete(project.id, delete_workspaces=True)
    assert result is True
    # rmtree should have removed the dir.
    assert not fake_path.exists()


@pytest.mark.asyncio
async def test_delete_project_workspace_cleanup_failure_logged(
    project_setup: dict,
) -> None:
    """Workspace cleanup exception is swallowed (logged) inside the try."""

    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    mock_ws = AsyncMock()
    mock_ws.list_workspaces = AsyncMock(side_effect=RuntimeError("boom"))
    with patch(
        "roboco.services.workspace.get_workspace_service",
        return_value=mock_ws,
    ):
        # delete_workspaces=True; exception swallowed inside the try.
        result = await svc.delete(project.id, delete_workspaces=True)
    assert result is True


# ---------------------------------------------------------------------------
# set_workspace_path — None when missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_workspace_path_returns_none_when_missing(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    assert await svc.set_workspace_path(uuid4(), "/data/x") is None


# ---------------------------------------------------------------------------
# update_sync_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_sync_state_returns_none_when_missing(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    assert (await svc.update_sync_state(uuid4(), "abc12345abc12345")) is None


@pytest.mark.asyncio
async def test_update_sync_state_success(project_setup: dict) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    updated = await svc.update_sync_state(project.id, "abc12345abc12345")
    assert updated is not None
    assert updated.head_commit == "abc12345abc12345"


# ---------------------------------------------------------------------------
# get_decrypted_token — decrypt failure logs + raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_decrypted_token_decryption_error(
    project_setup: dict,
) -> None:

    svc = project_setup["svc"]
    payload = _project_payload(uuid4().hex[:6])
    pd = payload.model_dump()
    pd["git_token"] = "ghp_x"
    project = await svc.create(ProjectCreate(**pd), project_setup["creator_id"])
    with (
        patch(
            "roboco.services.project.decrypt_token",
            side_effect=EncryptionError("corrupt"),
        ),
        pytest.raises(EncryptionError),
    ):
        await svc.get_decrypted_token(project.id)


@pytest.mark.asyncio
async def test_get_decrypted_token_returns_none_when_project_missing(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    assert await svc.get_decrypted_token(uuid4()) is None


@pytest.mark.asyncio
async def test_get_decrypted_token_by_slug_decryption_error(
    project_setup: dict,
) -> None:

    svc = project_setup["svc"]
    payload = _project_payload(uuid4().hex[:6])
    pd = payload.model_dump()
    pd["git_token"] = "ghp_x"
    await svc.create(ProjectCreate(**pd), project_setup["creator_id"])
    with (
        patch(
            "roboco.services.project.decrypt_token",
            side_effect=EncryptionError("corrupt"),
        ),
        pytest.raises(EncryptionError),
    ):
        await svc.get_decrypted_token_by_slug(payload.slug)


@pytest.mark.asyncio
async def test_get_decrypted_token_by_slug_returns_none_when_missing(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    assert await svc.get_decrypted_token_by_slug("ghost") is None


# ---------------------------------------------------------------------------
# add_allowed_agent — None branch + idempotent path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_allowed_agent_returns_none_when_missing(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    assert await svc.add_allowed_agent(uuid4(), uuid4()) is None


@pytest.mark.asyncio
async def test_add_allowed_agent_idempotent(project_setup: dict) -> None:
    """Adding same agent twice doesn't duplicate."""
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    aid = uuid4()
    await svc.add_allowed_agent(project.id, aid)
    refreshed = await svc.add_allowed_agent(project.id, aid)
    assert refreshed is not None
    assert refreshed.allowed_agents.count(aid) == 1


@pytest.mark.asyncio
async def test_add_allowed_agent_appends_new(project_setup: dict) -> None:
    """Adding a different agent after one exists appends it."""
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    a1, a2 = uuid4(), uuid4()
    await svc.add_allowed_agent(project.id, a1)
    refreshed = await svc.add_allowed_agent(project.id, a2)
    assert refreshed is not None
    _EXPECTED = 2
    assert len(refreshed.allowed_agents) == _EXPECTED
    assert a1 in refreshed.allowed_agents
    assert a2 in refreshed.allowed_agents


# ---------------------------------------------------------------------------
# remove_allowed_agent — None branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_allowed_agent_missing_project(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    assert await svc.remove_allowed_agent(uuid4(), uuid4()) is None


@pytest.mark.asyncio
async def test_remove_allowed_agent_when_allowed_list_is_none(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    # allowed_agents is None by default.
    assert await svc.remove_allowed_agent(project.id, uuid4()) is None


# ---------------------------------------------------------------------------
# check_agent_access — non-matching cell + matching list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_agent_access_returns_false_for_missing_project(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    assert (await svc.check_agent_access(uuid4(), uuid4(), Team.BACKEND)) is False


@pytest.mark.asyncio
async def test_check_agent_access_wrong_cell_returns_false(
    project_setup: dict,
) -> None:
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    # Project is BACKEND; ask FRONTEND.
    assert (await svc.check_agent_access(project.id, uuid4(), Team.FRONTEND)) is False


@pytest.mark.asyncio
async def test_check_agent_access_with_allowed_list_membership(
    project_setup: dict,
) -> None:
    """Project with explicit allowed_agents list — checks membership."""
    svc = project_setup["svc"]
    project = await svc.create(
        _project_payload(uuid4().hex[:6]), project_setup["creator_id"]
    )
    target = uuid4()
    await svc.add_allowed_agent(project.id, target)
    # Same cell + member → True.
    assert (await svc.check_agent_access(project.id, target, Team.BACKEND)) is True
    # Same cell + non-member → False.
    assert (await svc.check_agent_access(project.id, uuid4(), Team.BACKEND)) is False


# ---------------------------------------------------------------------------
# Factory function smoke-test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_service_factory(db_session: AsyncSession) -> None:

    svc = get_project_service(db_session)
    assert isinstance(svc, ProjectService)
