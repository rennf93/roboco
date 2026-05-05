"""MessagingService coverage — channels, groups, sessions, task links."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    ChannelType,
    SessionStatus,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.messaging import (
    ChannelCreateRequest,
    GroupCreateRequest,
    SessionCreateRequest,
)
from roboco.services.base import NotFoundError
from roboco.services.messaging import MessagingService

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
async def test_archive_channel(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    archived = await svc.archive_channel(ch.id)
    assert archived.is_archived is True


@pytest.mark.asyncio
async def test_add_and_remove_channel_member(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    aid = msg_setup["agent_id"]
    updated = await svc.add_channel_member(ch.id, aid, can_write=True)
    assert aid in updated.members
    assert aid in updated.writers
    removed = await svc.remove_channel_member(ch.id, aid)
    assert aid not in removed.members


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
    await svc.add_channel_member(ch.id, aid)
    await svc.remove_channel_member_or_raise(channel_id=ch.id, member_id=aid)


@pytest.mark.asyncio
async def test_list_channels_for_agent(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    await svc.add_channel_member(ch.id, aid)
    channels = await svc.list_channels_for_agent(aid)
    assert ch.id in {c.id for c in channels}


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
    assert len(groups) >= 2


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
async def test_create_session_replaces_active(msg_setup: dict) -> None:
    """Second create_session against same group still produces an ACTIVE session."""
    svc = msg_setup["svc"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    grp = await svc.create_group(GroupCreateRequest(name="g1", channel_id=ch.id))
    await svc.create_session(SessionCreateRequest(group_id=grp.id))
    second = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    assert second.status == SessionStatus.ACTIVE


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
    with pytest.raises(NotFoundError):
        await svc.link_session_to_task(uuid4(), tid, aid)


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
async def test_archive_channel_missing_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.archive_channel(uuid4())


@pytest.mark.asyncio
async def test_add_channel_member_missing_raises(msg_setup: dict) -> None:
    svc = msg_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.add_channel_member(uuid4(), uuid4())


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


from roboco.models.messaging import MessageCreateRequest  # noqa: E402


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
    from roboco.services.messaging import ApiSessionCreate

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
    from roboco.services.messaging import ApiSessionCreate

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
    from roboco.models.session import SessionForTasksCreate, SessionScope

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
    from roboco.models.session import SessionForTasksCreate, SessionScope

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
    from roboco.services.messaging import ApiSessionCreate

    svc = msg_setup["svc"]
    aid = msg_setup["agent_id"]
    ch = await svc.create_channel(_channel_req(uuid4().hex[:6]))
    # Add agent to writers list.
    await svc.add_channel_member(ch.id, aid, can_write=True)
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
    await svc.add_channel_member(ch.id, aid)
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
    await svc.add_channel_member(ch.id, aid)
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
    from datetime import UTC, datetime, timedelta

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
async def test_get_messages_with_filters(msg_setup: dict) -> None:
    """get_messages with before/after/type filters."""
    from datetime import UTC, datetime, timedelta

    from roboco.models import MessageType

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
        before=datetime.now(UTC) + timedelta(hours=1),
        after=cutoff,
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
    msgs, has_more = await svc.get_messages(sess.id, limit=2)
    assert len(msgs) == 2
    assert has_more is True


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
    sess2 = await svc.create_session(SessionCreateRequest(group_id=grp.id))
    with pytest.raises(ValueError, match="not found in this session"):
        await svc._validate_reply_target(msg.id, sess2.id)


@pytest.mark.asyncio
async def test_walk_task_ancestors_with_parent(
    msg_setup: dict, db_session: AsyncSession
) -> None:
    """Smoke-test ancestry walk via direct DB seeding."""
    from roboco.db.tables import TaskTable
    from roboco.models.base import TaskNature, TaskStatus, TaskType

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
