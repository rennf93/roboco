"""Interactive intake/secretary builders fork a GROK route onto opencode.

A GROK route swaps the Claude SDK-driver image for the opencode-serve image and
the ANTHROPIC_* env for OPENAI_* + the opencode store mount; every other
provider keeps the Claude path's ANTHROPIC_* behaviour.
"""

from __future__ import annotations

from roboco.runtime.orchestrator import (
    GROK_PROMPTER_IMAGE,
    GROK_SECRETARY_IMAGE,
    AgentOrchestrator,
    _IntakeRunSpec,
    _SecretaryRunSpec,
)

_HOSTS: dict[str, str | None] = {
    "claude": "/h/.claude",
    "prompt": "/h/p.md",
    "workspaces": "/h/ws",
    "opencode": "/h/oc/intake-1",
}


def _intake_spec(
    provider_type: str,
    *,
    base_url: str | None,
    token: str | None,
    grok_variant: str | None = None,
) -> _IntakeRunSpec:
    return _IntakeRunSpec(
        container_name="roboco-agent-intake-1",
        image=GROK_PROMPTER_IMAGE
        if provider_type == "grok"
        else "roboco-agent-prompter",
        hosts=_HOSTS,
        session_id="sess-1",
        cwd="/data/workspace",
        cli_model="grok-build-0.1",
        api_url="http://roboco-orchestrator:8000",
        provider_base_url=base_url,
        provider_auth_token=token,
        provider_type=provider_type,
        model="grok-build-0.1",
        grok_variant=grok_variant,
    )


def test_intake_grok_uses_openai_env_and_opencode_mount() -> None:
    cmd = AgentOrchestrator._build_intake_run_cmd(
        _intake_spec(
            "grok",
            base_url="https://api.x.ai/v1",
            token="xai-key",
            grok_variant="minimal",
        )
    )
    assert "OPENAI_BASE_URL=https://api.x.ai/v1" in cmd
    assert "OPENAI_API_KEY=xai-key" in cmd
    assert "ROBOCO_AGENT_MODEL=grok-build-0.1" in cmd
    assert "ROBOCO_SYSTEM_PROMPT=/app/system-prompt.md" in cmd
    assert "/h/oc/intake-1:/home/agent/.local/share/opencode" in cmd
    # Per-role reasoning effort reaches the container for the serve driver.
    assert "ROBOCO_GROK_VARIANT=minimal" in cmd
    assert cmd[-1] == GROK_PROMPTER_IMAGE
    # The xAI endpoint is never mislabelled as Anthropic.
    assert not any(c.startswith("ANTHROPIC_") for c in cmd)


def test_intake_grok_omits_variant_when_unset() -> None:
    cmd = AgentOrchestrator._build_intake_run_cmd(
        _intake_spec("grok", base_url="https://api.x.ai/v1", token="xai-key")
    )
    assert not any(c.startswith("ROBOCO_GROK_VARIANT=") for c in cmd)


def test_intake_anthropic_keeps_anthropic_env() -> None:
    cmd = AgentOrchestrator._build_intake_run_cmd(
        _intake_spec("anthropic", base_url="https://api.anthropic.com", token="sk-ant")
    )
    assert "ANTHROPIC_BASE_URL=https://api.anthropic.com" in cmd
    assert "ANTHROPIC_AUTH_TOKEN=sk-ant" in cmd
    assert not any(c.startswith("OPENAI_") for c in cmd)
    assert cmd[-1] == "roboco-agent-prompter"


def test_secretary_grok_uses_openai_env_and_grok_image() -> None:
    spec = _SecretaryRunSpec(
        container_name="roboco-agent-secretary-1",
        image=GROK_SECRETARY_IMAGE,
        hosts={"claude": "/h/.claude", "prompt": "/h/p.md", "opencode": "/h/oc/sec-1"},
        session_id="sess-2",
        cwd="/app",
        cli_model="grok-build-0.1",
        api_url="http://roboco-orchestrator:8000",
        agent_uuid="uuid-sec",
        agent_token="hmac-secretary",
        provider_base_url="https://api.x.ai/v1",
        provider_auth_token="xai-key",
        provider_type="grok",
        model="grok-build-0.1",
    )
    cmd = AgentOrchestrator._build_secretary_run_cmd(spec)
    assert "OPENAI_API_KEY=xai-key" in cmd
    assert "/h/oc/sec-1:/home/agent/.local/share/opencode" in cmd
    # The HMAC identity the directive tools authenticate with survives.
    assert "ROBOCO_AGENT_TOKEN=hmac-secretary" in cmd
    assert cmd[-1] == GROK_SECRETARY_IMAGE
    assert not any(c.startswith("ANTHROPIC_") for c in cmd)
