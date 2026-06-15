"""roboco.api.routes.pitch — role gates + decision flow (direct-call style)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from roboco.api.routes import pitch as pitch_route
from roboco.api.schemas.pitch import PitchCreateRequest, PitchDecision
from roboco.db.tables import PitchTable
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.base import ConflictError
from roboco.services.github_provisioning import ProvisioningDisabledError


def _agent(role: AgentRole) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=None)


def _db() -> MagicMock:
    db = MagicMock()
    db.commit = AsyncMock()
    return db


def _pitch() -> PitchTable:
    return PitchTable(
        id=uuid4(),
        title="Widget",
        slug="widget",
        problem="p",
        proposed_solution="s",
        target_cells=["backend"],
        status="proposed",
        created_by=uuid4(),
    )


class _FakeService:
    def __init__(
        self, *, pitch: PitchTable | None = None, exc: Exception | None = None
    ) -> None:
        self._pitch = pitch
        self._exc = exc

    async def create(self, _data: Any, created_by: Any) -> PitchTable:
        _ = created_by
        if self._exc is not None:
            raise self._exc
        assert self._pitch is not None
        return self._pitch

    async def approve(
        self, _pitch_id: Any, _notes: Any, _by: Any, *, provisioning: Any = None
    ) -> PitchTable:
        _ = provisioning
        if self._exc is not None:
            raise self._exc
        assert self._pitch is not None
        return self._pitch

    async def reject(self, _pitch_id: Any, _notes: Any, _by: Any) -> PitchTable:
        if self._exc is not None:
            raise self._exc
        assert self._pitch is not None
        return self._pitch


def _install(monkeypatch: pytest.MonkeyPatch, service: _FakeService) -> None:
    monkeypatch.setattr(pitch_route, "get_pitch_service", lambda _db: service)


@pytest.mark.asyncio
async def test_non_board_cannot_create() -> None:
    with pytest.raises(HTTPException) as exc:
        await pitch_route.create_pitch(
            PitchCreateRequest(
                title="W",
                slug="w",
                problem="p",
                proposed_solution="s",
                target_cells=["backend"],
            ),
            _db(),
            _agent(AgentRole.DEVELOPER),
        )
    assert exc.value.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_create_rejects_non_cell_target() -> None:
    with pytest.raises(HTTPException) as exc:
        await pitch_route.create_pitch(
            PitchCreateRequest(
                title="W",
                slug="w",
                problem="p",
                proposed_solution="s",
                target_cells=["board"],
            ),
            _db(),
            _agent(AgentRole.PRODUCT_OWNER),
        )
    assert exc.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_create_success(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db()
    _install(monkeypatch, _FakeService(pitch=_pitch()))
    resp = await pitch_route.create_pitch(
        PitchCreateRequest(
            title="Widget",
            slug="widget",
            problem="p",
            proposed_solution="s",
            target_cells=["backend"],
        ),
        db,
        _agent(AgentRole.HEAD_MARKETING),
    )
    assert resp.slug == "widget"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_ceo_cannot_approve() -> None:
    with pytest.raises(HTTPException) as exc:
        await pitch_route.approve_pitch(
            uuid4(), _db(), _agent(AgentRole.PRODUCT_OWNER), PitchDecision(notes="x")
        )
    assert exc.value.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_approve_provisioning_disabled_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeService(exc=ProvisioningDisabledError("not configured")))
    with pytest.raises(HTTPException) as exc:
        await pitch_route.approve_pitch(
            uuid4(), _db(), _agent(AgentRole.CEO), PitchDecision(notes="go")
        )
    assert exc.value.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_approve_conflict_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeService(exc=ConflictError("already decided")))
    with pytest.raises(HTTPException) as exc:
        await pitch_route.approve_pitch(
            uuid4(), _db(), _agent(AgentRole.CEO), PitchDecision(notes="go")
        )
    assert exc.value.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_reject_requires_reason() -> None:
    with pytest.raises(HTTPException) as exc:
        await pitch_route.reject_pitch(
            uuid4(), PitchDecision(notes=None), _db(), _agent(AgentRole.CEO)
        )
    assert exc.value.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_reject_success(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db()
    _install(monkeypatch, _FakeService(pitch=_pitch()))
    resp = await pitch_route.reject_pitch(
        uuid4(), PitchDecision(notes="not now, off-charter"), db, _agent(AgentRole.CEO)
    )
    assert resp.slug == "widget"
    db.commit.assert_awaited_once()
