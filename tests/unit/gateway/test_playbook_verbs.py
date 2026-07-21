"""Playbook content verbs — role grants + ContentActions RBAC.

Delivery roles DRAFT playbooks; only the Auditor CURATES (approve/reject/archive).
Curation is KB-curation, not agent comms, and stays separate from the
Auditor's dm/read_a2a surface (CEO-reachable, reply-only — see
agents_config.can_a2a_direct for the peer-initiation refusal).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.base import ConflictError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.gateway.role_config import get_role_config
from sqlalchemy.exc import PendingRollbackError

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
    # No "say" tool exists; dm/read_a2a ARE present (CEO-reachable, reply-only
    # — the auditor still never initiates peer A2A, enforced in
    # agents_config.can_a2a_direct, not by omitting the tool here).
    assert "say" not in do_tools
    assert "dm" in do_tools
    assert "read_a2a" in do_tools


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
    # the status commit gates the index — commit then index, never index
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
    # de-index is the post-commit step (commit gates it).
    actions.task.session.commit.assert_awaited_once()
    svc.unindex_playbook.assert_awaited_once_with(archived)


@pytest.mark.asyncio
async def test_archive_playbook_retires_approved_for_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """archive_playbook is the distinct APPROVED->archived retire path:
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
    re-fetch the playbook's current status before re-trying."""
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


@pytest.mark.asyncio
async def test_curate_poisoned_session_returns_envelope_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gating ``session.commit()`` runs before the RAG index. If a prior
    mid-verb failure poisoned the caller's session (PendingRollbackError), the
    commit raises — the curation verb must surface a clean invalid_state
    envelope (not a 500) AND must NOT proceed to index an uncommitted playbook
    into the corpus. See #55."""
    approved = MagicMock()
    approved.id = uuid4()
    approved.status = "approved"
    svc = MagicMock()
    svc.approve = AsyncMock(return_value=approved)
    svc.index_approved = AsyncMock()
    monkeypatch.setattr("roboco.services.playbook.get_playbook_service", lambda _s: svc)
    actions = _actions("auditor")
    actions.task.session.commit = AsyncMock(
        side_effect=PendingRollbackError("session was rolled back")
    )
    env = await actions.approve_playbook(agent_id=uuid4(), playbook_id=uuid4())
    assert env.error == "invalid_state"
    assert env.remediate  # tell the agent how to recover (re-fetch + retry)
    # The status change did NOT commit, so we must not have indexed it.
    svc.index_approved.assert_not_awaited()


@pytest.mark.asyncio
async def test_curate_clean_session_commits_once_then_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a healthy session the gating commit succeeds exactly once and the
    index follows — pinning that the #55 guard did not invert to fail-closed
    or double-commit on the happy path."""
    approved = MagicMock()
    approved.id = uuid4()
    approved.status = "approved"
    svc = MagicMock()
    svc.approve = AsyncMock(return_value=approved)
    svc.index_approved = AsyncMock()
    monkeypatch.setattr("roboco.services.playbook.get_playbook_service", lambda _s: svc)
    actions = _actions("auditor")
    actions.task.session.commit = AsyncMock()
    env = await actions.approve_playbook(agent_id=uuid4(), playbook_id=uuid4())
    assert env.error is None
    actions.task.session.commit.assert_awaited_once()
    svc.index_approved.assert_awaited_once_with(approved)
