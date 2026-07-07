"""roboco.runtime.orchestrator — Secretary docker-run cmd + host paths (pure)."""

from __future__ import annotations

from unittest.mock import MagicMock

from roboco.agents_config import (
    get_agent_team,
    issue_agent_token,
    verify_agent_token,
)
from roboco.foundation.identity import AGENTS
from roboco.runtime.orchestrator import (
    SECRETARY_AGENT_ID,
    AgentOrchestrator,
    _SecretaryRunSpec,
)


def _spec() -> _SecretaryRunSpec:
    return _SecretaryRunSpec(
        container_name=f"roboco-agent-{SECRETARY_AGENT_ID}",
        image="roboco-agent-secretary",
        hosts={"claude": "/h/.claude", "prompt": "/h/p.md"},
        session_id="sid123",
        cwd="/app",
        cli_model="opus",
        api_url="http://x:8000",
        agent_uuid="uuid-1",
        agent_token="tok-1",
        provider_base_url=None,
        provider_auth_token=None,
    )


def test_build_secretary_run_cmd_wires_token_and_session() -> None:
    cmd = AgentOrchestrator._build_secretary_run_cmd(_spec())
    assert cmd[-1] == "roboco-agent-secretary"  # image is last
    assert "ROBOCO_AGENT_TOKEN=tok-1" in cmd
    assert "ROBOCO_AGENT_ID=uuid-1" in cmd
    assert "ROBOCO_AGENT_ROLE=secretary" in cmd
    assert "ROBOCO_SECRETARY_SESSION_ID=sid123" in cmd
    # No workspaces mount for the Secretary.
    assert not any("/data/workspaces" in part for part in cmd)


def test_build_secretary_run_cmd_adds_provider_env_when_set() -> None:
    spec = _spec()
    spec_with_provider = _SecretaryRunSpec(
        **{
            **spec.__dict__,
            "provider_base_url": "https://prov",
            "provider_auth_token": "ptok",
        }
    )
    cmd = AgentOrchestrator._build_secretary_run_cmd(spec_with_provider)
    assert "ANTHROPIC_BASE_URL=https://prov" in cmd
    assert "ANTHROPIC_AUTH_TOKEN=ptok" in cmd


def test_resolve_secretary_host_paths_has_claude_and_prompt() -> None:
    paths = AgentOrchestrator._resolve_secretary_host_paths(MagicMock())
    assert "claude" in paths
    assert "prompt" in paths
    assert SECRETARY_AGENT_ID in str(paths["prompt"])


def test_secretary_token_signs_over_real_team_not_empty(monkeypatch) -> None:
    """The secretary token must verify against the (id, role, team) the
    secretary driver actually sends. secretary_driver._headers sends
    X-Agent-Team = get_agent_team(uuid) = the secretary's real team ("board"),
    so the token issued at spawn (orchestrator _spawn_secretary_container) must
    be signed over that same team — not "" — or every /api/secretary/* call
    401s with "signature mismatch" under ROBOCO_AGENT_AUTH_REQUIRED.
    """
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "x" * 32)
    secretary = AGENTS[SECRETARY_AGENT_ID]
    agent_uuid = str(secretary.uuid)
    team = secretary.team.value
    assert team  # the bug was signing "" — the secretary IS on a team

    token = issue_agent_token(agent_uuid, "secretary", team)  # mirrors spawn line
    # secretary_driver._headers sends X-Agent-ID=uuid, role=secretary,
    # X-Agent-Team=get_agent_team(uuid).
    assert verify_agent_token(
        token, agent_uuid, "secretary", get_agent_team(agent_uuid)
    )
