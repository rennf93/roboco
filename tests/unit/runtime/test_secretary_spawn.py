"""roboco.runtime.orchestrator — Secretary docker-run cmd + host paths (pure)."""

from __future__ import annotations

from unittest.mock import MagicMock

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
