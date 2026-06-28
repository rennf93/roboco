"""F057: the PLAYBOOKS RAG index write must not commit independently of — and
BEFORE — the playbook status transaction.

``approve()`` / ``reject()`` used to call ``_index_approved`` / ``_unindex_playbook``
inline, AFTER ``flush()`` but BEFORE the caller's ``commit()``. The vector store
writes chunks via its OWN pool connection (vector_store.py:211-237), which
auto-commits immediately and independently of the SQLAlchemy session
transaction. So a status-commit failure (or a crash between the index write and
the commit) left the RAG corpus with an approved/archived playbook whose DB row
was still DRAFT/APPROVED — a divergence agents then surfaced in briefings.

The fix: ``approve()`` / ``reject()`` flush the status ONLY; the index/unindex
is a separate post-commit step (``index_approved`` / ``unindex_playbook``) the
caller runs AFTER the status transaction commits. Both entry points — the panel
route (playbooks.py) and the Auditor gateway verb (content_actions.
_curate_playbook) — commit-then-index, and skip the index if the commit fails.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.api.routes.playbooks import approve_playbook, reject_playbook
from roboco.config import settings
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.playbook import PlaybookService

_PID = uuid4()
_APPROVER = uuid4()


def _playbook_mock() -> Any:
    pb = MagicMock(name="playbook")
    pb.id = _PID
    # approve/reject act on a DRAFT (their legitimate starting state — F109
    # guards both to DRAFT-only). The index/unindex ordering assertions below
    # are independent of the starting status.
    pb.status = "draft"
    pb.title = "T"
    pb.problem = "P"
    pb.procedure = "Pr"
    pb.tags = []
    pb.team = "backend"
    pb.scope = "team"
    return pb


# ---------------------------------------------------------------------------
# Service-level: approve/reject flush ONLY; index/unindex are separate steps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_does_not_index_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``approve()`` flushes the status change but must NOT write to the RAG
    index — that writes through its own auto-committing connection, so it would
    durably land before the caller commits the status (the F057 divergence)."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    session = AsyncMock()
    session.flush = AsyncMock()
    svc = PlaybookService(session)
    monkeypatch.setattr(svc, "_get_or_raise", AsyncMock(return_value=_playbook_mock()))

    optimal = MagicMock()
    optimal.index_playbook = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )

    await svc.approve(_PID, approver_id=_APPROVER)

    optimal.index_playbook.assert_not_awaited()


@pytest.mark.asyncio
async def test_index_approved_is_a_separate_post_commit_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``index_approved`` is the public post-commit index step the caller runs
    AFTER committing the status — it invokes the optimal index."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    svc = PlaybookService(AsyncMock())
    optimal = MagicMock()
    optimal.index_playbook = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )

    await svc.index_approved(_playbook_mock())

    optimal.index_playbook.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_does_not_unindex_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reject()`` flushes the status change but must NOT de-index from the RAG
    index inline — same ordering gap as approve (the de-index auto-commits before
    the status commit, dropping an approved playbook from briefings while its row
    is still APPROVED on rollback)."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    session = AsyncMock()
    session.flush = AsyncMock()
    svc = PlaybookService(session)
    monkeypatch.setattr(svc, "_get_or_raise", AsyncMock(return_value=_playbook_mock()))

    optimal = MagicMock()
    optimal.unindex_playbook = AsyncMock()
    optimal.deindex_playbook = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )

    await svc.reject(_PID, approver_id=_APPROVER, reason="nope")

    # No de-index call of any name originated from reject() before commit.
    optimal.unindex_playbook.assert_not_awaited()
    optimal.deindex_playbook.assert_not_awaited()


@pytest.mark.asyncio
async def test_unindex_playbook_is_a_separate_post_commit_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``unindex_playbook`` is the public post-commit de-index step."""
    monkeypatch.setattr(settings, "org_memory_enabled", True)
    svc = PlaybookService(AsyncMock())
    optimal = MagicMock()
    optimal.unindex_playbook = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )

    await svc.unindex_playbook(_playbook_mock())

    optimal.unindex_playbook.assert_awaited_once()


# ---------------------------------------------------------------------------
# Route-level (panel): commit-then-index, skip index on commit failure
# ---------------------------------------------------------------------------


def _route_service_mock(
    order: list[str], *, commit_fails: bool = False
) -> tuple[Any, Any]:
    def _approve(*_a: Any, **_k: Any) -> Any:
        order.append("approve")
        return _playbook_mock()

    def _reject(*_a: Any, **_k: Any) -> Any:
        order.append("reject")
        return _playbook_mock()

    svc = MagicMock()
    svc.approve = AsyncMock(side_effect=_approve)
    svc.reject = AsyncMock(side_effect=_reject)
    svc.index_approved = AsyncMock(side_effect=lambda *_a, **_k: order.append("index"))
    svc.unindex_playbook = AsyncMock(
        side_effect=lambda *_a, **_k: order.append("unindex")
    )
    db = AsyncMock()
    if commit_fails:
        db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
    else:
        db.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    return svc, db


@pytest.mark.asyncio
async def test_approve_route_commits_before_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    svc, db = _route_service_mock(order)
    monkeypatch.setattr(
        "roboco.api.routes.playbooks.get_playbook_service", lambda _db: svc
    )
    monkeypatch.setattr(
        "roboco.api.routes.playbooks.Playbook.model_validate", lambda obj: obj
    )
    agent = AgentContext(agent_id=_APPROVER, role=AgentRole.AUDITOR)

    await approve_playbook(_PID, db, agent)

    assert order == ["approve", "commit", "index"]


@pytest.mark.asyncio
async def test_approve_route_skips_index_when_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    svc, db = _route_service_mock(order, commit_fails=True)
    monkeypatch.setattr(
        "roboco.api.routes.playbooks.get_playbook_service", lambda _db: svc
    )
    agent = AgentContext(agent_id=_APPROVER, role=AgentRole.AUDITOR)

    with pytest.raises(RuntimeError):
        await approve_playbook(_PID, db, agent)

    svc.index_approved.assert_not_awaited()
    assert order == ["approve"]


@pytest.mark.asyncio
async def test_reject_route_commits_before_unindexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    svc, db = _route_service_mock(order)
    monkeypatch.setattr(
        "roboco.api.routes.playbooks.get_playbook_service", lambda _db: svc
    )
    monkeypatch.setattr(
        "roboco.api.routes.playbooks.Playbook.model_validate", lambda obj: obj
    )
    agent = AgentContext(agent_id=_APPROVER, role=AgentRole.AUDITOR)

    await reject_playbook(_PID, MagicMock(reason="nope"), db, agent)

    assert order == ["reject", "commit", "unindex"]


# ---------------------------------------------------------------------------
# Gateway-level (Auditor verb): commit-then-index, skip index on commit failure
# ---------------------------------------------------------------------------


def _gateway_actions(
    order: list[str], *, commit_fails: bool = False
) -> tuple[ContentActions, MagicMock]:
    task = MagicMock()
    task.session = AsyncMock()
    if commit_fails:
        task.session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
    else:
        task.session.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    task.agent_for = AsyncMock(return_value=MagicMock(role="auditor"))
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        messaging=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
    )
    return ContentActions(deps), task


def _gateway_svc_mock(order: list[str]) -> MagicMock:
    def _approve(*_a: Any, **_k: Any) -> Any:
        order.append("approve")
        return _playbook_mock()

    def _reject(*_a: Any, **_k: Any) -> Any:
        order.append("reject")
        return _playbook_mock()

    svc = MagicMock()
    svc.approve = AsyncMock(side_effect=_approve)
    svc.reject = AsyncMock(side_effect=_reject)
    svc.index_approved = AsyncMock(side_effect=lambda *_a, **_k: order.append("index"))
    svc.unindex_playbook = AsyncMock(
        side_effect=lambda *_a, **_k: order.append("unindex")
    )
    return svc


@pytest.mark.asyncio
async def test_gateway_approve_commits_before_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    actions, _task = _gateway_actions(order)
    svc = _gateway_svc_mock(order)
    monkeypatch.setattr(
        "roboco.services.playbook.get_playbook_service",
        lambda _session: svc,
    )

    env = await actions.approve_playbook(agent_id=_APPROVER, playbook_id=_PID)

    assert order == ["approve", "commit", "index"]
    assert env.status == "playbook_approved"


@pytest.mark.asyncio
async def test_gateway_approve_skips_index_when_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    actions, _task = _gateway_actions(order, commit_fails=True)
    svc = _gateway_svc_mock(order)
    monkeypatch.setattr(
        "roboco.services.playbook.get_playbook_service",
        lambda _session: svc,
    )

    with pytest.raises(RuntimeError):
        await actions.approve_playbook(agent_id=_APPROVER, playbook_id=_PID)

    svc.index_approved.assert_not_awaited()
    assert order == ["approve"]


@pytest.mark.asyncio
async def test_gateway_reject_commits_before_unindexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    actions, _task = _gateway_actions(order)
    svc = _gateway_svc_mock(order)
    monkeypatch.setattr(
        "roboco.services.playbook.get_playbook_service",
        lambda _session: svc,
    )

    env = await actions.reject_playbook(
        agent_id=_APPROVER, playbook_id=_PID, reason="nope"
    )

    assert order == ["reject", "commit", "unindex"]
    assert env.status == "playbook_archived"
