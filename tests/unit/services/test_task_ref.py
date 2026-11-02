"""The task-reference resolver both the agent gateway and the REST routes use."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.task_ref import (
    TaskRefOutcome,
    resolve_task_ref,
    task_ref_hex_prefix,
)


def _session_returning(rows: list[tuple[Any, ...]]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_full_uuid_resolves_without_a_lookup() -> None:
    task_id = uuid4()
    session = _session_returning([])
    resolution = await resolve_task_ref(session, f"  {task_id}  ")
    assert resolution.outcome is TaskRefOutcome.RESOLVED
    assert resolution.task_id == task_id
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unique_prefix_resolves_to_its_task() -> None:
    task_id = uuid4()
    resolution = await resolve_task_ref(
        _session_returning([(task_id,)]), str(task_id)[:8]
    )
    assert resolution.outcome is TaskRefOutcome.RESOLVED
    assert resolution.task_id == task_id


@pytest.mark.asyncio
async def test_prefix_without_a_match_is_not_found() -> None:
    resolution = await resolve_task_ref(_session_returning([]), "0000dead")
    assert resolution.outcome is TaskRefOutcome.NOT_FOUND
    assert resolution.task_id is None


@pytest.mark.asyncio
async def test_prefix_shared_by_two_tasks_is_ambiguous() -> None:
    resolution = await resolve_task_ref(
        _session_returning([(uuid4(),), (uuid4(),)]), "feedface"
    )
    assert resolution.outcome is TaskRefOutcome.AMBIGUOUS
    assert resolution.task_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["d38d", "summary1", "", "#zzzzzzzz"])
async def test_reference_that_cannot_name_a_task_is_malformed(ref: str) -> None:
    session = _session_returning([])
    resolution = await resolve_task_ref(session, ref)
    assert resolution.outcome is TaskRefOutcome.MALFORMED
    session.execute.assert_not_awaited()


def test_hex_prefix_drops_the_card_hash_and_lowercases() -> None:
    assert task_ref_hex_prefix(" #D38D6910 ") == "d38d6910"
