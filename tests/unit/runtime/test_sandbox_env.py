"""Sandbox marker env: `_append_sandbox_marker_env` + the `_spawn_container` branch.

An opted-in spawn injects a cheap `ROBOCO_SANDBOX_SERVICES_AVAILABLE` marker
(never prod creds — actual provisioning is on-demand via `request_sandbox`)
and MUST NOT also run the legacy `_append_gate_env` prod-creds injection —
the marker replaces, never coexists with, prod creds.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import OrchestratorAgentConfig
from roboco.runtime.orchestrator import AgentOrchestrator


def _config(
    sandbox_available_services: list[str] | None = None,
) -> OrchestratorAgentConfig:
    return OrchestratorAgentConfig(
        agent_id="dev-1",
        blueprint_path=Path(),
        mcp_config_path=Path("/tmp/mcp.json"),
        sandbox_available_services=sandbox_available_services or [],
    )


def test_append_sandbox_marker_env_lists_services() -> None:
    cmd: list[str] = []
    AgentOrchestrator._append_sandbox_marker_env(cmd, ["postgres", "redis"])

    assert "ROBOCO_SANDBOX_SERVICES_AVAILABLE=postgres,redis" in cmd


def test_append_sandbox_marker_env_single_service() -> None:
    cmd: list[str] = []
    AgentOrchestrator._append_sandbox_marker_env(cmd, ["mongo"])

    assert "ROBOCO_SANDBOX_SERVICES_AVAILABLE=mongo" in cmd


def _fake_proc() -> AsyncMock:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0
    return proc


def _stub_spawn_container_collaborators(
    monkeypatch: pytest.MonkeyPatch, orch: AgentOrchestrator, calls: list[str]
) -> None:
    monkeypatch.setattr(orch, "_provider_for", lambda *_a: None)
    monkeypatch.setattr(orch, "_remove_container", AsyncMock(return_value=None))
    monkeypatch.setattr(orch, "_resolve_host_paths", lambda *_a: {})
    monkeypatch.setattr(
        AgentOrchestrator,
        "_build_mount_args",
        staticmethod(lambda *_a: []),
    )
    monkeypatch.setattr(orch, "_append_agent_auth_env", lambda *_a: None)
    monkeypatch.setattr(orch, "_append_git_context_env", lambda *_a: None)
    monkeypatch.setattr(orch, "_append_gate_env", lambda *_a: calls.append("gate"))
    monkeypatch.setattr(
        orch,
        "_append_sandbox_marker_env",
        lambda *_a: calls.append("sandbox"),
    )
    monkeypatch.setattr(orch, "_append_image_and_claude_args", lambda *_a: None)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=_fake_proc())
    )


@pytest.mark.asyncio
async def test_spawn_container_uses_marker_env_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    calls: list[str] = []
    _stub_spawn_container_collaborators(monkeypatch, orch, calls)

    await orch._spawn_container(_config(["postgres"]))

    assert calls == ["sandbox"]


@pytest.mark.asyncio
async def test_spawn_container_uses_legacy_gate_env_when_not_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    calls: list[str] = []
    _stub_spawn_container_collaborators(monkeypatch, orch, calls)

    await orch._spawn_container(_config(None))

    assert calls == ["gate"]


@pytest.mark.asyncio
async def test_spawn_container_stale_clear_runs_with_teardown_sandbox_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-spawn stale-clear is vestigial now (nothing is provisioned
    before spawn) but still passes teardown_sandbox=False — it must not
    tear down a sandbox the agent requested moments ago via the verb."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    calls: list[str] = []
    _stub_spawn_container_collaborators(monkeypatch, orch, calls)
    remove = AsyncMock(return_value=None)
    monkeypatch.setattr(orch, "_remove_container", remove)

    await orch._spawn_container(_config(["postgres"]))

    remove.assert_awaited_once_with(
        "roboco-agent-dev-1",
        teardown_sandbox=False,
        stop_reason="pre_spawn_stale_clear",
    )
