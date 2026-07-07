"""_append_agent_auth_env mints an EXPIRING (ttl) token at spawn (M35)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from roboco.agents_config import (
    AGENT_UUIDS,
    get_agent_role,
    get_agent_team,
    verify_agent_token,
)
from roboco.config import settings
from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    import pytest


def _token_from_cmd(cmd: list[str]) -> str:
    # The injector uses cmd.extend(["-e", "ROBOCO_AGENT_TOKEN=<value>"]).
    for i, flag in enumerate(cmd):
        if (
            flag == "-e"
            and i + 1 < len(cmd)
            and cmd[i + 1].startswith("ROBOCO_AGENT_TOKEN=")
        ):
            return cmd[i + 1].split("=", 1)[1]
    raise AssertionError("ROBOCO_AGENT_TOKEN not found in cmd")


def test_append_agent_auth_env_mints_expiring_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "spawn-secret")
    monkeypatch.setattr(settings, "agent_token_ttl_seconds", 3600)

    cmd: list[str] = []
    config = AgentConfig(
        agent_id="be-dev-1",
        blueprint_path=Path("/app/blueprints/dev.md"),
        provider_type="anthropic",
    )
    AgentOrchestrator._append_agent_auth_env(cmd, config)
    token = _token_from_cmd(cmd)
    assert "." in token  # expiring format, not the static 64-hex digest
    uuid = AGENT_UUIDS.get("be-dev-1", "be-dev-1")
    role = get_agent_role("be-dev-1") or "developer"
    team = get_agent_team("be-dev-1") or "backend"
    assert verify_agent_token(token, uuid, role, team) is True
