"""roboco.services.pitch — CRUD + approve/reject orchestration (mocked deps).

Repository auto-provisioning was removed with the GitHub App integration;
approve now raises ProvisioningDisabledError so the CEO knows provisioning is
unavailable.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.db.tables import PitchTable
from roboco.foundation.identity import Team
from roboco.models.pitch import PitchCreate, PitchStatus
from roboco.services.base import ConflictError
from roboco.services.pitch import PitchService, ProvisioningDisabledError


def _session() -> MagicMock:
    s = MagicMock()
    s.add = MagicMock()
    s.flush = AsyncMock()
    return s


def _pitch(**kw: Any) -> PitchTable:
    defaults: dict[str, Any] = {
        "id": uuid4(),
        "title": "Widget",
        "slug": "widget",
        "problem": "people need widgets",
        "proposed_solution": "build a widget service",
        "target_cells": ["backend"],
        "status": "proposed",
        "created_by": uuid4(),
    }
    defaults.update(kw)
    return PitchTable(**defaults)


@pytest.mark.asyncio
async def test_create_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    svc = PitchService(session)
    monkeypatch.setattr(svc, "get_by_slug", AsyncMock(return_value=None))
    pitch = await svc.create(
        PitchCreate(
            title="Widget",
            slug="widget",
            problem="p",
            proposed_solution="s",
            target_cells=[Team.BACKEND, Team.FRONTEND],
        ),
        created_by=uuid4(),
    )
    assert pitch.slug == "widget"
    assert pitch.status == PitchStatus.PROPOSED.value
    assert pitch.target_cells == ["backend", "frontend"]
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_conflict_on_duplicate_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = PitchService(_session())
    monkeypatch.setattr(svc, "get_by_slug", AsyncMock(return_value=_pitch()))
    with pytest.raises(ConflictError):
        await svc.create(
            PitchCreate(
                title="Widget",
                slug="widget",
                problem="p",
                proposed_solution="s",
                target_cells=[Team.BACKEND],
            ),
            created_by=uuid4(),
        )


@pytest.mark.asyncio
async def test_reject_sets_status(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = PitchService(_session())
    pitch = _pitch()
    monkeypatch.setattr(svc, "get", AsyncMock(return_value=pitch))
    result = await svc.reject(pitch.id, "not aligned with the charter", uuid4())
    assert result.status == PitchStatus.REJECTED.value
    assert result.decision_notes == "not aligned with the charter"


@pytest.mark.asyncio
async def test_approve_rejects_when_not_proposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = PitchService(_session())
    monkeypatch.setattr(
        svc, "get", AsyncMock(return_value=_pitch(status="provisioned"))
    )
    with pytest.raises(ConflictError):
        await svc.approve(uuid4(), "x", uuid4())


@pytest.mark.asyncio
async def test_approve_raises_because_provisioning_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = PitchService(_session())
    monkeypatch.setattr(svc, "get", AsyncMock(return_value=_pitch()))
    with pytest.raises(ProvisioningDisabledError):
        await svc.approve(uuid4(), "x", uuid4())
