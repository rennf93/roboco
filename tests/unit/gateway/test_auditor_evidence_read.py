"""The auditor reads evidence() on any task; every write verb stays gated.

triage() surfaces blocked tasks the auditor is not assigned to. evidence() is
read-only and admits the claim-less observer role, while note/dm/notify/
progress on that same task keep the assignee/claim gate.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import ContentActions
from roboco.services.gateway.envelope import Envelope
from tests.unit.gateway.test_content_actions import _make_deps


def _foreign_task(task_id: Any, assignee: Any) -> MagicMock:
    """A task assigned to someone else, with no ancestors and no project."""
    return MagicMock(
        id=task_id,
        assigned_to=assignee,
        active_claimant_id=assignee,
        parent_task_id=None,
        project_id=uuid4(),
        product_id=None,
        branch_name=None,
        work_session_id=None,
        dependency_ids=[],
        status="blocked",
    )


def _actions(role: str, task: MagicMock) -> ContentActions:
    task_svc = AsyncMock()
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(role=role)
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_tasks_claimed_by.return_value = []
    task_svc.list_journal_context_tasks_for_agent.return_value = []
    task_svc.session = MagicMock()
    task_svc.session.commit = AsyncMock()
    return ContentActions(_make_deps(task=task_svc))


@pytest.mark.asyncio
async def test_auditor_reads_evidence_on_a_task_it_is_not_assigned() -> None:
    task = _foreign_task(uuid4(), uuid4())
    ca = _actions("auditor", task)

    result = await ca._evidence_task_or_error(task.id, uuid4())

    assert result is task


@pytest.mark.asyncio
async def test_developer_is_still_refused_evidence_on_a_foreign_task() -> None:
    task = _foreign_task(uuid4(), uuid4())
    ca = _actions("developer", task)

    result = await ca._evidence_task_or_error(task.id, uuid4())

    assert isinstance(result, Envelope)
    assert result.as_dict()["error"] == "not_authorized"


@pytest.mark.asyncio
@pytest.mark.parametrize("write", ["note", "progress", "dm", "notify"])
async def test_auditor_is_refused_every_write_on_a_foreign_task(write: str) -> None:
    task = _foreign_task(uuid4(), uuid4())
    ca = _actions("auditor", task)
    agent_id = uuid4()
    calls: dict[str, Any] = {
        "note": lambda: ca.note(
            agent_id=agent_id, text="auditor observation", task_id=task.id
        ),
        "progress": lambda: ca.progress(
            agent_id=agent_id, task_id=task.id, message="auditor progress update"
        ),
        "dm": lambda: ca.dm(
            agent_id=agent_id,
            recipient="be-pm",
            text="auditor message",
            task_id=task.id,
        ),
        "notify": lambda: ca.notify(
            agent_id=agent_id, target="be-pm", text="auditor notice", task_id=task.id
        ),
    }

    env = await calls[write]()

    body = env.as_dict()
    assert body["error"] == "not_authorized"
    if write != "notify":
        # notify is refused earlier, by its PM/Board-only role gate.
        assert "not the assignee" in body["message"]
