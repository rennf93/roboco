"""`AgentOrchestrator._sandbox_available_services` — the spawn-time availability
probe — and `ensure_sandbox` — the on-demand provision/cache path used by the
`request_sandbox` do-verb.

Off (flag or project) => [], byte-for-byte identical to legacy behavior. A
project lookup hiccup degrades to "no sandbox available" (best-effort,
matching the ambient-conventions-resolution convention). Provisioning itself
no longer happens at spawn time — a spawn never fails on sandbox
infrastructure; `ensure_sandbox` is the only path that calls
`SandboxProvisioner.provision`, and it is idempotent via an in-memory cache.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.models.sandbox import SandboxConnection, SandboxInfo
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orchestrator() -> tuple[AgentOrchestrator, MagicMock]:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._bg_tasks = set()
    orch._running = True
    orch._sandbox_info = {}
    sandbox = MagicMock()
    sandbox.provision = AsyncMock()
    # Live by default so cache-hit tests that don't care about liveness pass
    # through; tests exercising DEFECT 3 (dead-container eviction) override.
    sandbox.is_live = AsyncMock(return_value=True)
    orch._sandbox = sandbox
    return orch, sandbox


@asynccontextmanager
async def _fake_db_ctx(db: Any) -> Any:
    yield db


# ---------------------------------------------------------------------------
# _sandbox_available_services (spawn-time probe, no provisioning)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_returns_empty_without_project_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", False)
    orch, sandbox = _make_orchestrator()

    with patch("roboco.services.project.get_project_service") as get_svc:
        result = await orch._sandbox_available_services("roboco-api")

    assert result == []
    get_svc.assert_not_called()
    sandbox.provision.assert_not_called()


@pytest.mark.asyncio
async def test_project_without_sandbox_services_returns_empty(
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
        result = await orch._sandbox_available_services("roboco-api")

    assert result == []
    sandbox.provision.assert_not_called()


@pytest.mark.asyncio
async def test_missing_project_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
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
        result = await orch._sandbox_available_services("roboco-api")

    assert result == []


@pytest.mark.asyncio
async def test_opted_in_project_returns_services_without_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, sandbox = _make_orchestrator()
    project = MagicMock(sandbox_services=["postgres"])
    project_service = MagicMock()
    project_service.get_by_slug = AsyncMock(return_value=project)

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(MagicMock())),
        patch(
            "roboco.services.project.get_project_service",
            return_value=project_service,
        ),
    ):
        result = await orch._sandbox_available_services("roboco-api")

    assert result == ["postgres"]
    sandbox.provision.assert_not_called()


@pytest.mark.asyncio
async def test_project_lookup_failure_degrades_to_no_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, sandbox = _make_orchestrator()

    with patch("roboco.db.base.get_db_context", side_effect=RuntimeError("db down")):
        result = await orch._sandbox_available_services("roboco-api")

    assert result == []
    sandbox.provision.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_sandbox (on-demand provision + cache, called by request_sandbox)
# ---------------------------------------------------------------------------


def _info(services: dict[str, SandboxConnection]) -> SandboxInfo:
    return SandboxInfo(services=services)


@pytest.mark.asyncio
async def test_ensure_sandbox_miss_provisions_and_caches() -> None:
    orch, sandbox = _make_orchestrator()
    info = _info({"postgres": SandboxConnection(host="h", port=5432, password="pw")})
    sandbox.provision.return_value = info

    result = await orch.ensure_sandbox("dev-1", ["postgres"], ["postgres"])

    assert result is info
    sandbox.provision.assert_awaited_once_with("dev-1", ["postgres"])
    assert orch._sandbox_info["dev-1"] is info


@pytest.mark.asyncio
async def test_ensure_sandbox_cache_hit_skips_second_provision() -> None:
    orch, sandbox = _make_orchestrator()
    info = _info({"postgres": SandboxConnection(host="h", port=5432, password="pw")})
    sandbox.provision.return_value = info

    first = await orch.ensure_sandbox("dev-1", ["postgres"], ["postgres"])
    second = await orch.ensure_sandbox("dev-1", ["postgres"], ["postgres"])

    assert first is second is info
    sandbox.provision.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_sandbox_first_subset_request_provisions_full_opted_set() -> None:
    """DEFECT 1 fix: a first request for a subset of the project's opted-in
    set provisions the FULL opted set — not just what this call named — so a
    later call for the rest of that set is a guaranteed cache hit and never
    falls through to a fresh provision() (whose pre-clear teardown() would
    otherwise kill the live container the agent is already using)."""
    orch, sandbox = _make_orchestrator()
    info = _info(
        {
            "postgres": SandboxConnection(host="h", port=5432, password="pw"),
            "redis": SandboxConnection(host="h", port=6379, password="rw"),
        }
    )
    sandbox.provision.return_value = info

    first = await orch.ensure_sandbox("dev-1", ["postgres"], ["postgres", "redis"])
    second = await orch.ensure_sandbox(
        "dev-1", ["postgres", "redis"], ["postgres", "redis"]
    )

    assert first is second is info
    sandbox.provision.assert_awaited_once_with("dev-1", ["postgres", "redis"])
    assert orch._sandbox_info["dev-1"] is info


@pytest.mark.asyncio
async def test_ensure_sandbox_cache_is_per_agent_slug() -> None:
    """Caller A's cache entry never leaks to caller B (cross-agent isolation)."""
    orch, sandbox = _make_orchestrator()
    info_a = _info({"postgres": SandboxConnection(host="a", port=5432, password="pa")})
    info_b = _info({"postgres": SandboxConnection(host="b", port=5432, password="pb")})
    sandbox.provision.side_effect = [info_a, info_b]

    result_a = await orch.ensure_sandbox("dev-1", ["postgres"], ["postgres"])
    result_b = await orch.ensure_sandbox("dev-2", ["postgres"], ["postgres"])

    assert result_a is info_a
    assert result_b is info_b
    assert orch._sandbox_info["dev-1"] is info_a
    assert orch._sandbox_info["dev-2"] is info_b


@pytest.mark.asyncio
async def test_ensure_sandbox_concurrent_calls_serialize_on_agent_lock() -> None:
    """DEFECT 2 fix: two concurrent ensure_sandbox calls for the same agent
    (e.g. a client timeout + retry) must serialize behind the per-agent lock
    so only one provision() ever runs — never a race between provision() and
    a concurrent teardown()."""
    orch, sandbox = _make_orchestrator()
    info = _info({"postgres": SandboxConnection(host="h", port=5432, password="pw")})
    calls = 0

    async def _slow_provision(_agent_id: str, _services: list[str]) -> SandboxInfo:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return info

    sandbox.provision.side_effect = _slow_provision

    results = await asyncio.gather(
        orch.ensure_sandbox("dev-1", ["postgres"], ["postgres"]),
        orch.ensure_sandbox("dev-1", ["postgres"], ["postgres"]),
    )

    assert results[0] is results[1] is info
    assert calls == 1


@pytest.mark.asyncio
async def test_ensure_sandbox_cache_hit_with_dead_container_reprovisions() -> None:
    """DEFECT 3 fix: a cache hit whose container is no longer live (OOM-killed,
    manually removed) is evicted and re-provisioned with fresh creds, rather
    than handing back creds for a container that no longer exists."""
    orch, sandbox = _make_orchestrator()
    stale_info = _info(
        {"postgres": SandboxConnection(host="h", port=5432, password="pw-old")}
    )
    fresh_info = _info(
        {"postgres": SandboxConnection(host="h", port=5432, password="pw-new")}
    )
    sandbox.provision.side_effect = [stale_info, fresh_info]
    sandbox.is_live.return_value = False

    first = await orch.ensure_sandbox("dev-1", ["postgres"], ["postgres"])
    second = await orch.ensure_sandbox("dev-1", ["postgres"], ["postgres"])

    expected_provision_calls = 2
    assert first is stale_info
    assert second is fresh_info
    assert sandbox.provision.await_count == expected_provision_calls
    assert orch._sandbox_info["dev-1"] is fresh_info
    sandbox.is_live.assert_awaited_once_with("dev-1", ["postgres"])
