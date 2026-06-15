"""roboco.services.pitch — CRUD + approve/reject orchestration (mocked deps).

The approve path constructs real domain models (ProjectCreate, ProductCellMapping,
TaskCreateRequest) but the downstream services and the GitHub provisioner are
faked, so the test exercises the orchestration logic without a DB or network.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.db.tables import PitchTable
from roboco.foundation.identity import Team
from roboco.models.pitch import PitchCreate, PitchStatus
from roboco.services import pitch as pitch_module
from roboco.services.base import ConflictError
from roboco.services.github_provisioning import (
    GitHubProvisioningService,
    ProvisionedRepo,
    ProvisioningDisabledError,
)
from roboco.services.pitch import PitchService


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


class _FakeProvisioning(GitHubProvisioningService):
    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self.created: list[str] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def create_repo(
        self, name: str, description: str = "", *, private: bool = True
    ) -> ProvisionedRepo:
        _ = (description, private)
        self.created.append(name)
        return ProvisionedRepo(
            full_name=f"org/{name}",
            clone_url=f"https://github.com/org/{name}.git",
            html_url=f"https://github.com/org/{name}",
        )

    async def close(self) -> None:
        return None


def _patch_topology(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    proj = MagicMock()
    proj.id = uuid4()
    project_svc = MagicMock()
    project_svc.create = AsyncMock(return_value=proj)
    monkeypatch.setattr(pitch_module, "get_project_service", lambda _s: project_svc)

    prod = MagicMock()
    prod.id = uuid4()
    product_svc = MagicMock()
    product_svc.create = AsyncMock(return_value=prod)
    monkeypatch.setattr(pitch_module, "get_product_service", lambda _s: product_svc)

    task = MagicMock()
    task.id = uuid4()
    task_svc = MagicMock()
    task_svc.create = AsyncMock(return_value=task)
    monkeypatch.setattr(pitch_module, "get_task_service", lambda _s: task_svc)

    main_pm = MagicMock()
    main_pm.id = uuid4()
    agent_svc = MagicMock()
    agent_svc.get_by_slug = AsyncMock(return_value=main_pm)
    monkeypatch.setattr(pitch_module, "get_agent_service", lambda _s: agent_svc)

    return {"project": project_svc, "product": product_svc, "task": task_svc}


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
async def test_approve_single_cell_provisions_project_and_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = PitchService(_session())
    pitch = _pitch(target_cells=["backend"])
    monkeypatch.setattr(svc, "get", AsyncMock(return_value=pitch))
    svcs = _patch_topology(monkeypatch)
    prov = _FakeProvisioning(enabled=True)

    result = await svc.approve(
        pitch.id, "approved for build", uuid4(), provisioning=prov
    )

    assert result.status == PitchStatus.PROVISIONED.value
    assert result.seed_task_id is not None
    assert result.provisioned_project_ids is not None
    assert len(result.provisioned_project_ids) == len(pitch.target_cells)
    assert result.provisioned_product_id is None
    assert prov.created == ["widget"]
    svcs["product"].create.assert_not_called()
    svcs["task"].create.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_multi_cell_creates_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = PitchService(_session())
    pitch = _pitch(slug="multi", target_cells=["backend", "frontend"])
    monkeypatch.setattr(svc, "get", AsyncMock(return_value=pitch))
    svcs = _patch_topology(monkeypatch)
    prov = _FakeProvisioning(enabled=True)

    result = await svc.approve(pitch.id, "approved", uuid4(), provisioning=prov)

    assert result.provisioned_product_id is not None
    assert result.provisioned_project_ids is not None
    assert len(result.provisioned_project_ids) == len(pitch.target_cells)
    assert prov.created == ["multi-backend", "multi-frontend"]
    svcs["product"].create.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_rejects_when_not_proposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = PitchService(_session())
    monkeypatch.setattr(
        svc, "get", AsyncMock(return_value=_pitch(status="provisioned"))
    )
    with pytest.raises(ConflictError):
        await svc.approve(uuid4(), "x", uuid4(), provisioning=_FakeProvisioning())


@pytest.mark.asyncio
async def test_approve_blocked_when_provisioning_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = PitchService(_session())
    monkeypatch.setattr(svc, "get", AsyncMock(return_value=_pitch()))
    with pytest.raises(ProvisioningDisabledError):
        await svc.approve(
            uuid4(), "x", uuid4(), provisioning=_FakeProvisioning(enabled=False)
        )
