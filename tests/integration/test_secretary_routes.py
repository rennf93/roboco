"""roboco.api.routes.secretary — role gates + directive flow (direct-call style)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from roboco.api.routes import secretary as sec_route
from roboco.api.schemas.secretary import DirectiveDecision, DirectiveSubmit
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.base import ConflictError

_ROW = object()
_DIRECTIVE_DICT: dict[str, Any] = {
    "id": "11111111-1111-1111-1111-111111111111",
    "kind": "relay_message",
    "status": "executed",
    "payload": {},
    "requested_by": "22222222-2222-2222-2222-222222222222",
    "requested_at": None,
    "decided_by": None,
    "decided_at": None,
    "result": "posted to #all-hands",
}


def _agent(role: AgentRole) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=None)


def _db() -> MagicMock:
    db = MagicMock()
    db.commit = AsyncMock()
    return db


class _FakeService:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self._exc = exc

    async def submit_directive(self, _kind: Any, _payload: Any, _by: Any) -> object:
        if self._exc is not None:
            raise self._exc
        return _ROW

    async def confirm_directive(self, _directive_id: Any, _by: Any) -> object:
        if self._exc is not None:
            raise self._exc
        return _ROW

    async def reject_directive(
        self, _directive_id: Any, _by: Any, _reason: Any
    ) -> object:
        if self._exc is not None:
            raise self._exc
        return _ROW

    async def list_directives(self, _status: Any = None) -> list[object]:
        return [_ROW]

    async def read_company_state(self) -> dict[str, Any]:
        return {
            "goals": {},
            "task_counts": {},
            "pending_pitches": [],
            "pending_directives": [],
        }

    def to_dict(self, _row: object) -> dict[str, Any]:
        return dict(_DIRECTIVE_DICT)


def _install(monkeypatch: pytest.MonkeyPatch, service: _FakeService) -> None:
    monkeypatch.setattr(sec_route, "get_secretary_service", lambda _db: service)


@pytest.mark.asyncio
async def test_submit_forbidden_for_developer() -> None:
    with pytest.raises(HTTPException) as exc:
        await sec_route.submit_directive(
            DirectiveSubmit(kind="relay_message"), _db(), _agent(AgentRole.DEVELOPER)
        )
    assert exc.value.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_submit_bad_kind_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await sec_route.submit_directive(
            DirectiveSubmit(kind="bogus"), _db(), _agent(AgentRole.SECRETARY)
        )
    assert exc.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_submit_success(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db()
    _install(monkeypatch, _FakeService())
    resp = await sec_route.submit_directive(
        DirectiveSubmit(
            kind="relay_message", payload={"channel": "all-hands", "text": "hi"}
        ),
        db,
        _agent(AgentRole.SECRETARY),
    )
    assert resp.status == "executed"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_forbidden_for_secretary() -> None:
    with pytest.raises(HTTPException) as exc:
        await sec_route.confirm_directive(uuid4(), _db(), _agent(AgentRole.SECRETARY))
    assert exc.value.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_confirm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db()
    _install(monkeypatch, _FakeService())
    resp = await sec_route.confirm_directive(uuid4(), db, _agent(AgentRole.CEO))
    assert resp.id == _DIRECTIVE_DICT["id"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_conflict_409(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeService(exc=ConflictError("already decided")))
    with pytest.raises(HTTPException) as exc:
        await sec_route.confirm_directive(uuid4(), _db(), _agent(AgentRole.CEO))
    assert exc.value.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_list_forbidden_for_secretary() -> None:
    with pytest.raises(HTTPException) as exc:
        await sec_route.list_directives(_db(), _agent(AgentRole.SECRETARY))
    assert exc.value.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_reject_success(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db()
    _install(monkeypatch, _FakeService())
    resp = await sec_route.reject_directive(
        uuid4(), DirectiveDecision(reason="no"), db, _agent(AgentRole.CEO)
    )
    assert resp.id == _DIRECTIVE_DICT["id"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_state_allows_secretary(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeService())
    resp = await sec_route.read_state(_db(), _agent(AgentRole.SECRETARY))
    assert resp.pending_pitches == []
