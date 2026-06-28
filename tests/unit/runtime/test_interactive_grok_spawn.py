"""Interactive intake/secretary builders fork a GROK route onto the grok CLI.

A GROK route swaps the Claude SDK-driver image for the grok-CLI prompter/secretary
image and the ANTHROPIC_* env for the subscription auth mount + the per-agent
usage mount (no metered xAI key, no permission env — the driver computes the grok
permission flags). Every other provider keeps the Claude path's ANTHROPIC_*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.llm.providers import grok as grok_provider
from roboco.runtime.orchestrator import (
    GROK_PROMPTER_IMAGE,
    GROK_SECRETARY_IMAGE,
    AgentOrchestrator,
    _IntakeRunSpec,
    _SecretaryRunSpec,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_HOSTS: dict[str, str | None] = {
    "claude": "/h/.claude",
    "prompt": "/h/p.md",
    "workspaces": "/h/ws",
    "grok_usage": "/h/gu/intake-1",
}


def _intake_spec(
    provider_type: str, *, base_url: str | None, token: str | None
) -> _IntakeRunSpec:
    return _IntakeRunSpec(
        container_name="roboco-agent-intake-1",
        image=GROK_PROMPTER_IMAGE
        if provider_type == "grok"
        else "roboco-agent-prompter",
        hosts=_HOSTS,
        session_id="sess-1",
        cwd="/data/workspace",
        cli_model="grok-build",
        api_url="http://roboco-orchestrator:8000",
        provider_base_url=base_url,
        provider_auth_token=token,
        provider_type=provider_type,
        model="grok-build",
    )


def test_intake_grok_uses_grok_cli_usage_mount_and_env() -> None:
    cmd = AgentOrchestrator._build_intake_run_cmd(
        _intake_spec("grok", base_url="https://api.x.ai/v1", token="xai-key")
    )
    # The per-agent usage dir is mounted so finalize reads usage.json back.
    assert "/h/gu/intake-1:/home/agent/.grok-usage" in cmd
    assert "ROBOCO_AGENT_MODEL=grok-build" in cmd
    assert "ROBOCO_GROK_USAGE_FILE=/home/agent/.grok-usage/usage.json" in cmd
    assert cmd[-1] == GROK_PROMPTER_IMAGE
    # No metered xAI key, no Anthropic mislabelling, no stale opencode contract.
    assert not any(c.startswith("XAI_") for c in cmd)
    assert not any(c.startswith("ANTHROPIC_") for c in cmd)
    assert not any(c.startswith("ROBOCO_GROK_VARIANT") for c in cmd)
    assert not any(c.startswith("ROBOCO_GROK_EDIT_PERMISSION") for c in cmd)
    assert "/home/agent/.local/share/opencode" not in " ".join(cmd)


def test_intake_grok_mounts_subscription_auth_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The auth mount is .exists()-guarded; point the host dir at a tmp ~/.grok
    # holding an auth.json so the mount is emitted.
    grok_dir = tmp_path / ".grok"
    grok_dir.mkdir()
    (grok_dir / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(grok_provider, "GROK_AUTH_HOST_PATH", str(grok_dir))

    cmd = AgentOrchestrator._build_intake_run_cmd(
        _intake_spec("grok", base_url="https://api.x.ai/v1", token="xai-key")
    )
    # F005: directory mount (ro), not the single-file inode-pinning mount.
    assert f"{grok_dir}:/home/agent/.grok-auth-ro:ro" in cmd


def test_intake_anthropic_keeps_anthropic_env() -> None:
    cmd = AgentOrchestrator._build_intake_run_cmd(
        _intake_spec("anthropic", base_url="https://api.anthropic.com", token="sk-ant")
    )
    assert "ANTHROPIC_BASE_URL=https://api.anthropic.com" in cmd
    assert "ANTHROPIC_AUTH_TOKEN=sk-ant" in cmd
    assert not any(c.startswith("XAI_") for c in cmd)
    assert not any(c.startswith("ROBOCO_GROK_USAGE_FILE") for c in cmd)
    assert cmd[-1] == "roboco-agent-prompter"


def test_secretary_grok_uses_grok_cli_env_and_keeps_hmac() -> None:
    spec = _SecretaryRunSpec(
        container_name="roboco-agent-secretary-1",
        image=GROK_SECRETARY_IMAGE,
        hosts={
            "claude": "/h/.claude",
            "prompt": "/h/p.md",
            "grok_usage": "/h/gu/sec-1",
        },
        session_id="sess-2",
        cwd="/app",
        cli_model="grok-build",
        api_url="http://roboco-orchestrator:8000",
        agent_uuid="uuid-sec",
        agent_token="hmac-secretary",
        provider_base_url="https://api.x.ai/v1",
        provider_auth_token="xai-key",
        provider_type="grok",
        model="grok-build",
    )
    cmd = AgentOrchestrator._build_secretary_run_cmd(spec)
    assert "/h/gu/sec-1:/home/agent/.grok-usage" in cmd
    assert "ROBOCO_AGENT_MODEL=grok-build" in cmd
    # The HMAC identity the directive tools authenticate with survives.
    assert "ROBOCO_AGENT_TOKEN=hmac-secretary" in cmd
    assert cmd[-1] == GROK_SECRETARY_IMAGE
    assert not any(c.startswith("XAI_") for c in cmd)
    assert not any(c.startswith("ANTHROPIC_") for c in cmd)
