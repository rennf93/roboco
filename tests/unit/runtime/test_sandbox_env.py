"""Sandbox env injection: `_append_sandbox_env` + the `_spawn_container` branch.

A sandbox-active spawn must inject `ROBOCO_TEST_DB_*` / `ROBOCO_TEST_REDIS_*`
pointed at the sandbox and MUST NOT also run the legacy `_append_gate_env`
prod-creds injection — sandbox replaces, never coexists with, prod creds.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import OrchestratorAgentConfig
from roboco.models.sandbox import SandboxConnection, SandboxInfo
from roboco.runtime.orchestrator import AgentOrchestrator


def _config(sandbox_info: SandboxInfo | None = None) -> OrchestratorAgentConfig:
    return OrchestratorAgentConfig(
        agent_id="dev-1",
        blueprint_path=Path(),
        mcp_config_path=Path("/tmp/mcp.json"),
        sandbox_info=sandbox_info,
    )


def test_append_sandbox_env_injects_postgres_and_redis() -> None:
    info = SandboxInfo(
        services={
            "postgres": SandboxConnection(
                host="roboco-sandbox-pg-dev-1",
                port=5432,
                password="pgpw",
                user="sandbox",
                database="sandbox",
            ),
            "redis": SandboxConnection(
                host="roboco-sandbox-redis-dev-1", port=6379, password="rdpw"
            ),
        }
    )
    cmd: list[str] = []
    AgentOrchestrator._append_sandbox_env(cmd, _config(info))

    assert "ROBOCO_TEST_DB_HOST=roboco-sandbox-pg-dev-1" in cmd
    assert "ROBOCO_TEST_DB_PORT=5432" in cmd
    assert "ROBOCO_TEST_DB_USER=sandbox" in cmd
    assert "ROBOCO_TEST_DB_PASSWORD=pgpw" in cmd
    assert "ROBOCO_TEST_DB_ADMIN_DB=sandbox" in cmd
    assert "ROBOCO_TEST_REDIS_HOST=roboco-sandbox-redis-dev-1" in cmd
    assert "ROBOCO_TEST_REDIS_PORT=6379" in cmd
    assert "ROBOCO_TEST_REDIS_PASSWORD=rdpw" in cmd


def test_append_sandbox_env_postgres_only_omits_redis_vars() -> None:
    info = SandboxInfo(
        services={
            "postgres": SandboxConnection(
                host="roboco-sandbox-pg-dev-1",
                port=5432,
                password="pgpw",
                user="sandbox",
                database="sandbox",
            )
        }
    )
    cmd: list[str] = []
    AgentOrchestrator._append_sandbox_env(cmd, _config(info))

    assert "ROBOCO_TEST_DB_HOST=roboco-sandbox-pg-dev-1" in cmd
    assert not any(v.startswith("ROBOCO_TEST_REDIS_") for v in cmd)


def test_append_sandbox_env_injects_mongo() -> None:
    info = SandboxInfo(
        services={
            "mongo": SandboxConnection(
                host="roboco-sandbox-mongo-dev-1",
                port=27017,
                password="mpw",
                user="sandbox",
                database="admin",
            )
        }
    )
    cmd: list[str] = []
    AgentOrchestrator._append_sandbox_env(cmd, _config(info))

    assert "ROBOCO_TEST_MONGO_HOST=roboco-sandbox-mongo-dev-1" in cmd
    assert "ROBOCO_TEST_MONGO_PORT=27017" in cmd
    assert "ROBOCO_TEST_MONGO_USER=sandbox" in cmd
    assert "ROBOCO_TEST_MONGO_PASSWORD=mpw" in cmd
    assert "ROBOCO_TEST_MONGO_AUTH_DB=admin" in cmd
    assert not any(v.startswith("ROBOCO_TEST_DB_") for v in cmd)


def test_append_sandbox_env_noop_without_sandbox_info() -> None:
    cmd: list[str] = []
    AgentOrchestrator._append_sandbox_env(cmd, _config(None))
    assert cmd == []


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
        "_append_sandbox_env",
        lambda *_a: calls.append("sandbox"),
    )
    monkeypatch.setattr(orch, "_append_image_and_claude_args", lambda *_a: None)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=_fake_proc())
    )


@pytest.mark.asyncio
async def test_spawn_container_uses_sandbox_env_when_sandbox_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    calls: list[str] = []
    _stub_spawn_container_collaborators(monkeypatch, orch, calls)

    info = SandboxInfo(
        services={
            "postgres": SandboxConnection(
                host="h", port=5432, password="pw", user="sandbox", database="sandbox"
            )
        }
    )
    await orch._spawn_container(_config(info))

    assert calls == ["sandbox"]


@pytest.mark.asyncio
async def test_spawn_container_uses_legacy_gate_env_without_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    calls: list[str] = []
    _stub_spawn_container_collaborators(monkeypatch, orch, calls)

    await orch._spawn_container(_config(None))

    assert calls == ["gate"]


@pytest.mark.asyncio
async def test_spawn_container_stale_clear_spares_fresh_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-spawn stale-clear must not tear down the sandbox that was
    just provisioned for this very spawn (teardown_sandbox=False)."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    calls: list[str] = []
    _stub_spawn_container_collaborators(monkeypatch, orch, calls)
    remove = AsyncMock(return_value=None)
    monkeypatch.setattr(orch, "_remove_container", remove)

    info = SandboxInfo(
        services={
            "postgres": SandboxConnection(
                host="h", port=5432, password="pw", user="sandbox", database="sandbox"
            )
        }
    )
    await orch._spawn_container(_config(info))

    remove.assert_awaited_once_with(
        "roboco-agent-dev-1",
        teardown_sandbox=False,
        stop_reason="pre_spawn_stale_clear",
    )
