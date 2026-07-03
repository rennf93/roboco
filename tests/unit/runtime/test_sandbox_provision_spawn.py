"""`AgentOrchestrator._maybe_provision_sandbox` — the spawn-time decision gate.

Off (flag or project) => None, byte-for-byte identical to legacy behavior. A
project lookup hiccup degrades to "no sandbox" (best-effort, matching the
ambient-conventions-resolution convention); an actual provisioning failure
IS fail-loud — an agent whose gate can't run must never spawn.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.models.runtime import PostgresSandbox, SandboxInfo
from roboco.runtime.orchestrator import AgentOrchestrator, AgentReadinessError


def _make_orchestrator() -> tuple[AgentOrchestrator, MagicMock]:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._bg_tasks = set()
    orch._running = True
    sandbox = MagicMock()
    sandbox.provision = AsyncMock()
    orch._sandbox = sandbox
    return orch, sandbox


@asynccontextmanager
async def _fake_db_ctx(db: Any) -> Any:
    yield db


@pytest.mark.asyncio
async def test_flag_off_returns_none_without_project_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", False)
    orch, sandbox = _make_orchestrator()

    with patch("roboco.services.project.get_project_service") as get_svc:
        result = await orch._maybe_provision_sandbox("dev-1", "roboco-api", "task-1")

    assert result is None
    get_svc.assert_not_called()
    sandbox.provision.assert_not_called()


@pytest.mark.asyncio
async def test_project_without_sandbox_services_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, sandbox = _make_orchestrator()
    project = MagicMock(sandbox_services=None)
    project_service = MagicMock()
    project_service.get_by_slug = AsyncMock(return_value=project)

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(MagicMock())),
        patch(
            "roboco.services.project.get_project_service",
            return_value=project_service,
        ),
    ):
        result = await orch._maybe_provision_sandbox("dev-1", "roboco-api", "task-1")

    assert result is None
    sandbox.provision.assert_not_called()


@pytest.mark.asyncio
async def test_missing_project_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, _sandbox = _make_orchestrator()
    project_service = MagicMock()
    project_service.get_by_slug = AsyncMock(return_value=None)

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(MagicMock())),
        patch(
            "roboco.services.project.get_project_service",
            return_value=project_service,
        ),
    ):
        result = await orch._maybe_provision_sandbox("dev-1", "roboco-api", "task-1")

    assert result is None


@pytest.mark.asyncio
async def test_opted_in_project_provisions_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, sandbox = _make_orchestrator()
    project = MagicMock(sandbox_services=["postgres"])
    project_service = MagicMock()
    project_service.get_by_slug = AsyncMock(return_value=project)
    info = SandboxInfo(
        postgres=PostgresSandbox(
            host="h", port=5432, user="sandbox", password="pw", database="sandbox"
        )
    )
    sandbox.provision.return_value = info

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(MagicMock())),
        patch(
            "roboco.services.project.get_project_service",
            return_value=project_service,
        ),
    ):
        result = await orch._maybe_provision_sandbox("dev-1", "roboco-api", "task-1")

    assert result is info
    sandbox.provision.assert_awaited_once_with("dev-1", ["postgres"])


@pytest.mark.asyncio
async def test_provisioning_failure_raises_readiness_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, sandbox = _make_orchestrator()
    project = MagicMock(sandbox_services=["postgres", "redis"])
    project_service = MagicMock()
    project_service.get_by_slug = AsyncMock(return_value=project)
    sandbox.provision.side_effect = RuntimeError("boom")

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(MagicMock())),
        patch(
            "roboco.services.project.get_project_service",
            return_value=project_service,
        ),
        pytest.raises(AgentReadinessError, match="sandbox provisioning failed"),
    ):
        await orch._maybe_provision_sandbox("dev-1", "roboco-api", "task-1")


@pytest.mark.asyncio
async def test_project_lookup_failure_degrades_to_no_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, sandbox = _make_orchestrator()

    with patch("roboco.db.base.get_db_context", side_effect=RuntimeError("db down")):
        result = await orch._maybe_provision_sandbox("dev-1", "roboco-api", "task-1")

    assert result is None
    sandbox.provision.assert_not_called()
