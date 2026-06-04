"""Integration tests for PromptService (PromptSession + PromptTurn CRUD).

All tests require a live Postgres instance.  They are skipped automatically
when Postgres is unreachable (see top-level conftest.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.models.base import PromptSessionStatus
from roboco.services.base import NotFoundError
from roboco.services.prompter import PromptService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def prompt_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    """Yield a seeded dict with a ``PromptService`` backed by ``db_session``."""
    svc = PromptService(db_session)
    yield {"svc": svc}


# ---------------------------------------------------------------------------
# Session happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_happy_path(prompt_setup: dict) -> None:
    """create_session returns a row with DRAFT status and the expected fields."""
    svc: PromptService = prompt_setup["svc"]

    session = await svc.create_session(system_prompt="You are helpful.", model="gpt-4o")

    assert session.id is not None
    assert session.status == PromptSessionStatus.DRAFT
    assert session.system_prompt == "You are helpful."
    assert session.model == "gpt-4o"
    assert session.created_by is None


@pytest.mark.asyncio
async def test_create_session_with_created_by(prompt_setup: dict) -> None:
    """create_session stores created_by when provided."""
    svc: PromptService = prompt_setup["svc"]
    owner = uuid4()

    session = await svc.create_session(created_by=owner)

    assert str(session.created_by) == str(owner)


@pytest.mark.asyncio
async def test_get_session_happy_path(prompt_setup: dict) -> None:
    """get_session returns the same row that was created."""
    svc: PromptService = prompt_setup["svc"]

    created = await svc.create_session()
    fetched = await svc.get_session(created.id)

    assert str(fetched.id) == str(created.id)
    assert fetched.status == PromptSessionStatus.DRAFT


@pytest.mark.asyncio
async def test_get_session_raises_not_found_for_unknown_id(
    prompt_setup: dict,
) -> None:
    """get_session raises NotFoundError for an unknown UUID."""
    svc: PromptService = prompt_setup["svc"]

    with pytest.raises(NotFoundError) as exc_info:
        await svc.get_session(uuid4())

    assert exc_info.value.resource_type == "PromptSession"


# ---------------------------------------------------------------------------
# list_sessions filter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_returns_all_when_unfiltered(
    prompt_setup: dict,
) -> None:
    """list_sessions with no filters returns all sessions created in this test."""
    svc: PromptService = prompt_setup["svc"]

    a = await svc.create_session()
    b = await svc.create_session()

    sessions = await svc.list_sessions()
    session_ids = {str(s.id) for s in sessions}

    assert str(a.id) in session_ids
    assert str(b.id) in session_ids


@pytest.mark.asyncio
async def test_list_sessions_filter_by_status_draft(prompt_setup: dict) -> None:
    """list_sessions(status=DRAFT) returns only draft sessions."""
    svc: PromptService = prompt_setup["svc"]

    draft = await svc.create_session()
    launched = await svc.create_session()
    await svc.update_session_status(launched.id, PromptSessionStatus.LAUNCHED)

    drafts = await svc.list_sessions(status=PromptSessionStatus.DRAFT)
    draft_ids = {str(s.id) for s in drafts}

    assert str(draft.id) in draft_ids
    assert str(launched.id) not in draft_ids


@pytest.mark.asyncio
async def test_list_sessions_filter_by_status_launched(prompt_setup: dict) -> None:
    """list_sessions(status=LAUNCHED) returns only launched sessions."""
    svc: PromptService = prompt_setup["svc"]

    draft = await svc.create_session()
    launched = await svc.create_session()
    await svc.update_session_status(launched.id, PromptSessionStatus.LAUNCHED)

    launched_list = await svc.list_sessions(status=PromptSessionStatus.LAUNCHED)
    launched_ids = {str(s.id) for s in launched_list}

    assert str(launched.id) in launched_ids
    assert str(draft.id) not in launched_ids


@pytest.mark.asyncio
async def test_list_sessions_filter_by_created_by(prompt_setup: dict) -> None:
    """list_sessions(created_by=...) returns only sessions belonging to that owner."""
    svc: PromptService = prompt_setup["svc"]
    owner = uuid4()
    other = uuid4()

    mine = await svc.create_session(created_by=owner)
    await svc.create_session(created_by=other)

    mine_list = await svc.list_sessions(created_by=owner)
    mine_ids = {str(s.id) for s in mine_list}

    assert str(mine.id) in mine_ids
    # The other owner's session should not appear.
    for s in mine_list:
        assert str(s.created_by) == str(owner)


# ---------------------------------------------------------------------------
# update_session_status transition tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_session_status_draft_to_launched(prompt_setup: dict) -> None:
    """Status can transition from DRAFT to LAUNCHED."""
    svc: PromptService = prompt_setup["svc"]

    session = await svc.create_session()
    assert session.status == PromptSessionStatus.DRAFT

    updated = await svc.update_session_status(session.id, PromptSessionStatus.LAUNCHED)
    assert updated.status == PromptSessionStatus.LAUNCHED


@pytest.mark.asyncio
async def test_update_session_status_draft_to_abandoned(prompt_setup: dict) -> None:
    """Status can transition from DRAFT to ABANDONED."""
    svc: PromptService = prompt_setup["svc"]

    session = await svc.create_session()
    updated = await svc.update_session_status(
        session.id, PromptSessionStatus.ABANDONED
    )
    assert updated.status == PromptSessionStatus.ABANDONED


@pytest.mark.asyncio
async def test_update_session_status_accepts_string_value(
    prompt_setup: dict,
) -> None:
    """update_session_status accepts a plain lowercase string as the status arg."""
    svc: PromptService = prompt_setup["svc"]

    session = await svc.create_session()
    updated = await svc.update_session_status(session.id, "launched")
    assert updated.status == PromptSessionStatus.LAUNCHED


# ---------------------------------------------------------------------------
# delete_session tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session_removes_record(prompt_setup: dict) -> None:
    """delete_session returns True and the session is gone afterwards."""
    svc: PromptService = prompt_setup["svc"]

    session = await svc.create_session()
    result = await svc.delete_session(session.id)

    assert result is True

    with pytest.raises(NotFoundError):
        await svc.get_session(session.id)


@pytest.mark.asyncio
async def test_delete_session_returns_false_for_unknown_id(
    prompt_setup: dict,
) -> None:
    """delete_session returns False (not an error) for an unknown UUID."""
    svc: PromptService = prompt_setup["svc"]

    result = await svc.delete_session(uuid4())
    assert result is False


# ---------------------------------------------------------------------------
# Turn happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_turn_happy_path(prompt_setup: dict) -> None:
    """create_turn returns a turn linked to the correct session."""
    svc: PromptService = prompt_setup["svc"]

    session = await svc.create_session()
    turn = await svc.create_turn(session.id, role="user", content="Hello, world!")

    assert turn.id is not None
    assert str(turn.session_id) == str(session.id)
    assert turn.role == "user"
    assert turn.content == "Hello, world!"
    assert turn.turn_index == 0


@pytest.mark.asyncio
async def test_create_turn_raises_not_found_for_unknown_session(
    prompt_setup: dict,
) -> None:
    """create_turn raises NotFoundError when the parent session does not exist."""
    svc: PromptService = prompt_setup["svc"]

    with pytest.raises(NotFoundError) as exc_info:
        await svc.create_turn(uuid4(), role="user", content="orphaned turn")

    assert exc_info.value.resource_type == "PromptSession"


@pytest.mark.asyncio
async def test_list_turns_happy_path(prompt_setup: dict) -> None:
    """list_turns returns all turns ordered by turn_index ascending."""
    svc: PromptService = prompt_setup["svc"]

    session = await svc.create_session()
    t0 = await svc.create_turn(session.id, role="user", content="q1", turn_index=0)
    t1 = await svc.create_turn(session.id, role="assistant", content="a1", turn_index=1)
    t2 = await svc.create_turn(session.id, role="user", content="q2", turn_index=2)

    turns = await svc.list_turns(session.id)

    assert len(turns) == 3
    assert str(turns[0].id) == str(t0.id)
    assert str(turns[1].id) == str(t1.id)
    assert str(turns[2].id) == str(t2.id)


@pytest.mark.asyncio
async def test_list_turns_returns_empty_for_new_session(
    prompt_setup: dict,
) -> None:
    """list_turns returns an empty list for a session with no turns."""
    svc: PromptService = prompt_setup["svc"]

    session = await svc.create_session()
    turns = await svc.list_turns(session.id)

    assert turns == []


@pytest.mark.asyncio
async def test_delete_session_cascades_to_turns(prompt_setup: dict) -> None:
    """Deleting a session removes all its turns via cascade."""
    svc: PromptService = prompt_setup["svc"]

    session = await svc.create_session()
    await svc.create_turn(session.id, role="user", content="before delete")

    await svc.delete_session(session.id)

    # Session gone — turns should be gone too (cascade).
    turns = await svc.list_turns(session.id)
    assert turns == []
