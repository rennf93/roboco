"""Agent containers get their own ~/.claude/plugins, never the CEO's host copy."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from roboco.config import settings
from roboco.models.runtime import OrchestratorAgentConfig
from roboco.runtime.engines import spawn_launch
from roboco.runtime.orchestrator import (
    INTAKE_AGENT_ID,
    SECRETARY_AGENT_ID,
    AgentOrchestrator,
    _IntakeRunSpec,
    _SecretaryRunSpec,
)

CLAUDE_BIND = "/home/ceo/.claude:/home/agent/.claude"
SETGID_RWXRWXR_X = 0o2775
PLUGINS_TARGET = "/home/agent/.claude/plugins"
HOSTS: dict[str, str | None] = {"claude": "/home/ceo/.claude", "prompt": "/h/p.md"}


@pytest.fixture
def plugins_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    local = tmp_path / "agent-claude-plugins"
    monkeypatch.setattr(spawn_launch, "PROJECT_HOST_PATH", "/host/project")
    monkeypatch.setattr(spawn_launch, "DATA_HOST_PATH", "/host/data")
    monkeypatch.setattr(spawn_launch, "AGENT_PLUGINS_DATA_DIR", str(local))
    monkeypatch.setattr(settings, "agent_gid", os.getgid())
    return local


def _plugins_bind(cmd: list[str]) -> str:
    binds = [c for c in cmd if c.endswith(f":{PLUGINS_TARGET}")]
    assert len(binds) == 1
    return binds[0]


def _assert_after_claude_bind(cmd: list[str]) -> None:
    assert cmd.index(_plugins_bind(cmd)) > cmd.index(CLAUDE_BIND)
    assert _plugins_bind(cmd) == f"/host/data/agent-claude-plugins:{PLUGINS_TARGET}"


@pytest.mark.usefixtures("plugins_dir")
def test_agent_spawn_cmd_binds_own_plugins_after_claude() -> None:
    config = OrchestratorAgentConfig(agent_id="dev-1", blueprint_path=Path())
    cmd = AgentOrchestrator._build_mount_args(
        "c",
        config,
        {**HOSTS, "docs": "/h/docs", "workspaces": "/h/ws", "mcp_config": "/h/m.json"},
    )
    _assert_after_claude_bind(cmd)


@pytest.mark.usefixtures("plugins_dir")
def test_intake_cmd_binds_own_plugins_after_claude() -> None:
    spec = _IntakeRunSpec(
        container_name=f"roboco-agent-{INTAKE_AGENT_ID}",
        image="img",
        hosts={**HOSTS, "workspaces": "/h/ws"},
        session_id="s",
        cwd="/app",
        cli_model="m",
        api_url="http://x:8000",
        provider_base_url=None,
        provider_auth_token=None,
    )
    _assert_after_claude_bind(AgentOrchestrator._build_intake_run_cmd(spec))


@pytest.mark.usefixtures("plugins_dir")
def test_secretary_cmd_binds_own_plugins_after_claude() -> None:
    spec = _SecretaryRunSpec(
        container_name=f"roboco-agent-{SECRETARY_AGENT_ID}",
        image="img",
        hosts=HOSTS,
        session_id="s",
        cwd="/app",
        cli_model="m",
        api_url="http://x:8000",
        agent_uuid="u",
        agent_token="t",
        provider_base_url=None,
        provider_auth_token=None,
    )
    _assert_after_claude_bind(AgentOrchestrator._build_secretary_run_cmd(spec))


def test_plugins_dir_created_group_writable_before_run(plugins_dir: Path) -> None:
    assert not plugins_dir.exists()
    AgentOrchestrator._append_agent_plugins_mount([])
    assert plugins_dir.is_dir()
    assert stat.S_IMODE(plugins_dir.stat().st_mode) == SETGID_RWXRWXR_X
    assert plugins_dir.stat().st_gid == settings.agent_gid
    AgentOrchestrator._append_agent_plugins_mount([])  # idempotent
