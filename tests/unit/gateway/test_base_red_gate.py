"""Base-red submission gate: refusal, owner exemption, fail-open, clear."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.base_red_gate import (
    AUDIT_EVENT_CLEARED,
    base_red_submission_guard,
    clear_base_red_incident,
    resolve_incident_service,
)
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

OWNER_TASK = uuid4()
INCIDENT = SimpleNamespace(
    owner_task_id=OWNER_TASK, owner_agent="be-dev-1", base_branch="slave"
)


class FakeSource:
    def __init__(self, *, open_incident: bool = True, owner: bool = False) -> None:
        self.incident: Any = INCIDENT if open_incident else None
        self.owner = owner
        self.lookup_error: Exception | None = None
        self.cleared: list[tuple[Any, str, str]] = []

    async def get_open_incident(self, _project_id: Any) -> Any:
        if self.lookup_error:
            raise self.lookup_error
        return self.incident

    async def is_owner_task(self, _task_id: Any) -> bool:
        return self.owner

    async def clear(self, project_id: Any, cleared_by: str, reason: str) -> None:
        self.cleared.append((project_id, cleared_by, reason))
        self.incident = None


def _task() -> Any:
    return SimpleNamespace(id=uuid4(), project_id=uuid4())


@pytest.mark.asyncio
async def test_refused_naming_owner_and_base() -> None:
    env = await base_red_submission_guard(FakeSource(), _task())
    assert env is not None
    assert env.error == "not_authorized"
    assert "be-dev-1" in env.remediate
    assert str(OWNER_TASK) in env.remediate
    assert "slave" in env.remediate
    assert "manual clear" in env.remediate


@pytest.mark.asyncio
async def test_owner_task_passes() -> None:
    assert await base_red_submission_guard(FakeSource(owner=True), _task()) is None


@pytest.mark.asyncio
async def test_no_incident_passes() -> None:
    assert (
        await base_red_submission_guard(FakeSource(open_incident=False), _task())
        is None
    )


@pytest.mark.asyncio
async def test_inert_without_source_or_project() -> None:
    assert await base_red_submission_guard(None, _task()) is None
    task = SimpleNamespace(id=uuid4(), project_id=None)
    assert await base_red_submission_guard(FakeSource(), task) is None


@pytest.mark.asyncio
async def test_lookup_error_fails_open() -> None:
    src = FakeSource()
    src.lookup_error = RuntimeError("db down")
    assert await base_red_submission_guard(src, _task()) is None


@pytest.mark.asyncio
async def test_clear_reopens_submission_and_audits() -> None:
    src = FakeSource()
    task = _task()
    assert await base_red_submission_guard(src, task) is not None
    audit = AsyncMock()
    await clear_base_red_incident(
        src, audit, project_id=task.project_id, cleared_by="be-pm", reason="base green"
    )
    assert src.cleared == [(task.project_id, "be-pm", "base green")]
    assert audit.log_event.await_args.kwargs["event_type"] == AUDIT_EVENT_CLEARED
    assert await base_red_submission_guard(src, task) is None


def test_resolve_incident_service_inert_when_module_absent() -> None:
    # Returns None until be-dev-1's service lands; a real service afterwards.
    result = resolve_incident_service(MagicMock())
    assert result is None or hasattr(result, "get_open_incident")


def _choreographer(source: FakeSource, task: Any) -> Choreographer:
    deps = ChoreographerDeps(
        task=AsyncMock(),
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
        base_red=source,
    )
    deps.task.get.return_value = task
    return Choreographer(deps)


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["open_pr", "i_am_done"])
async def test_both_verbs_refused_while_incident_open(verb: str) -> None:
    task = _task()
    choreo = _choreographer(FakeSource(), task)
    args: tuple[Any, ...] = (uuid4(), task.id)
    if verb == "i_am_done":
        args += ("notes",)
    env = await getattr(choreo, verb)(*args)
    assert env.error == "not_authorized"
    assert "be-dev-1" in env.remediate
    choreo.git.assert_not_called()
