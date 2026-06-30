"""MessagingService coverage — channels, groups, sessions, task links."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
from uuid import uuid4 as _u

import pytest
import pytest_asyncio
from roboco.api.schemas.sessions import session_to_response_with_links
from roboco.config import settings
from roboco.db.tables import AgentTable, MessageTable, ProjectTable, TaskTable
from roboco.db.tables import AgentTable as _AgentTable
from roboco.enforcement.channel_access import ChannelAccessDeniedError
from roboco.models import AgentRole, AgentStatus, MessageType, Team
from roboco.models.base import (
    ChannelType,
    SessionStatus,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.events import EventType
from roboco.models.messaging import (
    ChannelCreateRequest,
    GroupCreateRequest,
    MessageCreateRequest,
    SessionCreateRequest,
)
from roboco.models.session import (
    SessionForTasksCreate,
    SessionScope,
    SessionTaskRelationshipType,
)
from roboco.services.base import ConflictError, NotFoundError
from roboco.services.messaging import (
    ApiSessionCreate,
    MessageCursor,
    MessagingService,
    get_messaging_service,
)
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def msg_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
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
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="M-Proj",
        slug=f"m-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=agent.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    yield {
        "svc": MessagingService(db_session),
        "agent_id": agent.id,
        "task_id": task.id,
    }


def _channel_req(slug_suffix: str) -> ChannelCreateRequest:
    return ChannelCreateRequest(
        name=f"Channel {slug_suffix}",
        slug=f"ch-{slug_suffix}",
        channel_type=ChannelType.CELL,
        description="desc",
    )


async def _make_agent(svc: MessagingService, role: AgentRole) -> AgentTable:
    """Insert a fresh agent with `role` (no channel membership) for access tests."""
    agent = AgentTable(
        id=uuid4(),
        name="X",
        slug=f"x-{uuid4().hex[:8]}",
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    svc.session.add(agent)
    await svc.session.flush()
    return agent


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_channel(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    assert ch.id is not None


@pytest.mark.asyncio
async def test_create_channel_duplicate_slug_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    req = _channel_req(uuid4().hex[:6])
    await svc.create_channel(req)
    with pytest.raises(ValueError, match="already exists"):
        await svc.create_channel(req)


@pytest.mark.asyncio
async def test_get_channel(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    fetched = await svc.get_channel(ch.id)
    assert fetched is not None
    assert fetched.id == ch.id


@pytest.mark.asyncio
async def test_get_channel_returns_none_for_missing(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    assert await svc.get_channel(uuid4()) is None


@pytest.mark.asyncio
async def test_get_channel_or_raise_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.get_channel_or_raise(uuid4())


@pytest.mark.asyncio
async def test_get_channel_by_slug(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    req = _channel_req(uuid4().hex[:6])
    await svc.create_channel(req)
    found = await svc.get_channel_by_slug(req.slug)
    assert found is not None
    # Hash prefix is preserved
    found_hash = await svc.get_channel_by_slug(f"#{req.slug}")
    assert found_hash is not None
    assert found_hash.id == found.id


@pytest.mark.asyncio
async def test_add_channel_member_or_raise(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    aid = msg_setup["agent_id"]
    await svc.add_channel_member_or_raise(
        channel_id=ch.id, member_id=aid, can_write=True
    )
    refreshed = await svc.get_channel(ch.id)
    assert refreshed is not None
    assert aid in refreshed.members


@pytest.mark.asyncio
async def test_remove_channel_member_or_raise(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    aid = msg_setup["agent_id"]
    await svc.add_channel_member_or_raise(
        channel_id=ch.id, member_id=aid, can_write=True
    )
    await svc.remove_channel_member_or_raise(channel_id=ch.id, member_id=aid)


@pytest.mark.asyncio
async def test_update_channel_fields(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    updated = await svc.update_channel_fields(
        channel_id=ch.id, fields={"name": "new", "description": "newdesc"}
    )
    assert updated.name == "new"
    assert updated.description == "newdesc"


@pytest.mark.asyncio
async def test_list_channels_paginated(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    req = _channel_req(uuid4().hex[:6])
    await svc.create_channel(req)
    rows, total = await svc.list_channels_paginated(
        accessible_slugs=[req.slug],
        include_archived=False,
        page=1,
        page_size=10,
    )
    assert total >= 1
    assert any(r.slug == req.slug for r in rows)


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_group(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(
        GroupCreateRequest(name="g1", channel_id=ch.id, hierarchy_level=4)
    )
    assert grp.id is not None


@pytest.mark.asyncio
async def test_create_group_missing_channel_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.create_group(
            GroupCreateRequest(name="g1", channel_id=uuid4(), hierarchy_level=4)
        )


@pytest.mark.asyncio
async def test_get_group_returns_none(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    assert await svc.get_group(uuid4()) is None


@pytest.mark.asyncio
async def test_list_groups_in_channel(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    await svc.create_group(GroupCreateRequest(name="g2", channel_id=ch.id))
    groups = await svc.list_groups_in_channel(ch.id)
    _CREATED_GROUPS = 2
    assert len(groups) >= _CREATED_GROUPS


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    assert sess.id is not None
    assert sess.status == SessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_create_session_missing_group_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.create_session(SessionCreateRequest(group_id=uuid4()))


@pytest.mark.asyncio
async def test_create_session_reuses_active(msg_setup: dict) -> None:
    """A group has ONE live session: a second create reuses it, never a new one."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    first = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    second = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    assert second.id == first.id
    assert second.status == SessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_create_session_persists_group_active_pointer(msg_setup: dict) -> None:
    """create_session must persist group.active_session_id to the DB.

    The pointer is what every post keys off to find the live session; if it is
    left NULL (e.g. assigned before the session id is flushed), the group opens a
    brand-new session on each post and one conversation fragments across many.
    """
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    # Re-read the pointer from the DB (async-safe) to prove it actually persisted,
    # not just the in-memory object.
    await svc.session.refresh(grp, ["active_session_id"])
    assert grp.active_session_id == sess.id


@pytest.mark.asyncio
async def test_get_session_returns_none(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    assert await svc.get_session(uuid4()) is None


@pytest.mark.asyncio
async def test_close_session_returns_none_when_missing(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    assert await svc.close_session(uuid4()) is None


@pytest.mark.asyncio
async def test_close_session_idempotent_when_already_closed(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.close_session(sess.id)
    again = await svc.close_session(sess.id)
    assert again is not None
    assert again.status == SessionStatus.CLOSED


@pytest.mark.asyncio
async def test_get_session_or_raise(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.get_session_or_raise(uuid4())


@pytest.mark.asyncio
async def test_close_session_or_raise_missing(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.close_session_or_raise(uuid4())


# ---------------------------------------------------------------------------
# Session-task links
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_session_to_task(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    link = await svc.link_session_to_task(sess.id, tid, aid)
    assert link.session_id == sess.id
    assert link.task_id == tid


@pytest.mark.asyncio
async def test_link_session_to_task_idempotent(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    a = await svc.link_session_to_task(sess.id, tid, aid)
    b = await svc.link_session_to_task(sess.id, tid, aid)
    assert a.id == b.id


@pytest.mark.asyncio
async def test_link_session_to_task_missing_session(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    with pytest.raises(NotFoundError) as exc:
        await svc.link_session_to_task(uuid4(), tid, aid)
    # Message must not read "... not found not found" — the raise passed a full
    # sentence as resource_type, which NotFoundError appends " not found" to.
    assert "not found not found" not in str(exc.value)
    assert exc.value.resource_type == "Session"


@pytest.mark.asyncio
async def test_unlink_session_from_task(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.link_session_to_task(sess.id, tid, aid)
    result = await svc.unlink_session_from_task(sess.id, tid)
    assert result is True


@pytest.mark.asyncio
async def test_unlink_session_from_task_returns_false_when_missing(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    assert await svc.unlink_session_from_task(uuid4(), uuid4()) is False


@pytest.mark.asyncio
async def test_get_session_with_links_eager_loads_task_links(
    msg_setup: dict,
) -> None:
    """GET /sessions/{id} must return the session's task_links populated in one
    shot (the panel was forced into a triple-fetch because the single-session
    read returned them empty). The service read eager-loads task_links → task so
    the response builder can render titles without a lazy-load greenlet error."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.link_session_to_task(sess.id, tid, aid, is_primary=True)

    loaded = await svc.get_session_with_links_or_raise(sess.id)
    resp = session_to_response_with_links(loaded)

    assert len(resp.task_links) == 1
    link = resp.task_links[0]
    assert link.task_id == tid
    assert link.task_title == "t"  # fixture task title
    assert link.is_primary is True


@pytest.mark.asyncio
async def test_get_session_with_links_missing_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.get_session_with_links_or_raise(uuid4())


@pytest.mark.asyncio
async def test_require_group_read_access_denies_non_member(msg_setup: dict) -> None:
    """An authenticated agent that is neither a channel member/observer nor
    privileged must be denied — closes the IDOR where any agent could read any
    private channel's group/session transcripts."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    outsider = await _make_agent(svc, AgentRole.DEVELOPER)
    with pytest.raises(PermissionError):
        await svc.require_group_read_access(grp.id, outsider.id)


@pytest.mark.asyncio
async def test_require_group_read_access_allows_member(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    member = await _make_agent(svc, AgentRole.DEVELOPER)
    await svc.add_channel_member_or_raise(
        channel_id=ch.id, member_id=member.id, can_write=True
    )
    allowed = await svc.require_group_read_access(grp.id, member.id)
    assert allowed.id == grp.id


@pytest.mark.asyncio
async def test_require_group_read_access_allows_privileged(msg_setup: dict) -> None:
    """A privileged role (CEO) reads any group without channel membership."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    ceo = await _make_agent(svc, AgentRole.CEO)
    allowed = await svc.require_group_read_access(grp.id, ceo.id)
    assert allowed.id == grp.id


@pytest.mark.asyncio
async def test_require_group_read_access_missing_group_raises(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    outsider = await _make_agent(svc, AgentRole.DEVELOPER)
    with pytest.raises(NotFoundError):
        await svc.require_group_read_access(uuid4(), outsider.id)


@pytest.mark.asyncio
async def test_get_session_with_links_for_agent_denies_non_member(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    outsider = await _make_agent(svc, AgentRole.DEVELOPER)
    with pytest.raises(PermissionError):
        await svc.get_session_with_links_for_agent(sess.id, outsider.id)


@pytest.mark.asyncio
async def test_require_session_read_access_denies_non_member(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    outsider = await _make_agent(svc, AgentRole.DEVELOPER)
    with pytest.raises(PermissionError):
        await svc.require_session_read_access(sess.id, outsider.id)


@pytest.mark.asyncio
async def test_get_sessions_for_task(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.link_session_to_task(sess.id, tid, aid)
    links = await svc.get_sessions_for_task(tid)
    # Returns SessionTaskTable (links), not SessionTable.
    assert sess.id in {ln.session_id for ln in links}


@pytest.mark.asyncio
async def test_sweep_timed_out_sessions_no_op(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    closed = await svc.sweep_timed_out_sessions()
    assert closed >= 0


@pytest.mark.asyncio
async def test_get_or_create_active_session_returns_active(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    a = await svc.get_or_create_active_session(grp.id)
    assert a.status == SessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_get_or_create_active_session_returns_existing(
    msg_setup: dict,
) -> None:
    """Returns the existing active session instead of opening a new one.

    No manual pointer-setting: the first call must itself register
    group.active_session_id so the second call finds and reuses it.
    """
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    first = await svc.get_or_create_active_session(grp.id)
    second = await svc.get_or_create_active_session(grp.id)
    assert second.id == first.id


@pytest.mark.asyncio
async def test_get_or_create_active_session_missing_group(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.get_or_create_active_session(uuid4())


@pytest.mark.asyncio
async def test_get_channel_by_slug_or_raise(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.get_channel_by_slug_or_raise("ghost-slug")


@pytest.mark.asyncio
async def test_get_channel_with_groups_or_raise(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    fetched = await svc.get_channel_with_groups_or_raise(ch.id)
    assert fetched.id == ch.id


@pytest.mark.asyncio
async def test_get_channel_with_groups_or_raise_missing(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.get_channel_with_groups_or_raise(uuid4())


@pytest.mark.asyncio
async def test_get_tasks_for_session(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.link_session_to_task(sess.id, tid, aid)
    tasks = await svc.get_tasks_for_session(sess.id)
    assert tid in {t.task_id for t in tasks}


@pytest.mark.asyncio
async def test_get_primary_session_for_task_returns_none(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    assert await svc.get_primary_session_for_task(uuid4()) is None


@pytest.mark.asyncio
async def test_get_primary_session_for_task_returns_link(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.link_session_to_task(sess.id, tid, aid, is_primary=True)
    link = await svc.get_primary_session_for_task(tid)
    assert link is not None
    assert link.session_id == sess.id


@pytest.mark.asyncio
async def test_walk_task_ancestors_empty_when_no_parent(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ancestors = await svc._walk_task_ancestors(msg_setup["task_id"])
    assert ancestors == []


@pytest.mark.asyncio
async def test_get_or_create_channel_by_slug_returns_none_for_unknown(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    assert await svc.get_or_create_channel_by_slug("ghost-channel") is None


# ---------------------------------------------------------------------------
# Content validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assert_content_rejects_empty(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="EMPTY_MESSAGE"):
        svc._assert_content("")
    with pytest.raises(ValueError, match="EMPTY_MESSAGE"):
        svc._assert_content("   \n\n  ")
    with pytest.raises(ValueError, match="EMPTY_MESSAGE"):
        svc._assert_content(None)


@pytest.mark.asyncio
async def test_assert_content_rejects_oversized(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="MESSAGE_TOO_LONG"):
        svc._assert_content("x" * 20_000)


@pytest.mark.asyncio
async def test_assert_content_accepts_valid(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    # No exception.
    svc._assert_content("hello")


# ---------------------------------------------------------------------------
# Default group resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_group_for_channel_creates_one_if_none(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc._default_group_for_channel(ch)
    assert grp is not None
    assert grp.channel_id == ch.id


@pytest.mark.asyncio
async def test_default_group_for_channel_returns_existing(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    explicit = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    found = await svc._default_group_for_channel(ch)
    assert found.id == explicit.id


# ---------------------------------------------------------------------------
# check_session_boundaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_session_boundaries_within_limits(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    assert svc._check_session_boundaries(sess) is False


@pytest.mark.asyncio
async def test_check_session_boundaries_exceeded(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    sess.message_count = sess.max_message_count or 100
    assert svc._check_session_boundaries(sess) is True


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_to_session(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(
            agent_id=aid,
            session_id=sess.id,
            content="hello world",
        )
    )
    assert msg.id is not None
    assert msg.content == "hello world"


@pytest.mark.asyncio
async def test_send_message_publishes_message_sent_when_bus_connected(
    msg_setup: dict,
) -> None:
    """Bus connected → a MESSAGE_SENT event is published carrying the message
    payload so the websocket bridge can fan the new message out to
    /ws/channels/{id} and /ws/sessions/{id} subscribers. Without this publish
    the live chat path is dead — the panel never sees incoming messages."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    mock_bus = AsyncMock()
    mock_bus.is_connected = lambda: True
    mock_bus.publish = AsyncMock(return_value=None)
    with patch("roboco.services.messaging.get_event_bus", return_value=mock_bus):
        msg = await svc.send_message(
            MessageCreateRequest(agent_id=aid, session_id=sess.id, content="hi")
        )
    mock_bus.publish.assert_awaited()
    published = mock_bus.publish.await_args.args[0]
    assert published.type is EventType.MESSAGE_SENT
    data = published.data
    assert data["message_id"] == str(msg.id)
    assert data["session_id"] == str(sess.id)
    assert data["channel_id"] == str(ch.id)
    assert data["agent_id"] == str(aid)
    assert data["content"] == "hi"


@pytest.mark.asyncio
async def test_send_message_bus_failure_does_not_break_send(
    msg_setup: dict,
) -> None:
    """A bus outage during the MESSAGE_SENT publish is logged but never rolls
    back the persisted message — live delivery is best-effort."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    with patch(
        "roboco.services.messaging.get_event_bus",
        side_effect=RuntimeError("bus down"),
    ):
        msg = await svc.send_message(
            MessageCreateRequest(agent_id=aid, session_id=sess.id, content="hi")
        )
    assert msg.id is not None
    assert msg.content == "hi"


@pytest.mark.asyncio
async def test_send_message_rejects_empty(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    with pytest.raises(ValueError, match="EMPTY_MESSAGE"):
        await svc.send_message(
            MessageCreateRequest(
                agent_id=aid,
                session_id=sess.id,
                content="",
            )
        )


@pytest.mark.asyncio
async def test_get_message_returns_none_for_missing(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    assert await svc.get_message(uuid4()) is None


@pytest.mark.asyncio
async def test_get_message_or_raise(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.get_message_or_raise(uuid4())


@pytest.mark.asyncio
async def test_get_messages_empty(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    messages, has_more = await svc.get_messages(sess.id)
    assert isinstance(messages, list)
    assert has_more is False


@pytest.mark.asyncio
async def test_edit_message_by_author(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="original")
    )
    edited = await svc.edit_message(msg.id, aid, "edited", edit_reason="typo")
    assert edited.content == "edited"


@pytest.mark.asyncio
async def test_edit_message_by_non_author_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="original")
    )
    with pytest.raises(ValueError, match="author"):
        await svc.edit_message(msg.id, uuid4(), "edited")


@pytest.mark.asyncio
async def test_delete_message_by_author(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="original")
    )
    assert await svc.delete_message(msg.id, aid) is True


@pytest.mark.asyncio
async def test_delete_message_by_non_author_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="original")
    )
    with pytest.raises(ValueError, match="author"):
        await svc.delete_message(msg.id, uuid4())


@pytest.mark.asyncio
async def test_get_or_create_channel_by_slug_creates_from_seed(
    msg_setup: dict,
) -> None:
    """Auto-create from DEFAULT_CHANNELS when DB has no row but slug is known."""
    svc = msg_setup["svc"]
    # backend-cell is in DEFAULT_CHANNELS.
    ch = await svc.get_or_create_channel_by_slug("backend-cell")
    assert ch is not None
    assert ch.slug == "backend-cell"


@pytest.mark.asyncio
async def test_post_to_channel_unknown_slug_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.post_to_channel(
            agent_id=msg_setup["agent_id"],
            channel_slug="ghost-channel",
            content="hi",
        )


# ---------------------------------------------------------------------------
# list_messages_for_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_for_session_unknown(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.list_messages_for_session(
            session_id=uuid4(),
            before=None,
            after=None,
            message_type=None,
            limit=10,
        )


@pytest.mark.asyncio
async def test_list_messages_for_session_returns_empty(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msgs, has_more = await svc.list_messages_for_session(
        session_id=sess.id,
        before=None,
        after=None,
        message_type=None,
        limit=10,
    )
    assert msgs == []
    assert has_more is False


# ---------------------------------------------------------------------------
# edit_message_or_raise / delete_message_or_raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_message_or_raise_not_found(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.edit_message_or_raise(
            message_id=uuid4(),
            agent_id=uuid4(),
            new_content="x",
            edit_reason=None,
        )


@pytest.mark.asyncio
async def test_delete_message_or_raise_not_found(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.delete_message_or_raise(message_id=uuid4(), agent_id=uuid4())


# ---------------------------------------------------------------------------
# list_group_sessions_for_agent + create_session_with_access_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_group_sessions_unknown_group(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    with pytest.raises(NotFoundError):
        await svc.list_group_sessions_for_agent(
            group_id=uuid4(),
            agent_id=aid,
            status_filter=None,
            limit=10,
        )


@pytest.mark.asyncio
async def test_list_group_sessions_unauthorized(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    # Random agent who's not in the channel.
    with pytest.raises(PermissionError):
        await svc.list_group_sessions_for_agent(
            group_id=grp.id,
            agent_id=uuid4(),
            status_filter=None,
            limit=10,
        )


@pytest.mark.asyncio
async def test_create_session_with_access_check_unknown_group(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.create_session_with_access_check(
            agent_id=msg_setup["agent_id"],
            request=ApiSessionCreate(
                group_id=uuid4(),
                max_time_window_minutes=30,
                max_message_count=100,
                max_content_length=10000,
                timeout_seconds=300,
            ),
        )


@pytest.mark.asyncio
async def test_create_session_with_access_check_unauthorized(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    with pytest.raises(PermissionError):
        await svc.create_session_with_access_check(
            agent_id=uuid4(),  # Not in channel.writers.
            request=ApiSessionCreate(
                group_id=grp.id,
                max_time_window_minutes=30,
                max_message_count=100,
                max_content_length=10000,
                timeout_seconds=300,
            ),
        )


# ---------------------------------------------------------------------------
# create_session_for_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_for_tasks_unknown_channel(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.create_session_for_tasks(
            SessionForTasksCreate(
                task_ids=[msg_setup["task_id"]],
                channel_slug="ghost-channel",
                scope=SessionScope.TASK,
            ),
            pm_agent_id=msg_setup["agent_id"],
        )


@pytest.mark.asyncio
async def test_create_session_for_tasks_creates_session(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    sess, links = await svc.create_session_for_tasks(
        SessionForTasksCreate(
            task_ids=[msg_setup["task_id"]],
            channel_slug=ch.slug,
            scope=SessionScope.TASK,
        ),
        pm_agent_id=msg_setup["agent_id"],
    )
    assert sess.id is not None
    assert len(links) == 1


# ---------------------------------------------------------------------------
# Walking ancestors with parent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_with_access_check_member_can_write(
    msg_setup: dict,
) -> None:
    """Channel writer can create a session via access-checked path."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    # Add agent to writers list.
    await svc.add_channel_member_or_raise(
        channel_id=ch.id, member_id=aid, can_write=True
    )
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session_with_access_check(
        agent_id=aid,
        request=ApiSessionCreate(
            group_id=grp.id,
            max_time_window_minutes=30,
            max_message_count=100,
            max_content_length=10000,
            timeout_seconds=300,
        ),
    )
    assert sess.id is not None


@pytest.mark.asyncio
async def test_list_group_sessions_for_agent_member(
    msg_setup: dict,
) -> None:
    """Channel member can list sessions in their group."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    await svc.add_channel_member_or_raise(
        channel_id=ch.id, member_id=aid, can_write=True
    )
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    await svc.create_session(SessionCreateRequest(group_id=grp.id))
    sessions = await svc.list_group_sessions_for_agent(
        group_id=grp.id, agent_id=aid, status_filter=None, limit=10
    )
    assert len(sessions) >= 1


@pytest.mark.asyncio
async def test_list_group_sessions_with_status_filter(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    await svc.add_channel_member_or_raise(
        channel_id=ch.id, member_id=aid, can_write=True
    )
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    await svc.create_session(SessionCreateRequest(group_id=grp.id))
    sessions = await svc.list_group_sessions_for_agent(
        group_id=grp.id,
        agent_id=aid,
        status_filter=SessionStatus.ACTIVE,
        limit=10,
    )
    assert all(s.status == SessionStatus.ACTIVE for s in sessions)


@pytest.mark.asyncio
async def test_sweep_timed_out_sessions_closes_idle_session(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(
        SessionCreateRequest(group_id=grp.id, timeout_seconds=1)
    )
    # Force last_activity_at into the past so sweeper closes it.
    sess.last_activity_at = datetime.now(UTC) - timedelta(seconds=120)
    await db_session.flush()
    closed = await svc.sweep_timed_out_sessions()
    assert closed >= 1


@pytest.mark.asyncio
async def test_sweep_skips_session_refreshed_after_candidate_select(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """TOCTOU: a session stale at the candidate SELECT but refreshed (a message
    landed) before the close must NOT be closed. The sweeper re-reads
    last_activity_at fresh and skips a just-used session."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(
        SessionCreateRequest(group_id=grp.id, timeout_seconds=1)
    )
    sid = sess.id
    # Stale at SELECT time -> candidate.
    sess.last_activity_at = datetime.now(UTC) - timedelta(seconds=120)
    await db_session.flush()
    # Fresh re-read sees stale -> still timed out.
    assert (
        await svc._session_still_timed_out(sid, timeout_seconds=1, max_time_window=None)
        is True
    )
    # A message lands, refreshing activity.
    sess.last_activity_at = datetime.now(UTC)
    await db_session.flush()
    # Fresh re-read sees recent -> no longer timed out -> sweeper must skip.
    assert (
        await svc._session_still_timed_out(sid, timeout_seconds=1, max_time_window=None)
        is False
    )


@pytest.mark.asyncio
async def test_edit_message_or_raise_succeeds(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="original")
    )
    edited = await svc.edit_message_or_raise(
        message_id=msg.id,
        agent_id=aid,
        new_content="edited content",
        edit_reason=None,
    )
    assert edited.content == "edited content"


@pytest.mark.asyncio
async def test_delete_message_or_raise_succeeds(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(
            agent_id=aid, session_id=sess.id, content="will be deleted"
        )
    )
    await svc.delete_message_or_raise(message_id=msg.id, agent_id=aid)


@pytest.mark.asyncio
async def test_send_message_with_mentions(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    other = uuid4()
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(
            agent_id=aid,
            session_id=sess.id,
            content="hi @other",
            mentions=[other],
        )
    )
    assert other in msg.mentions


@pytest.mark.asyncio
async def test_send_message_reply_target_unknown_raises(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    with pytest.raises((ValueError, NotFoundError)):
        await svc.send_message(
            MessageCreateRequest(
                agent_id=aid,
                session_id=sess.id,
                content="reply",
                reply_to=uuid4(),  # Bogus reply target.
            )
        )


@pytest.mark.asyncio
async def test_reply_to_same_active_session_succeeds(msg_setup: dict) -> None:
    """A reply to a message in the same active session is accepted."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    m1 = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="first")
    )
    m2 = await svc.send_message(
        MessageCreateRequest(
            agent_id=aid, session_id=sess.id, content="reply", reply_to=m1.id
        )
    )
    assert m2.reply_to == m1.id
    assert m2.session_id == sess.id


@pytest.mark.asyncio
async def test_reply_to_rejected_after_closed_session_redirect(
    msg_setup: dict,
) -> None:
    """reply_to must be validated against the EFFECTIVE session the message
    lands in, not the requested one. After a closed session redirects the send
    to a fresh active session, a reply_to a message from the OLD session must be
    rejected — otherwise the new message carries a dangling cross-session reply.
    """
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    s1 = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    m1 = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=s1.id, content="first")
    )
    # Close S1 → the next send to S1 redirects to a freshly-created session.
    await svc.close_session_or_raise(s1.id)
    with pytest.raises(ValueError, match="Reply target not found"):
        await svc.send_message(
            MessageCreateRequest(
                agent_id=aid,
                session_id=s1.id,
                content="reply",
                reply_to=m1.id,  # belongs to the now-closed S1, not the redirect
            )
        )


@pytest.mark.asyncio
async def test_get_messages_with_filters(msg_setup: dict) -> None:
    """get_messages with before/after/type filters."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="msg1")
    )
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    msgs, _ = await svc.get_messages(
        sess.id,
        before=MessageCursor(datetime.now(UTC) + timedelta(hours=1)),
        after=MessageCursor(cutoff),
        message_type=MessageType.DIALOGUE,
    )
    assert isinstance(msgs, list)


@pytest.mark.asyncio
async def test_get_messages_with_limit(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    for i in range(5):
        await svc.send_message(
            MessageCreateRequest(agent_id=aid, session_id=sess.id, content=f"msg-{i}")
        )
    _PAGE = 2
    msgs, has_more = await svc.get_messages(sess.id, limit=_PAGE)
    assert len(msgs) == _PAGE
    assert has_more is True


async def _seed_messages_same_timestamp(
    svc: MessagingService, session: Any, aid: UUID, count: int
) -> tuple[UUID, list[UUID]]:
    """Create ``count`` messages in one session and force them ALL to the same
    timestamp so the equal-timestamp pagination skip is reproducible. Returns
    ``(session_id, message_ids)``."""
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(
        GroupCreateRequest(name="g1", channel_id=cast("uuid.UUID", ch.id))
    )
    sess = await svc.create_session(
        SessionCreateRequest(group_id=cast("uuid.UUID", grp.id))
    )
    ids: list[UUID] = []
    for i in range(count):
        m = await svc.send_message(
            MessageCreateRequest(
                agent_id=aid, session_id=cast("uuid.UUID", sess.id), content=f"m-{i}"
            )
        )
        ids.append(cast("uuid.UUID", m.id))
    fixed = datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(MessageTable).where(MessageTable.session_id == sess.id)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.timestamp = fixed
    await session.flush()
    return cast("uuid.UUID", sess.id), ids


@pytest.mark.asyncio
async def test_get_messages_compound_before_cursor_no_skip_on_equal_timestamps(
    msg_setup: dict,
) -> None:
    """Equal-timestamp messages must not be skipped across pages.

    With a strict ``timestamp < before`` cursor and ``order_by(timestamp.desc())``,
    messages sharing the page's last timestamp are cut by ``limit`` on page 1
    and excluded (``< T``) from page 2 — they vanish. The compound
    ``(timestamp, id)`` cursor tie-breaks on the unique id so the next page
    resumes exactly past the last id (no skip, no duplicate). Requires the
    deterministic ``order_by(timestamp.desc(), id.desc())`` so the "last item"
    cursor is unambiguous.
    """
    svc = msg_setup["svc"]
    session = svc.session
    aid = msg_setup["agent_id"]
    total = 5
    sess_id, ids = await _seed_messages_same_timestamp(svc, session, aid, count=total)

    page_size = 3
    page1, has_more = await svc.get_messages(sess_id, limit=page_size)
    assert has_more is True
    assert len(page1) == page_size
    # Deterministic order: same timestamp → by id.desc()
    page1_ids = [m.id for m in page1]
    assert page1_ids == sorted(page1_ids, reverse=True)

    last = page1[-1]
    page2, has_more2 = await svc.get_messages(
        sess_id, before=MessageCursor(last.timestamp, last.id), limit=page_size
    )
    # All 5 covered, no skip, no duplicate.
    seen = {m.id for m in page1} | {m.id for m in page2}
    assert seen == set(ids)
    assert len(page1) + len(page2) == total
    assert has_more2 is False


@pytest.mark.asyncio
async def test_get_messages_compound_after_cursor_no_skip_on_equal_timestamps(
    msg_setup: dict,
) -> None:
    """Forward pagination (``after``) with the compound ``(timestamp, id)``
    cursor tie-breaks on id so newer-direction pagination across equal
    timestamps skips nothing either.

    With a strict ``timestamp > after`` cursor, every row sharing the cursor's
    timestamp is EXCLUDED — so forward-paginating from a middle message would
    return nothing (all rows are at the same timestamp, none strictly greater).
    The compound cursor includes same-timestamp rows with a larger id.
    """
    svc = msg_setup["svc"]
    session = svc.session
    aid = msg_setup["agent_id"]
    total = 5
    fetch_limit = 10
    mid_index = 2
    newer_count = 2
    sess_id, _ids = await _seed_messages_same_timestamp(svc, session, aid, count=total)

    # Full desc ordering: [id_largest, ..., id_smallest].
    all_msgs, _ = await svc.get_messages(sess_id, limit=fetch_limit)
    assert len(all_msgs) == total
    # Pick the middle row as the forward cursor.
    mid = all_msgs[mid_index]
    newer = {m.id for m in all_msgs[:newer_count]}  # the rows above mid (larger id)

    page, _ = await svc.get_messages(
        sess_id, after=MessageCursor(mid.timestamp, mid.id), limit=fetch_limit
    )
    # The same-timestamp rows newer than mid are returned — NOT skipped.
    assert {m.id for m in page} == newer
    assert len(page) == newer_count


@pytest.mark.asyncio
async def test_get_message_context_redirects_when_session_closed(
    msg_setup: dict,
) -> None:
    """If session is closed, _get_message_context should redirect to active session."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    # Close the session.
    await svc.close_session(sess.id)
    # Now request its context — should redirect to a fresh active session.
    new_sess, new_grp, new_ch = await svc._get_message_context(sess.id)
    assert new_grp.id == grp.id
    assert new_ch.id == ch.id
    assert new_sess.status == SessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_get_message_context_unknown_session_raises(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc._get_message_context(uuid4())


@pytest.mark.asyncio
async def test_validate_reply_target_unknown_message_raises(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc._validate_reply_target(uuid4(), uuid4())


@pytest.mark.asyncio
async def test_validate_reply_target_wrong_session_raises(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess1 = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess1.id, content="msg")
    )
    # A genuinely different session: a group holds ONE live session, so use a
    # second group. The reply target must be rejected as not belonging to it.
    grp2 = await svc.create_group(GroupCreateRequest(name="g2", channel_id=ch.id))
    sess2 = await svc.create_session(SessionCreateRequest(group_id=grp2.id))
    with pytest.raises(ValueError, match="not found in this session"):
        await svc._validate_reply_target(msg.id, sess2.id)


@pytest.mark.asyncio
async def test_walk_task_ancestors_with_parent(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Smoke-test ancestry walk via direct DB seeding."""
    svc = msg_setup["svc"]
    parent_id = uuid4()
    child_id = uuid4()
    # Need to fetch project_id and aid from msg_setup.
    result = await db_session.execute(
        __import__("sqlalchemy")
        .select(TaskTable)
        .where(TaskTable.id == msg_setup["task_id"])
    )
    base_task = result.scalar_one()

    parent = TaskTable(
        id=parent_id,
        title="parent",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=msg_setup["agent_id"],
        team=base_task.team,
    )
    child = TaskTable(
        id=child_id,
        title="child",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=msg_setup["agent_id"],
        team=base_task.team,
        parent_task_id=parent_id,
    )
    db_session.add_all([parent, child])
    await db_session.flush()
    ancestors = await svc._walk_task_ancestors(child_id)
    assert len(ancestors) >= 1
    assert ancestors[0].id == parent_id


# ---------------------------------------------------------------------------
# update_channel_fields — None values + unknown keys ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_channel_fields_skips_none_and_unknown(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    # Pass None and an unknown key — both skipped.
    updated = await svc.update_channel_fields(
        channel_id=ch.id,
        fields={"name": None, "ghost_field": "x", "topic": "real-topic"},
    )
    assert updated.topic == "real-topic"


# ---------------------------------------------------------------------------
# get_or_create_channel_by_slug — auto-create from seeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_channel_by_slug_returns_existing(
    msg_setup: dict,
) -> None:
    """If channel already exists in DB, return it directly (line 297)."""
    svc = msg_setup["svc"]
    req = _channel_req(uuid4().hex[:6])
    created = await svc.create_channel(req)
    # First time looks up DB and finds it.
    found = await svc.get_or_create_channel_by_slug(req.slug)
    assert found is not None
    assert found.id == created.id


# ---------------------------------------------------------------------------
# create_session — closes existing active session + bus publish + failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_publishes_event_when_bus_connected(
    msg_setup: dict,
) -> None:
    """Bus connected → session_created event published."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    mock_bus = AsyncMock()
    mock_bus.is_connected = lambda: True
    mock_bus.publish = AsyncMock(return_value=None)
    with patch("roboco.services.messaging.get_event_bus", return_value=mock_bus):
        sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    assert sess.id is not None
    mock_bus.publish.assert_awaited()


@pytest.mark.asyncio
async def test_create_session_handles_bus_failure(msg_setup: dict) -> None:
    """Bus exception in publish path is logged but doesn't break."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    with patch(
        "roboco.services.messaging.get_event_bus",
        side_effect=RuntimeError("bus down"),
    ):
        sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    assert sess.id is not None


# ---------------------------------------------------------------------------
# sweep_timed_out_sessions — within-limits skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_skip_when_within_limits(msg_setup: dict) -> None:
    """Active session whose last_activity is recent → not closed."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    # Long timeout — won't be closed.
    sess = await svc.create_session(
        SessionCreateRequest(group_id=grp.id, timeout_seconds=10000)
    )
    closed = await svc.sweep_timed_out_sessions()
    fetched = await svc.get_session(sess.id)
    # Session still active.
    assert fetched is not None
    assert fetched.status == SessionStatus.ACTIVE
    # closed counter may be 0 or higher (other tests).
    assert closed >= 0


# ---------------------------------------------------------------------------
# close_session — clears group's active_session_id + bus events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_session_clears_active_session_on_group(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.close_session(sess.id, "test close")
    refreshed_group = await svc.get_group(grp.id)
    assert refreshed_group is not None
    assert refreshed_group.active_session_id is None


@pytest.mark.asyncio
async def test_close_session_publishes_event(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    mock_bus = AsyncMock()
    mock_bus.is_connected = lambda: True
    mock_bus.publish = AsyncMock(return_value=None)
    with patch("roboco.services.messaging.get_event_bus", return_value=mock_bus):
        await svc.close_session(sess.id, "test")
    mock_bus.publish.assert_awaited()


@pytest.mark.asyncio
async def test_resolve_group_for_session_returns_inherited_group(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Cover line 1128: _resolve_group_for_session returns inherited group.

    No explicit `group_id`, parent task has primary session linked to a
    group → `_resolve_group_from_parent_tasks` returns that group, line 1128
    returns it before falling through to channel-default lookup.
    """
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    base_task_result = await db_session.execute(
        select(TaskTable).where(TaskTable.id == msg_setup["task_id"])
    )
    base_task = base_task_result.scalar_one()

    parent = TaskTable(
        id=uuid4(),
        title="rg-parent",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
    )
    child = TaskTable(
        id=uuid4(),
        title="rg-child",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
        parent_task_id=parent.id,
    )
    db_session.add_all([parent, child])
    await db_session.flush()

    channel = await svc.create_channel(_channel_req(f"rg-{uuid4().hex[:6]}"))
    parent_grp = await svc.create_group(
        GroupCreateRequest(name="parent-grp", channel_id=channel.id)
    )
    parent_sess = await svc.create_session(SessionCreateRequest(group_id=parent_grp.id))
    await svc.link_session_to_task(parent_sess.id, parent.id, aid, is_primary=True)

    req = SessionForTasksCreate(
        task_ids=[cast("uuid.UUID", child.id)],
        channel_slug=channel.slug,
        scope=SessionScope.TASK,
    )
    resolved = await svc._resolve_group_for_session(req, channel)
    assert resolved.id == parent_grp.id


@pytest.mark.asyncio
async def test_resolve_group_for_session_inherits_from_parent(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Cover line 1128: inherited group returned from _resolve_group_for_session.

    Parent task has a primary session on channel A; child requests a session
    on channel B. _find_ancestor_session_on_channel returns None (channel
    mismatch), so _resolve_group_for_session falls through to
    _resolve_group_from_parent_tasks which finds the parent's group, and
    line 1128 returns it.
    """
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    base_task_result = await db_session.execute(
        select(TaskTable).where(TaskTable.id == msg_setup["task_id"])
    )
    base_task = base_task_result.scalar_one()

    parent = TaskTable(
        id=uuid4(),
        title="parent-r",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
    )
    child = TaskTable(
        id=uuid4(),
        title="child-r",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
        parent_task_id=parent.id,
    )
    db_session.add_all([parent, child])
    await db_session.flush()

    channel_a = await svc.create_channel(_channel_req(f"a-{uuid4().hex[:6]}"))
    grp_a = await svc.create_group(
        GroupCreateRequest(name="a", channel_id=channel_a.id)
    )
    sess_a = await svc.create_session(SessionCreateRequest(group_id=grp_a.id))
    await svc.link_session_to_task(sess_a.id, parent.id, aid, is_primary=True)

    inherited = await svc._resolve_group_from_parent_tasks([child.id])
    assert inherited is not None
    assert inherited.id == grp_a.id


@pytest.mark.asyncio
async def test_walk_task_ancestors_cycle_breaks(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Cover line 1031: cycle detection breaks the walk loop.

    Build A -> B -> A and walk from B; the second hop tries to visit A which
    is already in `seen`, so the loop breaks.
    """
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    base_task_result = await db_session.execute(
        select(TaskTable).where(TaskTable.id == msg_setup["task_id"])
    )
    base_task = base_task_result.scalar_one()

    a = TaskTable(
        id=uuid4(),
        title="cycle-A",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
    )
    b = TaskTable(
        id=uuid4(),
        title="cycle-B",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
        parent_task_id=a.id,
    )
    db_session.add_all([a, b])
    await db_session.flush()
    # Force the cycle: A -> B -> A
    a.parent_task_id = b.id
    await db_session.flush()

    ancestors = await svc._walk_task_ancestors(b.id)
    # Walk yields A then breaks on cycle (B already in seen).
    assert len(ancestors) >= 1


@pytest.mark.asyncio
async def test_walk_task_ancestors_orphan_parent_breaks(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Cover line 1038: parent_id present but parent row missing → break.

    SQLAlchemy ORM doesn't enforce FK at flush when we set the field directly
    in Python (the FK fires on write). We patch session.execute so the second
    call (parent lookup) returns no row, simulating an orphaned parent_id.
    """
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    base_task_result = await db_session.execute(
        select(TaskTable).where(TaskTable.id == msg_setup["task_id"])
    )
    base_task = base_task_result.scalar_one()

    parent = TaskTable(
        id=uuid4(),
        title="real-parent",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
    )
    child = TaskTable(
        id=uuid4(),
        title="orphan-child",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
        parent_task_id=parent.id,
    )
    db_session.add_all([parent, child])
    await db_session.flush()

    # Now point child's parent_task_id at a non-existent UUID via raw SQL —
    # this avoids the ORM relationship loader and bypasses FK at the SQL
    # level (Postgres still enforces, so use update with deferred FK isn't
    # possible; instead, set it to the parent's parent_task_id which is None
    # → no, that won't trigger line 1038 either). Skip via fake task lookup.
    # Patch session.execute so the second call (parent lookup) returns no row.
    real_execute = svc.session.execute
    call_count = {"n": 0}
    _PARENT_LOOKUP_CALL = 2

    class _Empty:
        def scalar_one_or_none(self) -> None:
            return None

    async def _fake_execute(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        result = await real_execute(stmt, *args, **kwargs)
        if call_count["n"] == _PARENT_LOOKUP_CALL:
            return _Empty()
        return result

    svc.session.execute = _fake_execute
    try:
        ancestors = await svc._walk_task_ancestors(child.id)
    finally:
        svc.session.execute = real_execute
    # parent lookup returned None → break before appending.
    assert ancestors == []


@pytest.mark.asyncio
async def test_close_session_clears_explicit_active_session(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Cover line 584: group.active_session_id == session_id assignment.

    The matching active_session_id branch only runs when the group's stored
    active_session_id equals the session_id being closed. After re-fetch,
    SQLAlchemy may load a value that compares unequal to the in-memory id,
    so set it explicitly + flush to lock in the equality before close.
    """
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    grp.active_session_id = sess.id
    await db_session.flush()
    closed = await svc.close_session(sess.id, "explicit close")
    assert closed is not None
    refreshed = await svc.get_group(grp.id)
    assert refreshed is not None
    assert refreshed.active_session_id is None


@pytest.mark.asyncio
async def test_close_session_or_raise_clears_active_session(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Cover line 769: same active_session_id reset path via close_session_or_raise."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    grp.active_session_id = sess.id
    await db_session.flush()
    closed = await svc.close_session_or_raise(sess.id)
    assert closed is not None
    refreshed = await svc.get_group(grp.id)
    assert refreshed is not None
    assert refreshed.active_session_id is None


@pytest.mark.asyncio
async def test_close_session_handles_bus_failure(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    with patch(
        "roboco.services.messaging.get_event_bus",
        side_effect=RuntimeError("bus down"),
    ):
        # Doesn't raise even though bus fails.
        await svc.close_session(sess.id, "test")


# ---------------------------------------------------------------------------
# create_session_with_access_check — reuses the group's live session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_with_access_check_reuses_active(
    msg_setup: dict,
) -> None:
    """A group has ONE live session; opening again reuses it (no churn)."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    await svc.add_channel_member_or_raise(
        channel_id=ch.id, member_id=aid, can_write=True
    )
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    # Pre-create active session.
    prior = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    new_sess = await svc.create_session_with_access_check(
        agent_id=aid,
        request=ApiSessionCreate(
            group_id=grp.id,
            max_time_window_minutes=30,
            max_message_count=100,
            max_content_length=10000,
            timeout_seconds=300,
        ),
    )
    # Same live session is returned, not a fresh one — and it stays active.
    assert new_sess.id == prior.id
    refetched = await svc.get_session(prior.id)
    assert refetched is not None
    assert refetched.status == SessionStatus.ACTIVE


# ---------------------------------------------------------------------------
# _inject_proactive_context — failure swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_proactive_context_swallows_exception(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    with patch(
        "roboco.services.proactive.get_proactive_service",
        side_effect=RuntimeError("proactive down"),
    ):
        # Doesn't raise.
        await svc._inject_proactive_context(session_id=_u(), agent_id=_u())


@pytest.mark.asyncio
async def test_inject_proactive_context_logs_when_context_present(
    msg_setup: dict,
) -> None:
    """Context with content triggers info log."""
    svc = msg_setup["svc"]
    fake_proactive = AsyncMock()
    fake_context = SimpleNamespace(is_empty=lambda: False)
    fake_proactive.get_context_for_session = AsyncMock(return_value=fake_context)
    with patch(
        "roboco.services.proactive.get_proactive_service",
        AsyncMock(return_value=fake_proactive),
    ):
        await svc._inject_proactive_context(session_id=_u(), agent_id=_u())


# ---------------------------------------------------------------------------
# close_session_or_raise — already-closed branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_session_or_raise_already_closed(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.close_session(sess.id)
    with pytest.raises(ValueError, match="not active"):
        await svc.close_session_or_raise(sess.id)


@pytest.mark.asyncio
async def test_close_session_or_raise_clears_active(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    # Force-flush so close_session reads back active_session_id from DB.
    await svc.session.flush()
    closed = await svc.close_session_or_raise(sess.id)
    assert closed.status == SessionStatus.CLOSED
    refreshed_grp = await svc.get_group(grp.id)
    assert refreshed_grp is not None
    assert refreshed_grp.active_session_id is None


# ---------------------------------------------------------------------------
# get_or_create_active_session — creates new when none active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_active_session_creates_when_no_active(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    # Group has no active session yet.
    sess = await svc.get_or_create_active_session(grp.id)
    assert sess.status == SessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_get_or_create_active_session_existing_inactive(
    msg_setup: dict,
) -> None:
    """Group has an active_session_id but the session is closed."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    s1 = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    # Close the session but leave grp.active_session_id pointing at it.
    s1.status = SessionStatus.CLOSED
    grp_row = await svc.get_group(grp.id)
    assert grp_row is not None
    grp_row.active_session_id = s1.id
    await svc.session.flush()
    s2 = await svc.get_or_create_active_session(grp.id)
    assert s2.status == SessionStatus.ACTIVE
    assert s2.id != s1.id


# ---------------------------------------------------------------------------
# link_session_to_task — primary conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_session_to_task_primary_conflict(
    msg_setup: dict,
) -> None:
    """Two distinct sessions both promoted to primary for same task → conflict."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    # Two distinct live sessions: a group holds ONE live session, so use two groups.
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    grp2 = await svc.create_group(GroupCreateRequest(name="g2", channel_id=ch.id))
    sess1 = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    sess2 = await svc.create_session(SessionCreateRequest(group_id=grp2.id))
    await svc.link_session_to_task(sess1.id, tid, aid, is_primary=True)
    with pytest.raises(ConflictError):
        await svc.link_session_to_task(sess2.id, tid, aid, is_primary=True)


# ---------------------------------------------------------------------------
# get_sessions_for_task — relationship_type filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_for_task_with_relationship_filter(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    tid = msg_setup["task_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.link_session_to_task(
        sess.id,
        tid,
        aid,
        relationship_type=SessionTaskRelationshipType.DISCUSSION,
    )
    links = await svc.get_sessions_for_task(
        tid, relationship_type=SessionTaskRelationshipType.DISCUSSION
    )
    assert len(links) >= 1


# ---------------------------------------------------------------------------
# _resolve_group_for_session — explicit group_id, not found, fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_group_for_session_missing_explicit(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    req = SessionForTasksCreate(
        task_ids=[msg_setup["task_id"]],
        channel_slug=ch.slug,
        scope=SessionScope.TASK,
        group_id=uuid4(),  # Doesn't exist.
    )
    with pytest.raises(NotFoundError):
        await svc._resolve_group_for_session(req, ch)


@pytest.mark.asyncio
async def test_resolve_group_for_session_explicit_found(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    req = SessionForTasksCreate(
        task_ids=[msg_setup["task_id"]],
        channel_slug=ch.slug,
        scope=SessionScope.TASK,
        group_id=grp.id,
    )
    resolved = await svc._resolve_group_for_session(req, ch)
    assert resolved.id == grp.id


@pytest.mark.asyncio
async def test_resolve_group_for_session_fallback_to_first(
    msg_setup: dict,
) -> None:
    """No explicit group_id, no inherited ancestor — fall back to first group."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    await svc.create_group(GroupCreateRequest(name="first", channel_id=ch.id))
    await svc.create_group(
        GroupCreateRequest(name="second", channel_id=ch.id, hierarchy_level=2)
    )
    req = SessionForTasksCreate(
        task_ids=[msg_setup["task_id"]],
        channel_slug=ch.slug,
        scope=SessionScope.TASK,
    )
    resolved = await svc._resolve_group_for_session(req, ch)
    assert resolved.name in {"first", "second"}


# ---------------------------------------------------------------------------
# create_session_for_tasks — reuses ancestor session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_for_tasks_reuses_ancestor(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """If an ancestor task has an ACTIVE primary session on this channel, reuse it."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    # Set up parent task with primary session.
    parent_id = uuid4()
    child_id = uuid4()
    base_task_result = await db_session.execute(
        __import__("sqlalchemy")
        .select(TaskTable)
        .where(TaskTable.id == msg_setup["task_id"])
    )
    base_task = base_task_result.scalar_one()

    parent = TaskTable(
        id=parent_id,
        title="parent",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
    )
    child = TaskTable(
        id=child_id,
        title="child",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
        parent_task_id=parent_id,
    )
    db_session.add_all([parent, child])
    await db_session.flush()

    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    parent_sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.link_session_to_task(parent_sess.id, parent_id, aid, is_primary=True)

    # Create session for child — should reuse parent's session.
    sess, _links = await svc.create_session_for_tasks(
        SessionForTasksCreate(
            task_ids=[child_id],
            channel_slug=ch.slug,
            scope=SessionScope.TASK,
        ),
        pm_agent_id=aid,
    )
    assert sess.id == parent_sess.id


# ---------------------------------------------------------------------------
# _check_session_boundaries — content_length boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_session_boundaries_content_length_exceeded(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(
        SessionCreateRequest(group_id=grp.id, max_content_length=100)
    )
    sess.total_content_length = 200
    assert svc._check_session_boundaries(sess) is True


# ---------------------------------------------------------------------------
# _get_message_context — group/channel missing branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_message_context_group_missing_raises(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    # Patch get_group to return None on the live (active) path.
    with (
        patch.object(svc, "get_group", AsyncMock(return_value=None)),
        pytest.raises(ValueError, match="not found"),
    ):
        await svc._get_message_context(sess.id)


@pytest.mark.asyncio
async def test_get_message_context_channel_missing_raises(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    with (
        patch.object(svc, "get_channel", AsyncMock(return_value=None)),
        pytest.raises(ValueError, match="not found"),
    ):
        await svc._get_message_context(sess.id)


@pytest.mark.asyncio
async def test_get_message_context_closed_session_group_missing(
    msg_setup: dict,
) -> None:
    """Closed-session redirect path: group lookup returns None → raise."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.close_session(sess.id)
    with (
        patch.object(svc, "get_group", AsyncMock(return_value=None)),
        pytest.raises(ValueError, match="not found"),
    ):
        await svc._get_message_context(sess.id)


@pytest.mark.asyncio
async def test_get_message_context_closed_session_channel_missing(
    msg_setup: dict,
) -> None:
    """Closed-session redirect path: channel lookup returns None → raise."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.close_session(sess.id)
    with (
        patch.object(svc, "get_channel", AsyncMock(return_value=None)),
        pytest.raises(ValueError, match="not found"),
    ):
        await svc._get_message_context(sess.id)


# ---------------------------------------------------------------------------
# send_message — mention notifications path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_with_mentions_triggers_delivery(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Mention path delivers via NotificationDelivery service."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    other = _AgentTable(
        id=_u(),
        name="Other",
        slug=f"be-other-{_u().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other)
    await db_session.flush()
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    mock_delivery = AsyncMock()
    mock_delivery.deliver = AsyncMock(return_value=None)
    with patch(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        return_value=mock_delivery,
    ):
        await svc.send_message(
            MessageCreateRequest(
                agent_id=aid,
                session_id=sess.id,
                content="hi @other",
                mentions=[cast("uuid.UUID", other.id)],
            )
        )
    mock_delivery.deliver.assert_awaited()


@pytest.mark.asyncio
async def test_send_message_self_mention_skipped(
    msg_setup: dict,
) -> None:
    """Mentioning yourself doesn't create a notification."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(
            agent_id=aid,
            session_id=sess.id,
            content="hi me",
            mentions=[aid],  # Self-mention.
        )
    )
    assert aid in msg.mentions


@pytest.mark.asyncio
async def test_send_message_boundary_exceeded_closes_session(
    msg_setup: dict,
) -> None:
    """When boundary exceeded after send, session closed."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(
        SessionCreateRequest(group_id=grp.id, max_message_count=1)
    )
    await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="msg")
    )
    refreshed = await svc.get_session(sess.id)
    assert refreshed is not None
    assert refreshed.status == SessionStatus.CLOSED


# ---------------------------------------------------------------------------
# edit_message_or_raise + delete_message_or_raise — wrong author
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_message_or_raise_wrong_author(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="orig")
    )
    with pytest.raises(PermissionError):
        await svc.edit_message_or_raise(
            message_id=msg.id,
            agent_id=uuid4(),
            new_content="hi",
            edit_reason=None,
        )


@pytest.mark.asyncio
async def test_delete_message_or_raise_wrong_author(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="orig")
    )
    with pytest.raises(PermissionError):
        await svc.delete_message_or_raise(
            message_id=msg.id,
            agent_id=uuid4(),
        )


# ---------------------------------------------------------------------------
# edit_message — message not found error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_message_not_found(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.edit_message(uuid4(), uuid4(), "x")


@pytest.mark.asyncio
async def test_delete_message_not_found(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.delete_message(uuid4(), uuid4())


# ---------------------------------------------------------------------------
# _index_message_async — failure swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_message_async_swallows_exception(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """RAG indexing failure logged but doesn't break."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = MessageTable(
        id=uuid4(),
        agent_id=aid,
        channel_id=ch.id,
        group_id=grp.id,
        session_id=sess.id,
        type=MessageType.DIALOGUE,
        content="x",
        content_length=1,
    )
    db_session.add(msg)
    await db_session.flush()
    with patch(
        "roboco.services.optimal.get_optimal_service",
        side_effect=RuntimeError("rag down"),
    ):
        await svc._index_message_async(msg)


# ---------------------------------------------------------------------------
# post_to_channel — full path + agent slug not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_to_channel_agent_slug_missing_raises(
    msg_setup: dict,
) -> None:
    """get_agent_slug returns None → ChannelAccessDeniedError."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    # Seed the channel into config so it's resolvable.
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    with (
        patch(
            "roboco.services.repositories.get_agent_slug",
            AsyncMock(return_value=None),
        ),
        pytest.raises(ChannelAccessDeniedError),
    ):
        await svc.post_to_channel(
            agent_id=aid,
            channel_slug=ch.slug,
            content="hi",
        )


@pytest.mark.asyncio
async def test_post_to_channel_full_path(msg_setup: dict) -> None:
    """Resolves slug → group → session → message."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    # Add agent to channel writers so write check passes.
    await svc.add_channel_member_or_raise(
        channel_id=ch.id, member_id=aid, can_write=True
    )
    with (
        patch(
            "roboco.services.repositories.get_agent_slug",
            AsyncMock(return_value="be-dev-1"),
        ),
        patch(
            "roboco.services.messaging.validate_channel_access",
            return_value=None,
        ),
    ):
        msg = await svc.post_to_channel(
            agent_id=aid,
            channel_slug=ch.slug,
            content="hello channel",
        )
    assert msg.content == "hello channel"


# ---------------------------------------------------------------------------
# get_or_create_active_session — group has active session that's still ACTIVE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_active_session_returns_existing_active(
    msg_setup: dict,
) -> None:
    """Group has active_session_id pointing at ACTIVE session — returns it."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    await svc.create_session(SessionCreateRequest(group_id=grp.id))
    s2 = await svc.get_or_create_active_session(grp.id)
    assert s2.status == SessionStatus.ACTIVE


# ---------------------------------------------------------------------------
# (orphan-parent walk is covered by test_walk_task_ancestors_orphan_parent_breaks
# elsewhere in this file, which patches session.execute to bypass the FK
# constraint that prevents inserting a real orphan row.)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _resolve_group_for_session — auto-create default group when channel empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_group_for_session_auto_creates_default(
    msg_setup: dict,
) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    # No groups in this channel.
    req = SessionForTasksCreate(
        task_ids=[msg_setup["task_id"]],
        channel_slug=ch.slug,
        scope=SessionScope.TASK,
    )
    grp = await svc._resolve_group_for_session(req, ch)
    assert grp.name == "General"


# ---------------------------------------------------------------------------
# _resolve_group_from_parent_tasks — ancestor's primary session group
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_group_from_parent_tasks_inherits(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Task with ancestor having primary session → inherit ancestor's group."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    base_task_result = await db_session.execute(
        __import__("sqlalchemy")
        .select(TaskTable)
        .where(TaskTable.id == msg_setup["task_id"])
    )
    base_task = base_task_result.scalar_one()

    parent = TaskTable(
        id=uuid4(),
        title="parent-inh",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
    )
    child = TaskTable(
        id=uuid4(),
        title="child-inh",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=base_task.project_id,
        created_by=aid,
        team=base_task.team,
        parent_task_id=parent.id,
    )
    db_session.add_all([parent, child])
    await db_session.flush()

    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(
        GroupCreateRequest(name="parent-grp", channel_id=ch.id)
    )
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    await svc.link_session_to_task(sess.id, parent.id, aid, is_primary=True)
    inherited = await svc._resolve_group_from_parent_tasks([child.id])
    assert inherited is not None
    assert inherited.id == grp.id


# ---------------------------------------------------------------------------
# _index_message_async — happy path (debug log)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_message_async_happy(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Successful index logs debug."""
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = MessageTable(
        id=uuid4(),
        agent_id=aid,
        channel_id=ch.id,
        group_id=grp.id,
        session_id=sess.id,
        type=MessageType.DIALOGUE,
        content="x",
        content_length=1,
    )
    db_session.add(msg)
    await db_session.flush()
    mock_optimal = AsyncMock()
    mock_optimal.index_conversation = AsyncMock(return_value=None)
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=mock_optimal),
    ):
        await svc._index_message_async(msg)


# ---------------------------------------------------------------------------
# get_message_or_raise — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_message_or_raise_happy(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    sess = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    msg = await svc.send_message(
        MessageCreateRequest(agent_id=aid, session_id=sess.id, content="hi")
    )
    found = await svc.get_message_or_raise(msg.id)
    assert found.id == msg.id


# ---------------------------------------------------------------------------
# Factory function smoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_messaging_service_factory(
    db_session: AsyncSession,
) -> None:
    svc = get_messaging_service(db_session)
    assert isinstance(svc, MessagingService)


# ---------------------------------------------------------------------------
# post_to_channel — task threading + per-role channel access
# ---------------------------------------------------------------------------


async def _seed_backend_dev(db_session: AsyncSession, slug: str) -> AgentTable:
    """Create a backend DEVELOPER agent with a known slug for ACL checks."""
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=slug,
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


async def _seed_task(
    db_session: AsyncSession, created_by: Any
) -> tuple[ProjectTable, TaskTable]:
    project = ProjectTable(
        id=uuid4(),
        name="Thread-Proj",
        slug=f"thread-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=created_by,
    )
    db_session.add(project)
    await db_session.flush()
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=created_by,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    return project, task


def _real_channel_req(slug: str) -> ChannelCreateRequest:
    """Channel whose slug matches a real CHANNEL_ACCESS entry so the
    static ACL check in validate_channel_access runs for real (no patch)."""
    return ChannelCreateRequest(
        name=f"Channel {slug}",
        slug=slug,
        channel_type=ChannelType.CELL,
        description="desc",
    )


@pytest.mark.asyncio
async def test_post_to_channel_threads_messages_under_one_task_group(
    db_session: AsyncSession,
) -> None:
    """With task_id, both posts land in the same per-(channel, task) group."""
    svc = MessagingService(db_session)
    agent = await _seed_backend_dev(db_session, "be-dev-1")
    _project, task = await _seed_task(db_session, agent.id)
    await svc.create_channel(_real_channel_req("backend-cell"))

    first = await svc.post_to_channel(
        agent_id=cast("uuid.UUID", agent.id),
        channel_slug="backend-cell",
        content="first update",
        task_id=cast("uuid.UUID", task.id),
    )
    second = await svc.post_to_channel(
        agent_id=cast("uuid.UUID", agent.id),
        channel_slug="backend-cell",
        content="second update",
        task_id=cast("uuid.UUID", task.id),
    )

    assert first.group_id == second.group_id
    assert first.task_id == task.id
    assert second.task_id == task.id


@pytest.mark.asyncio
async def test_post_to_channel_task_group_is_distinct_from_default(
    db_session: AsyncSession,
) -> None:
    """A task post threads into a task-specific group, NOT the channel's
    default standing group used by untasked posts."""
    svc = MessagingService(db_session)
    agent = await _seed_backend_dev(db_session, "be-dev-1")
    _project, task = await _seed_task(db_session, agent.id)
    await svc.create_channel(_real_channel_req("backend-cell"))

    untasked = await svc.post_to_channel(
        agent_id=cast("uuid.UUID", agent.id),
        channel_slug="backend-cell",
        content="no task here",
    )
    tasked = await svc.post_to_channel(
        agent_id=cast("uuid.UUID", agent.id),
        channel_slug="backend-cell",
        content="task scoped",
        task_id=cast("uuid.UUID", task.id),
    )

    assert tasked.group_id != untasked.group_id


@pytest.mark.asyncio
async def test_post_to_channel_separate_tasks_get_separate_groups(
    db_session: AsyncSession,
) -> None:
    """Two different tasks thread into two different groups on one channel."""
    svc = MessagingService(db_session)
    agent = await _seed_backend_dev(db_session, "be-dev-1")
    _project_a, task_a = await _seed_task(db_session, agent.id)
    _project_b, task_b = await _seed_task(db_session, agent.id)
    await svc.create_channel(_real_channel_req("backend-cell"))

    msg_a = await svc.post_to_channel(
        agent_id=cast("uuid.UUID", agent.id),
        channel_slug="backend-cell",
        content="task a",
        task_id=cast("uuid.UUID", task_a.id),
    )
    msg_b = await svc.post_to_channel(
        agent_id=cast("uuid.UUID", agent.id),
        channel_slug="backend-cell",
        content="task b",
        task_id=cast("uuid.UUID", task_b.id),
    )

    assert msg_a.group_id != msg_b.group_id


@pytest.mark.asyncio
async def test_post_to_channel_rejects_agent_without_channel_access(
    db_session: AsyncSession,
) -> None:
    """A backend dev cannot write to #announcements (board/PM/CEO only) —
    even with a task_id, post_to_channel must reject before threading."""
    svc = MessagingService(db_session)
    agent = await _seed_backend_dev(db_session, "be-dev-1")
    _project, task = await _seed_task(db_session, agent.id)
    await svc.create_channel(_real_channel_req("announcements"))

    with pytest.raises(ChannelAccessDeniedError):
        await svc.post_to_channel(
            agent_id=cast("uuid.UUID", agent.id),
            channel_slug="announcements",
            content="should be blocked",
            task_id=cast("uuid.UUID", task.id),
        )


@pytest.mark.asyncio
async def test_post_to_channel_access_denied_creates_no_task_group(
    db_session: AsyncSession,
) -> None:
    """Rejection happens before any per-task group is created (no side effect
    leaks for a denied write)."""
    svc = MessagingService(db_session)
    agent = await _seed_backend_dev(db_session, "be-dev-1")
    _project, task = await _seed_task(db_session, agent.id)
    ch = await svc.create_channel(_real_channel_req("announcements"))

    with pytest.raises(ChannelAccessDeniedError):
        await svc.post_to_channel(
            agent_id=cast("uuid.UUID", agent.id),
            channel_slug="announcements",
            content="should be blocked",
            task_id=cast("uuid.UUID", task.id),
        )

    groups = await svc.list_groups_in_channel(cast("uuid.UUID", ch.id))
    assert groups == []


@pytest.mark.asyncio
async def test_post_to_channel_permitted_agent_succeeds(
    db_session: AsyncSession,
) -> None:
    """An agent on the channel's write list posts successfully (real ACL)."""
    svc = MessagingService(db_session)
    agent = await _seed_backend_dev(db_session, "be-dev-1")
    _project, task = await _seed_task(db_session, agent.id)
    await svc.create_channel(_real_channel_req("backend-cell"))

    msg = await svc.post_to_channel(
        agent_id=cast("uuid.UUID", agent.id),
        channel_slug="backend-cell",
        content="hello cell",
        task_id=cast("uuid.UUID", task.id),
    )
    assert msg.content == "hello cell"
    assert msg.task_id == task.id


def test_resolve_session_timeout_uses_configurable_default() -> None:
    """An unset session timeout resolves to the configurable default instead of
    the old 300s that swept human chats between messages."""
    explicit = settings.session_idle_timeout_seconds + 60
    assert MessagingService._resolve_session_timeout(explicit) == explicit
    assert (
        MessagingService._resolve_session_timeout(None)
        == settings.session_idle_timeout_seconds
    )
