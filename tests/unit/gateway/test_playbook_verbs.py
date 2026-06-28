"""Playbook content verbs — role grants + ContentActions RBAC.

Delivery roles DRAFT playbooks; only the Auditor CURATES (approve/reject/archive).
The Auditor's no-say/no-dm restriction is preserved (these are KB curation
actions, not agent comms).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.base import ConflictError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.gateway.role_config import get_role_config

_DRAFT_ROLES = ("developer", "qa", "documenter", "cell_pm", "main_pm")
_CURATE_VERBS = ("approve_playbook", "reject_playbook", "archive_playbook")


# --- role grants (spawn-manifest source of truth) --------------------------- #


def test_delivery_roles_can_draft_playbook() -> None:
    for role in _DRAFT_ROLES:
        assert "draft_playbook" in get_role_config(role).do_tools


def test_auditor_curates_but_does_not_draft() -> None:
    do_tools = get_role_config("auditor").do_tools
    for verb in _CURATE_VERBS:
        assert verb in do_tools
    assert "draft_playbook" not in do_tools
    # No-say/no-dm preserved.
    assert "say" not in do_tools
    assert "dm" not in do_tools


def test_delivery_role_cannot_curate() -> None:
    assert "approve_playbook" not in get_role_config("developer").do_tools


# --- ContentActions RBAC ---------------------------------------------------- #


def _actions(role: str) -> ContentActions:
    task = MagicMock()
    agent = MagicMock()
    agent.role = role
    task.agent_for = AsyncMock(return_value=agent)
    task.session = AsyncMock()
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        messaging=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
    )
    return ContentActions(deps)


@pytest.mark.asyncio
async def test_draft_playbook_forbidden_for_auditor() -> None:
    env = await _actions("auditor").draft_playbook(
        agent_id=uuid4(),
        title="Retry flaky pg",
        problem="connection resets",
        procedure="1. retry with backoff",
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_draft_playbook_creates_for_developer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = MagicMock()
    created.id = uuid4()
    svc = MagicMock()
    svc.draft = AsyncMock(return_value=created)
    monkeypatch.setattr("roboco.services.playbook.get_playbook_service", lambda _s: svc)
    env = await _actions("developer").draft_playbook(
        agent_id=uuid4(),
        title="Retry flaky pg",
        problem="connection resets",
        procedure="1. retry with backoff",
    )
    assert env.error is None
    assert env.status == "playbook_drafted"
    svc.draft.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_playbook_forbidden_for_developer() -> None:
    env = await _actions("developer").approve_playbook(
        agent_id=uuid4(), playbook_id=uuid4()
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_approve_playbook_for_auditor(monkeypatch: pytest.MonkeyPatch) -> None:
    approved = MagicMock()
    approved.id = uuid4()
    approved.status = "approved"
    svc = MagicMock()
    svc.approve = AsyncMock(return_value=approved)
    svc.index_approved = AsyncMock()
    monkeypatch.setattr("roboco.services.playbook.get_playbook_service", lambda _s: svc)
    actions = _actions("auditor")
    env = await actions.approve_playbook(agent_id=uuid4(), playbook_id=uuid4())
    assert env.error is None
    assert env.status == "playbook_approved"
    svc.approve.assert_awaited_once()
    # F057: the status commit gates the index — commit then index, never index
    # before commit (the index write auto-commits on its own connection).
    actions.task.session.commit.assert_awaited_once()
    svc.index_approved.assert_awaited_once_with(approved)


@pytest.mark.asyncio
async def test_reject_playbook_archives_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archived = MagicMock()
    archived.id = uuid4()
    archived.status = "archived"
    svc = MagicMock()
    svc.reject = AsyncMock(return_value=archived)
    svc.unindex_playbook = AsyncMock()
    monkeypatch.setattr("roboco.services.playbook.get_playbook_service", lambda _s: svc)
    actions = _actions("auditor")
    env = await actions.reject_playbook(
        agent_id=uuid4(), playbook_id=uuid4(), reason="duplicate"
    )
    assert env.status == "playbook_archived"
    svc.reject.assert_awaited_once()
    # F057: de-index is the post-commit step (commit gates it).
    actions.task.session.commit.assert_awaited_once()
    svc.unindex_playbook.assert_awaited_once_with(archived)


@pytest.mark.asyncio
async def test_archive_playbook_retires_approved_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """archive_playbook is the distinct APPROVED->archived retire path (F109):
    it calls ``svc.archive`` (NOT ``svc.reject``), commits, then de-indexes."""
    archived = MagicMock()
    archived.id = uuid4()
    archived.status = "archived"
    svc = MagicMock()
    svc.archive = AsyncMock(return_value=archived)
    svc.reject = AsyncMock()
    svc.unindex_playbook = AsyncMock()
    monkeypatch.setattr("roboco.services.playbook.get_playbook_service", lambda _s: svc)
    actions = _actions("auditor")
    env = await actions.archive_playbook(agent_id=uuid4(), playbook_id=uuid4())
    assert env.status == "playbook_archived"
    svc.archive.assert_awaited_once()
    svc.reject.assert_not_awaited()
    actions.task.session.commit.assert_awaited_once()
    svc.unindex_playbook.assert_awaited_once_with(archived)


@pytest.mark.asyncio
async def test_approve_playbook_invalid_state_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A status-precondition ConflictError from the service becomes a clean
    invalid_state envelope (not a 500) — the agent gets a remediate hint to
    re-fetch the playbook's current status before re-trying (F109)."""
    svc = MagicMock()
    svc.approve = AsyncMock(
        side_effect=ConflictError("not draft", resource_type="playbook")
    )
    monkeypatch.setattr("roboco.services.playbook.get_playbook_service", lambda _s: svc)
    env = await _actions("auditor").approve_playbook(
        agent_id=uuid4(), playbook_id=uuid4()
    )
    assert env.error == "invalid_state"
    assert env.remediate  # the agent is told how to recover
