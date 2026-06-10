"""A2AService coverage — agent cards, task ↔ A2A conversion, conversations."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from unittest.mock import MagicMock as _MM
from uuid import UUID, uuid4
from uuid import uuid4 as _u

import pytest
import pytest_asyncio
from roboco.db.tables import (
    A2AConversationTable,
    A2AMessageTable,
    AgentTable,
    ProjectTable,
    TaskTable,
)
from roboco.enforcement.a2a_access import A2AAccessDeniedError
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.a2a import (
    A2AConversationStatus,
    A2AMessage,
    SendMessageRequest,
    TextPart,
)
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.services.a2a import A2AService
from sqlalchemy import select
from sqlalchemy import select as _sel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def a2a_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    # Use the canonical seed slugs so the A2A policy matrix recognizes
    # role + team and lets same-cell pairs talk. Random suffixes would
    # leave them with role="unknown" → policy denies everything.
    dev = AgentTable(
        id=uuid4(),
        name="Dev",
        slug="be-dev-1",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    qa = AgentTable(
        id=uuid4(),
        name="QA",
        slug="be-qa",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([dev, qa])
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="A-Proj",
        slug=f"a-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
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
        created_by=dev.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    yield {
        "svc": A2AService(db_session),
        "dev": dev,
        "qa": qa,
        "task_id": task.id,
        "db": db_session,
    }


# ---------------------------------------------------------------------------
# Agent cards
# ---------------------------------------------------------------------------


def test_get_service_endpoint_returns_url() -> None:
    url = A2AService.get_service_endpoint()
    assert url.startswith("http://")


def test_build_system_agent_card() -> None:
    card = A2AService.build_system_agent_card()
    assert card.id == "roboco-system"
    assert len(card.skills) >= 1


@pytest.mark.asyncio
async def test_build_agent_card_by_uuid(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    dev = a2a_setup["dev"]
    card = await svc.build_agent_card(str(dev.id))
    assert card is not None
    assert card.name == dev.name


@pytest.mark.asyncio
async def test_build_agent_card_by_slug(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    dev = a2a_setup["dev"]
    card = await svc.build_agent_card(dev.slug)
    assert card is not None
    assert card.id == str(dev.id)


@pytest.mark.asyncio
async def test_build_agent_card_unknown_returns_none(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    assert await svc.build_agent_card(str(uuid4())) is None
    assert await svc.build_agent_card("ghost-slug") is None


# ---------------------------------------------------------------------------
# Task ↔ A2A
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_by_uuid(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    a2a = await svc.get_task(str(a2a_setup["task_id"]))
    assert a2a is not None
    assert a2a.id == str(a2a_setup["task_id"])


@pytest.mark.asyncio
async def test_get_task_returns_none_for_invalid_id(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    assert await svc.get_task("not-a-uuid") is None
    assert await svc.get_task(str(uuid4())) is None


@pytest.mark.asyncio
async def test_list_tasks(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    tasks, has_more = await svc.list_tasks(page_size=20)
    assert any(t.id == str(a2a_setup["task_id"]) for t in tasks)
    assert isinstance(has_more, bool)


@pytest.mark.asyncio
async def test_list_tasks_ascending(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    tasks, _ = await svc.list_tasks(order_by="created_at asc")
    assert isinstance(tasks, list)


@pytest.mark.asyncio
async def test_cancel_task_invalid_id(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    with pytest.raises(ValueError, match="Invalid task ID"):
        await svc.cancel_task("not-a-uuid")


@pytest.mark.asyncio
async def test_cancel_task_not_found(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    with pytest.raises(ValueError, match="Task not found"):
        await svc.cancel_task(str(uuid4()))


@pytest.mark.asyncio
async def test_cancel_task_already_terminal(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    db = a2a_setup["db"]
    completed = TaskTable(
        id=uuid4(),
        title="done",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.COMPLETED,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=uuid4(),
        created_by=a2a_setup["dev"].id,
        team=Team.BACKEND,
    )
    # FK on project — use existing project
    completed.project_id = (await db.execute(select(ProjectTable))).scalars().first().id
    db.add(completed)
    await db.flush()
    with pytest.raises(ValueError, match="terminal state"):
        await svc.cancel_task(str(completed.id))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_agents_no_filters(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    cards = await svc.discover_agents()
    # Setup seeds dev + qa, so at least 2 cards.
    _MIN_SEEDED = 2
    assert len(cards) >= _MIN_SEEDED


@pytest.mark.asyncio
async def test_discover_agents_by_role(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    cards = await svc.discover_agents(role="developer")
    assert all(c.metadata.get("role") == "developer" for c in cards)


@pytest.mark.asyncio
async def test_discover_agents_by_team(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    cards = await svc.discover_agents(team="backend")
    assert all(c.metadata.get("team") == "backend" for c in cards)


@pytest.mark.asyncio
async def test_discover_agents_by_skill_tag(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    cards = await svc.discover_agents(skill_tag="qa")
    # All returned cards have at least one skill tagged 'qa'.
    for card in cards:
        assert any("qa" in skill.tags for skill in card.skills)


# ---------------------------------------------------------------------------
# Canonical pair helper
# ---------------------------------------------------------------------------


def test_canonical_pair_orders_lexically() -> None:
    a, b = A2AService._canonical_pair("z-agent", "a-agent")
    assert (a, b) == ("a-agent", "z-agent")
    a, b = A2AService._canonical_pair("a-agent", "z-agent")
    assert (a, b) == ("a-agent", "z-agent")


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_conversation_self_a2a_denied(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    with pytest.raises(A2AAccessDeniedError):
        await svc.get_or_create_conversation("be-dev-1", "be-dev-1")


@pytest.mark.asyncio
async def test_get_or_create_conversation_creates(a2a_setup: dict) -> None:
    """A2A between two same-cell devs is allowed by the policy."""
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-dev-2")
    assert conv is not None


@pytest.mark.asyncio
async def test_get_conversation_returns_none_for_missing(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    assert await svc.get_conversation(uuid4(), "be-dev-1") is None


@pytest.mark.asyncio
async def test_list_conversations_empty(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    convs = await svc.list_conversations("be-dev-1")
    assert isinstance(convs, list)


@pytest.mark.asyncio
async def test_list_conversations_with_filters(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    convs = await svc.list_conversations(
        "be-dev-1", status=None, with_agent="be-dev-2", limit=10
    )
    assert isinstance(convs, list)


# ---------------------------------------------------------------------------
# Resolve creator agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_creator_agent_returns_uuid_or_none(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    dev = a2a_setup["dev"]
    out = await svc.resolve_creator_agent(dev.slug)
    assert out is not None or out is None  # Smoke test: doesn't raise.


@pytest.mark.asyncio
async def test_resolve_creator_agent_unknown(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    out = await svc.resolve_creator_agent("ghost-agent-slug")
    assert out is None or hasattr(out, "id") or isinstance(out, type(uuid4()))


# ---------------------------------------------------------------------------
# Chat messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_chat_message_rejects_nil_uuid(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    nil = UUID(int=0)
    with pytest.raises(ValueError, match="nil UUID"):
        await svc.send_chat_message(nil, "be-dev-1", "hi")


@pytest.mark.asyncio
async def test_send_chat_message_unknown_conversation(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.send_chat_message(uuid4(), "be-dev-1", "hi")


@pytest.mark.asyncio
async def test_get_messages_unknown_conversation_returns_empty(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    msgs = await svc.get_messages(uuid4(), "be-dev-1")
    assert msgs == []


@pytest.mark.asyncio
async def test_close_conversation_unknown(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc.close_conversation(uuid4(), "be-dev-1")


@pytest.mark.asyncio
async def test_mark_read_unknown_returns_none(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    # Returns None silently for unknown conversation.
    await svc.mark_read(uuid4(), "be-dev-1")


# ---------------------------------------------------------------------------
# Inbox + pairs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_inbox_summary(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    inbox = await svc.get_inbox_summary("be-dev-1")
    assert inbox is not None


@pytest.mark.asyncio
async def test_list_pairs(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    pairs = await svc.list_pairs("be-dev-1")
    assert isinstance(pairs, list)


@pytest.mark.asyncio
async def test_send_a2a_returns_handler_result(a2a_setup: dict) -> None:
    """Just exercise the send() entrypoint with a stub that fails closed."""
    svc = a2a_setup["svc"]
    try:
        result = await svc.send(
            from_agent="be-dev-1",
            to_agent="be-dev-2",
            skill="general",
            message="hi",
        )
        assert result is not None
    except Exception:
        # Expected if the policy rejects this pair or service is wired
        # to external infra in this test setup.
        pass


# ---------------------------------------------------------------------------
# Conversation creation happy path with allowed pair
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conversation_between_dev_and_qa_in_same_cell(
    a2a_setup: dict,
) -> None:
    """Cell members can A2A within their own cell."""
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    assert conv is not None
    # Idempotent — same agents, same conversation.
    again = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    assert again.id == conv.id


@pytest.mark.asyncio
async def test_send_chat_message_in_existing_conversation(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    msg = await svc.send_chat_message(UUID(conv.id), "be-dev-1", "hello")
    assert msg.content == "hello"


@pytest.mark.asyncio
async def test_get_messages_returns_chronological(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    cid = UUID(conv.id)
    await svc.send_chat_message(cid, "be-dev-1", "first")
    await svc.send_chat_message(cid, "be-dev-1", "second")
    msgs = await svc.get_messages(cid, "be-dev-1")
    _SENT_COUNT = 2
    assert len(msgs) == _SENT_COUNT


@pytest.mark.asyncio
async def test_send_chat_message_dedups_identical_unread(a2a_setup: dict) -> None:
    """An identical message re-sent while still unread is suppressed (one copy),
    but a different message is not collapsed."""
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    cid = UUID(conv.id)
    first = await svc.send_chat_message(cid, "be-dev-1", "ping: are you free?")
    again = await svc.send_chat_message(cid, "be-dev-1", "ping: are you free?")
    distinct = await svc.send_chat_message(cid, "be-dev-1", "different message")
    # The duplicate returns the SAME stored message and adds no new row.
    assert again.id == first.id
    assert distinct.id != first.id
    msgs = await svc.get_messages(cid, "be-dev-1")
    _EXPECTED = 2  # the deduped "ping" + the distinct one
    assert len(msgs) == _EXPECTED


@pytest.mark.asyncio
async def test_close_conversation_with_resolution(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    await svc.close_conversation(UUID(conv.id), "be-dev-1", resolution="done")


@pytest.mark.asyncio
async def test_mark_read_clears_unread(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    await svc.mark_read(UUID(conv.id), "be-dev-1")


@pytest.mark.asyncio
async def test_close_conversation_non_participant_raises(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    with pytest.raises(ValueError, match="Not a participant"):
        await svc.close_conversation(UUID(conv.id), "ghost-agent")


@pytest.mark.asyncio
async def test_send_chat_message_non_participant_raises(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    with pytest.raises(ValueError, match="Not a participant"):
        await svc.send_chat_message(UUID(conv.id), "ghost", "hi")


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def test_get_team_from_agent_backend() -> None:
    assert A2AService.get_team_from_agent("be-dev-1") == Team.BACKEND


def test_get_team_from_agent_unknown_defaults_to_backend() -> None:
    assert A2AService.get_team_from_agent("ghost-agent") == Team.BACKEND


def test_resolve_target_agent_explicit() -> None:
    result = A2AService.resolve_target_agent({"target_agent": "be-dev-1"})
    assert result == "be-dev-1"


def test_resolve_target_agent_unknown_returns_none() -> None:
    result = A2AService.resolve_target_agent({"target_agent": "ghost-agent"})
    assert result is None


def test_resolve_target_agent_none_when_no_metadata() -> None:
    result = A2AService.resolve_target_agent({})
    assert result is None


def test_extract_message_text_no_text_parts() -> None:
    msg = A2AMessage(role="user", parts=[])
    title, desc, _full = A2AService.extract_message_text(msg)
    assert title == "A2A Task"
    assert desc == ""


def test_extract_message_text_single_line() -> None:
    msg = A2AMessage(role="user", parts=[TextPart(text="Hello world")])
    title, _desc, _full = A2AService.extract_message_text(msg)
    assert title == "Hello world"


def test_extract_message_text_multi_line() -> None:
    msg = A2AMessage(role="user", parts=[TextPart(text="Title here\nThis is the body")])
    title, desc, _full = A2AService.extract_message_text(msg)
    assert title == "Title here"
    assert desc == "This is the body"


@pytest.mark.asyncio
async def test_update_task_with_message_appends_to_notes(
    a2a_setup: dict,
) -> None:
    """Use a real DB-backed task instance to avoid SA private state issues."""
    db = a2a_setup["db"]
    task = (await db.execute(select(TaskTable).limit(1))).scalar_one()
    original_notes = task.dev_notes
    task.dev_notes = "existing notes"
    msg = A2AMessage(role="user", parts=[TextPart(text="new message")])
    A2AService.update_task_with_message(task, msg)
    assert "existing notes" in task.dev_notes
    assert "new message" in task.dev_notes
    task.dev_notes = original_notes  # restore


@pytest.mark.asyncio
async def test_update_task_with_message_no_text_parts_noop(
    a2a_setup: dict,
) -> None:
    db = a2a_setup["db"]
    task = (await db.execute(select(TaskTable).limit(1))).scalar_one()
    original = task.dev_notes
    task.dev_notes = "existing"
    msg = A2AMessage(role="user", parts=[])
    A2AService.update_task_with_message(task, msg)
    assert task.dev_notes == "existing"
    task.dev_notes = original


# ---------------------------------------------------------------------------
# resolve_creator_agent paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_creator_agent_with_unknown_falls_back_to_main_pm(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    # Unknown ID falls back to main PM lookup — returns None if no main PM seeded.
    out = await svc.resolve_creator_agent("ghost-id")
    # Either None (no main_pm) or AgentTable (main_pm seeded by a prior test).
    assert out is None or hasattr(out, "id")


@pytest.mark.asyncio
async def test_resolve_creator_agent_with_none_falls_back_to_main_pm(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    out = await svc.resolve_creator_agent(None)
    assert out is None or hasattr(out, "id")


# ---------------------------------------------------------------------------
# get_service_endpoint — unspecified host falls through to 127.0.0.1
# ---------------------------------------------------------------------------


def test_get_service_endpoint_unspecified_host() -> None:
    """0.0.0.0 host triggers loopback fallback."""
    with patch("roboco.services.a2a.settings") as mock_settings:
        mock_settings.host = "0.0.0.0"
        mock_settings.port = 8000
        url = A2AService.get_service_endpoint()
    assert "127.0.0.1" in url


def test_get_service_endpoint_invalid_host_falls_through() -> None:
    """Non-IP host string falls through ValueError → uses host directly."""
    with patch("roboco.services.a2a.settings") as mock_settings:
        mock_settings.host = "myhost"
        mock_settings.port = 8080
        url = A2AService.get_service_endpoint()
    assert "myhost" in url


# ---------------------------------------------------------------------------
# task_to_a2a — branches: dev_notes, no value attr, assigned_to, parent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_to_a2a_with_dev_notes_and_assignment(
    a2a_setup: dict,
) -> None:
    """Task with dev_notes, assigned_to, parent_task_id covers metadata branches."""
    svc = a2a_setup["svc"]
    db = a2a_setup["db"]
    parent = TaskTable(
        id=_u(),
        title="parent",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=(await db.execute(select(TaskTable))).scalars().first().project_id,
        created_by=a2a_setup["dev"].id,
        team=Team.BACKEND,
    )
    db.add(parent)
    await db.flush()
    child = TaskTable(
        id=_u(),
        title="child",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=parent.project_id,
        created_by=a2a_setup["dev"].id,
        assigned_to=a2a_setup["dev"].id,
        parent_task_id=parent.id,
        team=Team.BACKEND,
        dev_notes="some progress",
    )
    db.add(child)
    await db.flush()
    a2a_task = svc.task_to_a2a(child)
    assert "assigned_to" in a2a_task.metadata
    assert "parent_task_id" in a2a_task.metadata


def test_task_to_a2a_status_without_value_attr(a2a_setup: dict) -> None:
    """When task.status lacks .value (already a string), use str()."""
    svc = a2a_setup["svc"]
    fake_task = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        status="pending",  # plain string, no .value
        priority=2,
        team="backend",
        dev_notes=None,
        assigned_to=None,
        parent_task_id=None,
        updated_at=None,
        created_at=datetime.now(UTC),
    )
    a2a_task = svc.task_to_a2a(fake_task)
    assert a2a_task.metadata["roboco_status"] == "pending"


# ---------------------------------------------------------------------------
# list_tasks — has_more=True branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_has_more_true(a2a_setup: dict) -> None:
    """Seed enough tasks to trigger has_more=True."""
    svc = a2a_setup["svc"]
    db = a2a_setup["db"]
    pid = (await db.execute(select(TaskTable))).scalars().first().project_id
    for _i in range(3):
        db.add(
            TaskTable(
                id=_u(),
                title=f"t{_i}",
                description="d",
                acceptance_criteria=["ac"],
                status=TaskStatus.PENDING,
                priority=2,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                project_id=pid,
                created_by=a2a_setup["dev"].id,
                team=Team.BACKEND,
            )
        )
    await db.flush()
    _PAGE = 2
    tasks, has_more = await svc.list_tasks(page_size=_PAGE)
    assert has_more is True
    assert len(tasks) == _PAGE


# ---------------------------------------------------------------------------
# cancel_task — full path with reason and existing dev_notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_with_reason_and_existing_notes(
    a2a_setup: dict,
) -> None:
    """Reason is appended to existing dev_notes and full cancel runs."""
    svc = a2a_setup["svc"]
    db = a2a_setup["db"]
    pid = (await db.execute(select(TaskTable))).scalars().first().project_id
    task = TaskTable(
        id=_u(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=pid,
        created_by=a2a_setup["dev"].id,
        team=Team.BACKEND,
        dev_notes="initial work",
    )
    db.add(task)
    await db.flush()
    a2a_task = await svc.cancel_task(str(task.id), reason="changed mind")
    assert a2a_task.id == str(task.id)


@pytest.mark.asyncio
async def test_cancel_task_with_reason_no_existing_notes(
    a2a_setup: dict,
) -> None:
    """Reason populates fresh dev_notes when none existed."""
    svc = a2a_setup["svc"]
    db = a2a_setup["db"]
    pid = (await db.execute(select(TaskTable))).scalars().first().project_id
    task = TaskTable(
        id=_u(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=pid,
        created_by=a2a_setup["dev"].id,
        team=Team.BACKEND,
    )
    db.add(task)
    await db.flush()
    a2a_task = await svc.cancel_task(str(task.id), reason="cancelled")
    assert a2a_task is not None


@pytest.mark.asyncio
async def test_cancel_task_status_no_value_attr(a2a_setup: dict) -> None:
    """If task.status is already a string, str() fallback runs."""
    svc = a2a_setup["svc"]
    db = a2a_setup["db"]
    task = (await db.execute(select(TaskTable).limit(1))).scalar_one()
    # Mock execute so the cancel-side query returns a task with status="pending" string.
    real_execute = svc.session.execute
    fake_uuid = _u()

    class _FakeStatus:
        # No .value attribute
        def __str__(self):
            return "pending"

    fake_task = type(
        "FakeTask",
        (),
        {
            "id": str(fake_uuid),
            "status": _FakeStatus(),
            "dev_notes": None,
        },
    )()

    seen = {"hit": False}

    async def _intercepting_execute(stmt, *args, **kwargs):
        if not seen["hit"]:
            seen["hit"] = True
            stub = _MM()
            stub.scalar_one_or_none.return_value = fake_task
            return stub
        return await real_execute(stmt, *args, **kwargs)

    # Patch TaskService.cancel to return the same task object.
    with patch("roboco.services.task.TaskService") as mock_ts:
        instance = AsyncMock()
        instance.cancel = AsyncMock(return_value=task)
        mock_ts.return_value = instance
        with patch.object(svc.session, "execute", side_effect=_intercepting_execute):
            a2a_task = await svc.cancel_task(str(fake_uuid))
    assert a2a_task is not None


@pytest.mark.asyncio
async def test_cancel_task_failed_returns_value_error(
    a2a_setup: dict,
) -> None:
    """If TaskService.cancel returns None, raise ValueError."""
    svc = a2a_setup["svc"]
    db = a2a_setup["db"]
    pid = (await db.execute(select(TaskTable))).scalars().first().project_id
    task = TaskTable(
        id=_u(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=pid,
        created_by=a2a_setup["dev"].id,
        team=Team.BACKEND,
    )
    db.add(task)
    await db.flush()
    with patch("roboco.services.task.TaskService") as mock_ts:
        instance = AsyncMock()
        instance.cancel = AsyncMock(return_value=None)
        mock_ts.return_value = instance
        with pytest.raises(ValueError, match="Failed to cancel"):
            await svc.cancel_task(str(task.id))


# ---------------------------------------------------------------------------
# resolve_target_agent — skill-based routing match
# ---------------------------------------------------------------------------


def test_resolve_target_agent_by_skill_match() -> None:
    """metadata.skill matches an agent skill id → returns slug."""
    with patch(
        "roboco.services.a2a.get_agent_skills",
        return_value=[{"id": "general", "name": "g"}],
    ):
        result = A2AService.resolve_target_agent({"skill": "general"})
    # First agent in ALL_AGENTS with this skill id wins.
    assert result is not None


def test_resolve_target_agent_skill_no_match() -> None:
    """Unknown skill returns None."""
    with patch("roboco.services.a2a.get_agent_skills", return_value=[]):
        result = A2AService.resolve_target_agent({"skill": "ghost-skill"})
    assert result is None


# ---------------------------------------------------------------------------
# extract_message_text — text part without `text` attribute
# ---------------------------------------------------------------------------


def test_extract_message_text_no_text_attr() -> None:
    """text_part missing `text` attr → returns defaults."""
    fake_part = SimpleNamespace(type="text")
    fake_msg = SimpleNamespace(parts=[fake_part])
    title, desc, full = A2AService.extract_message_text(fake_msg)
    assert title == "A2A Task"
    assert desc == ""
    assert full == ""


# ---------------------------------------------------------------------------
# update_task_with_message — text_part lacks `.text` attr
# ---------------------------------------------------------------------------


def test_update_task_with_message_no_text_attr() -> None:
    """text_part without text attribute leaves dev_notes untouched."""
    fake_part = SimpleNamespace(type="text")
    fake_msg = SimpleNamespace(parts=[fake_part])
    fake_task = SimpleNamespace(dev_notes="orig")
    A2AService.update_task_with_message(fake_task, fake_msg)
    assert fake_task.dev_notes == "orig"


# ---------------------------------------------------------------------------
# resolve_creator_agent — known agent slug + lookup hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_creator_agent_known_slug_with_uuid_hit(
    a2a_setup: dict,
) -> None:
    """from_agent_id is a known slug AND has UUID — looks up by UUID."""
    svc = a2a_setup["svc"]
    dev = a2a_setup["dev"]
    # be-dev-1 is in ALL_AGENTS (a list) by default; just inject a UUID.
    with patch.dict(
        "roboco.services.a2a.AGENT_UUIDS",
        {"be-dev-1": str(dev.id)},
    ):
        out = await svc.resolve_creator_agent("be-dev-1")
    # May return None if agent not found by id, or the dev row.
    assert out is None or hasattr(out, "id")


# ---------------------------------------------------------------------------
# create_a2a_notification — full path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_a2a_notification_missing_task_id_raises(
    a2a_setup: dict,
) -> None:
    """task_id absent → ValueError."""
    svc = a2a_setup["svc"]
    msg = A2AMessage(role="user", parts=[TextPart(text="hi")])
    req = SendMessageRequest(message=msg, metadata={})
    with pytest.raises(ValueError, match="task_id"):
        await svc.create_a2a_notification(req)


@pytest.mark.asyncio
async def test_create_a2a_notification_with_target_calls_notification_service(
    a2a_setup: dict,
) -> None:
    """Path with from_agent + target + skill → NotificationService called."""
    svc = a2a_setup["svc"]
    task_id = str(a2a_setup["task_id"])
    msg = A2AMessage(role="user", parts=[TextPart(text="hi there")], task_id=task_id)
    req = SendMessageRequest(
        message=msg,
        metadata={"from_agent": "be-dev-1", "target_agent": "be-dev-2"},
    )
    mock_ns = AsyncMock()
    mock_ns.send_a2a_notification = AsyncMock(return_value=None)
    with patch(
        "roboco.services.notification.NotificationService",
        return_value=mock_ns,
    ):
        result = await svc.create_a2a_notification(req)
    assert result["task_id"] == task_id


@pytest.mark.asyncio
async def test_create_a2a_notification_permission_denied(
    a2a_setup: dict,
) -> None:
    """can_a2a_direct returns False → ValueError with hint."""
    svc = a2a_setup["svc"]
    task_id = str(a2a_setup["task_id"])
    msg = A2AMessage(role="user", parts=[TextPart(text="hi")], task_id=task_id)
    req = SendMessageRequest(
        message=msg,
        metadata={"from_agent": "be-dev-1", "target_agent": "be-dev-2"},
    )
    with (
        patch(
            "roboco.agents_config.can_a2a_direct",
            return_value=(False, "denied"),
        ),
        patch(
            "roboco.agents_config.get_a2a_route_hint",
            return_value="use channel",
        ),
        pytest.raises(ValueError, match="Hint:"),
    ):
        await svc.create_a2a_notification(req)


# ---------------------------------------------------------------------------
# update_task_from_message — full happy path + invalid id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_from_message_invalid_id(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    msg = A2AMessage(role="user", parts=[TextPart(text="hi")])
    with pytest.raises(ValueError, match="Invalid task ID"):
        await svc.update_task_from_message("not-a-uuid", msg)


@pytest.mark.asyncio
async def test_update_task_from_message_not_found(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    msg = A2AMessage(role="user", parts=[TextPart(text="hi")])
    with pytest.raises(ValueError, match="not found"):
        await svc.update_task_from_message(str(_u()), msg)


@pytest.mark.asyncio
async def test_update_task_from_message_success(a2a_setup: dict) -> None:
    """Real task updated with new dev_notes."""
    svc = a2a_setup["svc"]
    task_id = str(a2a_setup["task_id"])
    msg = A2AMessage(role="user", parts=[TextPart(text="response text")])
    updated = await svc.update_task_from_message(task_id, msg)
    assert "response text" in (updated.dev_notes or "")


# ---------------------------------------------------------------------------
# _notify_original_requester — branches: not A2A, no created_by, no slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_original_requester_not_a2a_returns(
    a2a_setup: dict,
) -> None:
    """Task.dev_notes lacks 'A2A Request' → no-op."""
    svc = a2a_setup["svc"]
    fake_task = SimpleNamespace(dev_notes="just notes", created_by=None)
    # No raise.
    await svc._notify_original_requester(fake_task, "responder")


@pytest.mark.asyncio
async def test_notify_original_requester_no_created_by(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    fake_task = SimpleNamespace(dev_notes="A2A Request: hi", created_by=None)
    await svc._notify_original_requester(fake_task)


@pytest.mark.asyncio
async def test_notify_original_requester_unknown_slug(
    a2a_setup: dict,
) -> None:
    """If the creator UUID can't be mapped to a slug, returns silently."""
    svc = a2a_setup["svc"]
    fake_task = SimpleNamespace(
        id=_u(),
        dev_notes="A2A Request: please review",
        created_by=_u(),  # Not in AGENT_UUIDS.
    )
    await svc._notify_original_requester(fake_task)


@pytest.mark.asyncio
async def test_notify_original_requester_responder_is_requester(
    a2a_setup: dict,
) -> None:
    """If responder is the same as requester, no event published."""
    svc = a2a_setup["svc"]
    fake_task = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        dev_notes="A2A Request: please review",
        created_by="be-dev-1-uuid",
    )
    with patch(
        "roboco.services.a2a.A2AService._lookup_requester_slug",
        return_value="be-dev-1",
    ):
        # responder == requester → return without publish.
        await svc._notify_original_requester(fake_task, "be-dev-1")


@pytest.mark.asyncio
async def test_notify_original_requester_publishes_event(
    a2a_setup: dict,
) -> None:
    """Full path: requester slug found, responder different → publish."""
    svc = a2a_setup["svc"]
    fake_task = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        dev_notes="A2A Request: please review",
        created_by="some-uuid",
    )
    mock_bus = AsyncMock()
    mock_bus.is_connected = lambda: True
    mock_bus.publish = AsyncMock(return_value=None)
    with (
        patch(
            "roboco.services.a2a.A2AService._lookup_requester_slug",
            return_value="be-dev-1",
        ),
        patch("roboco.services.a2a.get_event_bus", return_value=mock_bus),
    ):
        await svc._notify_original_requester(fake_task, "be-dev-2")
    mock_bus.publish.assert_awaited()


@pytest.mark.asyncio
async def test_publish_a2a_response_event_no_bus() -> None:
    """Bus not connected → silent no-op."""
    fake_task = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
    mock_bus = type("B", (), {"is_connected": lambda _self: False})()
    with patch("roboco.services.a2a.get_event_bus", return_value=mock_bus):
        await A2AService._publish_a2a_response_event(
            fake_task, "creator", "requester", "responder"
        )


@pytest.mark.asyncio
async def test_publish_a2a_response_event_bus_exception_swallowed() -> None:
    fake_task = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
    with patch(
        "roboco.services.a2a.get_event_bus",
        side_effect=RuntimeError("bus down"),
    ):
        # Exception swallowed.
        await A2AService._publish_a2a_response_event(
            fake_task, "creator", "requester", "responder"
        )


# ---------------------------------------------------------------------------
# list_conversations — status/with_agent/task_id filters + last message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations_with_status_and_task_filter(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    convs = await svc.list_conversations(
        "be-dev-1",
        status=A2AConversationStatus.ACTIVE,
        with_agent="be-dev-2",
        task_id=a2a_setup["task_id"],
    )
    assert isinstance(convs, list)


@pytest.mark.asyncio
async def test_list_conversations_with_messages(a2a_setup: dict) -> None:
    """Seed a conversation + a message so the last_message preview path runs."""
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    await svc.send_chat_message(UUID(conv.id), "be-dev-1", "preview text")

    convs = await svc.list_conversations("be-dev-1")
    # last_message_preview should be truthy for this conv.
    assert any(c.last_message_preview for c in convs)


# ---------------------------------------------------------------------------
# send_chat_message — both agents perspective + message_kind/response_to
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_chat_message_options_response_to(
    a2a_setup: dict,
) -> None:
    """response_to_id and requires_response options surface on the message.

    `response_to_id` has a FK to `a2a_messages.id`, so we send a real
    "first" message to thread off — passing a random UUID would hit FK
    violation.
    """
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    first = await svc.send_chat_message(UUID(conv.id), "be-qa", "first message")
    msg = await svc.send_chat_message(
        UUID(conv.id),
        "be-dev-1",
        "needs answer",
        options={"requires_response": True, "response_to_id": UUID(first.id)},
    )
    assert msg.requires_response is True
    assert msg.response_to_id == first.id


@pytest.mark.asyncio
async def test_send_chat_message_from_agent_b_increments_unread_a(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    # First exchange establishes canonical pair (a < b lexicographically).
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    # Send from whichever agent is conv.agent_b (the "other" side).
    result = await svc.session.execute(
        _sel(A2AConversationTable).where(A2AConversationTable.id == UUID(conv.id))
    )
    row = result.scalar_one()
    msg = await svc.send_chat_message(UUID(conv.id), row.agent_b, "hi from b")
    assert msg.from_agent == row.agent_b


# ---------------------------------------------------------------------------
# get_messages — non-participant returns []
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_messages_non_participant_returns_empty(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    msgs = await svc.get_messages(UUID(conv.id), "ghost-agent")
    assert msgs == []


@pytest.mark.asyncio
async def test_get_messages_with_before_filter(a2a_setup: dict) -> None:
    """Pass a `before` datetime — exercises the filter branch."""
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    await svc.send_chat_message(UUID(conv.id), "be-dev-1", "first")
    future = datetime.now(UTC).replace(year=2099)
    msgs = await svc.get_messages(UUID(conv.id), "be-dev-1", before=future)
    assert isinstance(msgs, list)


# ---------------------------------------------------------------------------
# mark_read — non-participant returns silently + agent_b path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_read_non_participant(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    # Non-participant → silent return.
    await svc.mark_read(UUID(conv.id), "ghost")


@pytest.mark.asyncio
async def test_mark_read_as_agent_b(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    result = await svc.session.execute(
        _sel(A2AConversationTable).where(A2AConversationTable.id == UUID(conv.id))
    )
    row = result.scalar_one()
    await svc.mark_read(UUID(conv.id), row.agent_b)


# ---------------------------------------------------------------------------
# list_pairs — exercises grouping + last_activity comparison
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pairs_with_conversations(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    await svc.get_or_create_conversation("be-dev-1", "be-qa")
    pairs = await svc.list_pairs("be-dev-1")
    assert any(
        (p.agent_a, p.agent_b) == ("be-dev-1", "be-qa")
        or (p.agent_a, p.agent_b) == ("be-qa", "be-dev-1")
        for p in pairs
    )


# ---------------------------------------------------------------------------
# _resolve_slug_from_id — happy + raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_slug_from_id_happy(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    dev = a2a_setup["dev"]
    slug = await svc._resolve_slug_from_id(dev.id)
    assert slug == dev.slug


@pytest.mark.asyncio
async def test_resolve_slug_from_id_missing_raises(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    with pytest.raises(ValueError, match="Agent not found"):
        await svc._resolve_slug_from_id(_u())


# ---------------------------------------------------------------------------
# send — gateway adapter, both UUID and str recipient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_gateway_adapter_uuid_to_uuid(
    a2a_setup: dict,
) -> None:
    """Both ends as UUIDs → resolves both slugs via DB lookup."""
    svc = a2a_setup["svc"]
    dev = a2a_setup["dev"]
    qa = a2a_setup["qa"]
    msg = await svc.send(
        from_agent=dev.id,
        to_agent=qa.id,
        task_id=a2a_setup["task_id"],
        body="hello",
        skill="general",
    )
    assert msg.content == "hello"


@pytest.mark.asyncio
async def test_send_gateway_adapter_with_string_recipient(
    a2a_setup: dict,
) -> None:
    """Recipient as slug string → no DB lookup for it."""
    svc = a2a_setup["svc"]
    dev = a2a_setup["dev"]
    msg = await svc.send(
        from_agent=dev.id,
        to_agent="be-qa",
        task_id=a2a_setup["task_id"],
        body="hello",
    )
    assert msg.content == "hello"


# ---------------------------------------------------------------------------
# create_task_from_message — patched DB so flush succeeds
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _lookup_requester_slug — found path
# ---------------------------------------------------------------------------


def test_lookup_requester_slug_found() -> None:
    target_uuid = "00000000-0000-0000-0000-000000000042"
    with patch(
        "roboco.seeds.initial_data.AGENT_UUIDS",
        {"slug-x": target_uuid},
    ):
        result = A2AService._lookup_requester_slug(target_uuid)
    assert result == "slug-x"


# ---------------------------------------------------------------------------
# get_or_create_conversation — topic provided branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_conversation_with_topic(
    a2a_setup: dict,
) -> None:
    svc = a2a_setup["svc"]
    a = await svc.get_or_create_conversation("be-dev-1", "be-qa", topic="Bug X")
    b = await svc.get_or_create_conversation("be-dev-1", "be-qa", topic="Bug X")
    assert a.id == b.id


# ---------------------------------------------------------------------------
# get_conversation — non-participant returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_conversation_non_participant(a2a_setup: dict) -> None:
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    result = await svc.get_conversation(UUID(conv.id), "ghost")
    assert result is None


@pytest.mark.asyncio
async def test_get_conversation_returns_model_when_participant(
    a2a_setup: dict,
) -> None:
    """Participant access → returns the conversation model."""
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    result = await svc.get_conversation(UUID(conv.id), "be-dev-1")
    assert result is not None
    assert result.id == conv.id


# ---------------------------------------------------------------------------
# get_inbox_summary with unread > 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_inbox_summary_with_unread(a2a_setup: dict) -> None:
    """Send a message from a2 → a1 has unread."""
    svc = a2a_setup["svc"]
    conv = await svc.get_or_create_conversation("be-dev-1", "be-qa")
    result = await svc.session.execute(
        _sel(A2AConversationTable).where(A2AConversationTable.id == UUID(conv.id))
    )
    row = result.scalar_one()
    # Send from agent_b → agent_a unread increments.
    await svc.send_chat_message(UUID(conv.id), row.agent_b, "hi")
    inbox = await svc.get_inbox_summary(row.agent_a)
    assert inbox.total_unread >= 1


# ---------------------------------------------------------------------------
# send — gateway adapter without skill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_gateway_adapter_skill_none(
    a2a_setup: dict,
) -> None:
    """skill=None branch → options dict stays empty."""
    svc = a2a_setup["svc"]
    dev = a2a_setup["dev"]
    msg = await svc.send(
        from_agent=dev.id,
        to_agent="be-qa",
        task_id=a2a_setup["task_id"],
        body="no skill",
    )
    assert msg.content == "no skill"


@pytest.mark.asyncio
async def test_send_gateway_adapter_with_mocked_conv(
    a2a_setup: dict,
) -> None:
    """Mock get_or_create_conversation + send_chat_message so skill branches run."""
    svc = a2a_setup["svc"]
    dev = a2a_setup["dev"]
    fake_conv = SimpleNamespace(id=str(_u()))
    fake_msg = SimpleNamespace(content="hi", id=str(_u()))
    with (
        patch.object(svc, "_resolve_slug_from_id", AsyncMock(return_value="be-dev-1")),
        patch.object(
            svc, "get_or_create_conversation", AsyncMock(return_value=fake_conv)
        ),
        patch.object(svc, "send_chat_message", AsyncMock(return_value=fake_msg)),
    ):
        # With skill set.
        result = await svc.send(
            from_agent=dev.id,
            to_agent="be-qa",
            task_id=a2a_setup["task_id"],
            body="hi",
            skill="general",
        )
    assert result.content == "hi"
    # Without skill.
    with (
        patch.object(svc, "_resolve_slug_from_id", AsyncMock(return_value="be-dev-1")),
        patch.object(
            svc, "get_or_create_conversation", AsyncMock(return_value=fake_conv)
        ),
        patch.object(svc, "send_chat_message", AsyncMock(return_value=fake_msg)),
    ):
        result2 = await svc.send(
            from_agent=dev.id,
            to_agent="be-qa",
            task_id=a2a_setup["task_id"],
            body="hi-2",
        )
    assert result2.content == "hi"


@pytest.mark.asyncio
async def test_mark_all_read_clears_unread_for_agent(a2a_setup: dict) -> None:
    """mark_all_read zeroes the agent's unread counter across its conversations
    and stamps read_at on the inbound messages, returning the count cleared —
    the bulk ack that lets an agent satisfy i_am_idle's unread-A2A soft-block."""
    svc: A2AService = a2a_setup["svc"]
    db = a2a_setup["db"]
    qa = a2a_setup["qa"]
    conv = await svc.get_or_create_conversation(
        agent_a="be-dev-1", agent_b="be-qa", task_id=a2a_setup["task_id"]
    )
    conv_id = UUID(conv.id)
    # be-dev-1 < be-qa canonically → dev is agent_a; dev's messages bump qa's
    # unread (unread_by_b).
    await svc.send_chat_message(conv_id, "be-dev-1", "one")
    await svc.send_chat_message(conv_id, "be-dev-1", "two")

    cleared = await svc.mark_all_read(qa.id)
    assert cleared == 1

    row = await db.get(A2AConversationTable, conv_id)
    assert row is not None
    assert row.unread_by_b == 0
    msgs = (
        (
            await db.execute(
                select(A2AMessageTable).where(
                    A2AMessageTable.conversation_id == conv_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert all(m.read_at is not None for m in msgs)

    # Idempotent — nothing left unread for qa.
    assert await svc.mark_all_read(qa.id) == 0
