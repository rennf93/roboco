"""Tests for the LLM agent provider seam.

Covers the ProviderRegistry, the ClaudeCodeProvider adapter, and the
GrokProvider (xAI / OpenAI protocol) — especially the safety properties an
OpenAI-protocol agent provider must hold:

  * the agent gets the MCP gateway wiring (reuses the orchestrator mount path);
  * the xAI endpoint is injected as OPENAI_* and never mislabelled ANTHROPIC_*;
  * the prompt travels via env, so a leading ``--`` cannot become a CLI flag.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.llm.providers import (
    ClaudeCodeProvider,
    GrokProvider,
    ProviderError,
    ProviderNotRegisteredError,
    ProviderRegistry,
    SpawnResult,
)
from roboco.llm.providers.grok import (
    _bash_permission_for,
    _edit_permission_for,
    _external_dir_permission_for,
    _reasoning_effort_for,
)
from roboco.models.base import ModelProvider
from roboco.models.runtime import OrchestratorAgentConfig


def _config(
    *,
    agent_id: str = "be-dev-1",
    provider_type: str = "grok",
    provider_base_url: str | None = "https://api.x.ai/v1",
    provider_auth_token: str | None = "xai-secret-key",
    mcp_config_path: Path | None = Path("/host/mcp-configs/be-dev-1.json"),
) -> OrchestratorAgentConfig:
    return OrchestratorAgentConfig(
        agent_id=agent_id,
        blueprint_path=Path("/app/system-prompt.md"),
        model="grok-build-0.1",
        mcp_config_path=mcp_config_path,
        claude_session_id="sess-1",
        provider_type=provider_type,
        provider_base_url=provider_base_url,
        provider_auth_token=provider_auth_token,
    )


class _FakeHost:
    """Implements the orchestrator surface the providers delegate to."""

    def __init__(self) -> None:
        self.removed: list[str] = []
        self.spawn_args: tuple[object, ...] | None = None
        self.mount_config: OrchestratorAgentConfig | None = None
        self.opencode_dirs_ensured: list[str] = []

    async def _spawn_container(
        self,
        config: OrchestratorAgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> str:
        self.spawn_args = (config, initial_prompt, agent_settings_path)
        return "container-id-abc123"

    async def _remove_container(self, container_name: str) -> None:
        self.removed.append(container_name)

    def _ensure_opencode_data_dir(self, agent_id: str) -> None:
        self.opencode_dirs_ensured.append(agent_id)

    def _resolve_host_paths(
        self, config: OrchestratorAgentConfig, agent_settings_path: Path | None
    ) -> dict[str, str | None]:
        return {
            "mcp_config": str(config.mcp_config_path)
            if config.mcp_config_path
            else None,
            "settings": str(agent_settings_path) if agent_settings_path else None,
            "opencode": f"/host/opencode/{config.agent_id}",
        }

    def _build_mount_args(
        self,
        container_name: str,
        config: OrchestratorAgentConfig,
        hosts: dict[str, str | None],
    ) -> list[str]:
        # Record the config the mount step saw, and MIMIC the real
        # _append_provider_env so a missed blanking would leak ANTHROPIC_*.
        self.mount_config = config
        cmd = ["docker", "run", "-d", "--name", container_name]
        mcp = hosts.get("mcp_config")
        if mcp:
            cmd += ["-v", f"{mcp}:/app/mcp-config.json:ro"]
        if config.provider_base_url:
            cmd += ["-e", f"ANTHROPIC_BASE_URL={config.provider_base_url}"]
        if config.provider_auth_token:
            cmd += ["-e", f"ANTHROPIC_AUTH_TOKEN={config.provider_auth_token}"]
        return cmd

    def _append_agent_auth_env(
        self, cmd: list[str], config: OrchestratorAgentConfig
    ) -> None:
        cmd += ["-e", f"ROBOCO_AGENT_TOKEN=hmac-{config.agent_id}"]

    def _append_git_context_env(
        self, cmd: list[str], config: OrchestratorAgentConfig
    ) -> None:
        cmd += ["-e", f"ROBOCO_GIT_AGENT={config.agent_id}"]


def _proc(
    returncode: int = 0, stdout: bytes = b"cid\n", stderr: bytes = b""
) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ---------------------------------------------------------------------------
# ProviderRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_get() -> None:
    registry = ProviderRegistry()
    provider = GrokProvider(_FakeHost())
    registry.register(ModelProvider.GROK, provider)
    assert registry.get(ModelProvider.GROK) is provider
    assert registry.is_registered(ModelProvider.GROK)
    assert registry.registered_types() == [ModelProvider.GROK]


def test_registry_get_unregistered_raises() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderNotRegisteredError):
        registry.get(ModelProvider.GROK)


def test_registry_get_or_none_returns_none_when_absent() -> None:
    registry = ProviderRegistry()
    assert registry.get_or_none(ModelProvider.ANTHROPIC) is None


def test_registry_unregister() -> None:
    registry = ProviderRegistry()
    registry.register(ModelProvider.GROK, GrokProvider(_FakeHost()))
    registry.unregister(ModelProvider.GROK)
    assert not registry.is_registered(ModelProvider.GROK)
    registry.unregister(ModelProvider.GROK)  # idempotent


# ---------------------------------------------------------------------------
# GrokProvider
# ---------------------------------------------------------------------------


async def test_grok_spawn_requires_api_key() -> None:
    provider = GrokProvider(_FakeHost())
    with pytest.raises(ProviderError, match="xAI API key"):
        await provider.spawn(_config(provider_auth_token=None))


async def test_grok_spawn_requires_mcp_config() -> None:
    provider = GrokProvider(_FakeHost())
    with pytest.raises(ProviderError, match="MCP config"):
        await provider.spawn(_config(mcp_config_path=None))


async def test_grok_spawn_injects_openai_env_and_no_anthropic_leak() -> None:
    host = _FakeHost()
    provider = GrokProvider(host, image="roboco-agent-grok:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(), initial_prompt="do the work")
    cmd = list(exec_mock.call_args.args)
    assert "OPENAI_BASE_URL=https://api.x.ai/v1" in cmd
    assert "OPENAI_API_KEY=xai-secret-key" in cmd
    # The xAI endpoint must NOT be injected as an Anthropic var.
    assert not any(c.startswith("ANTHROPIC_BASE_URL=") for c in cmd)
    assert not any(c.startswith("ANTHROPIC_AUTH_TOKEN=") for c in cmd)
    # Provider fields were blanked before the shared mount step.
    assert host.mount_config is not None
    assert host.mount_config.provider_base_url is None
    assert host.mount_config.provider_auth_token is None


async def test_grok_spawn_wires_gateway_and_image_last() -> None:
    host = _FakeHost()
    provider = GrokProvider(host, image="roboco-agent-grok:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        result = await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    # Gateway + operational env the grok image entrypoint consumes.
    assert "ROBOCO_MCP_CONFIG=/app/mcp-config.json" in cmd
    assert "ROBOCO_SYSTEM_PROMPT=/app/system-prompt.md" in cmd
    # Tool restriction lives in the rendered opencode.json (opencode `tools`),
    # not a spawn env var — no ROBOCO_AGENT_TOOLS is injected.
    assert not any(c.startswith("ROBOCO_AGENT_TOOLS=") for c in cmd)
    # The opencode store is mounted so the orchestrator can read usage/cost
    # back at finalize.
    assert "/host/opencode/be-dev-1:/home/agent/.local/share/opencode" in cmd
    # Identity wiring from the shared host helpers is present.
    assert "ROBOCO_AGENT_TOKEN=hmac-be-dev-1" in cmd
    # The image is the final docker-run argument.
    assert cmd[-1] == "roboco-agent-grok:test"
    assert host.removed == ["roboco-agent-be-dev-1"]
    assert result == SpawnResult(
        instance_id="roboco-agent-be-dev-1",
        extra={"container_id": "cid", "model": "grok-build-0.1"},
    )


async def test_grok_spawn_prompt_is_injection_safe() -> None:
    host = _FakeHost()
    provider = GrokProvider(host)
    nasty = "--model evil --session-id pwned"
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(), initial_prompt=nasty)
    cmd = list(exec_mock.call_args.args)
    # Passed only as an env value, never as a bare argv token.
    assert f"ROBOCO_INITIAL_PROMPT={nasty}" in cmd
    assert nasty not in cmd


async def test_grok_spawn_defaults_base_url_when_route_blank() -> None:
    provider = GrokProvider(_FakeHost())
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(provider_base_url=None))
    cmd = list(exec_mock.call_args.args)
    assert "OPENAI_BASE_URL=https://api.x.ai/v1" in cmd


async def test_grok_spawn_raises_on_docker_failure() -> None:
    provider = GrokProvider(_FakeHost())
    with (
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_proc(returncode=1, stderr=b"boom")),
        ),
        pytest.raises(ProviderError, match="boom"),
    ):
        await provider.spawn(_config())


# ---------------------------------------------------------------------------
# Reasoning effort by role
# ---------------------------------------------------------------------------


def test_reasoning_effort_full_for_code_roles() -> None:
    # developer / qa / pr_reviewer keep full reasoning (no variant).
    assert _reasoning_effort_for("be-dev-1") is None
    assert _reasoning_effort_for("be-qa") is None
    assert _reasoning_effort_for("pr-reviewer-1") is None


def test_reasoning_effort_minimal_for_coordination_roles() -> None:
    for slug in ("be-pm", "main-pm", "be-doc", "auditor", "product-owner"):
        assert _reasoning_effort_for(slug) == "minimal", slug


def test_reasoning_effort_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_GROK_REASONING_EFFORT", "max")
    assert _reasoning_effort_for("be-dev-1") == "max"  # override wins over role
    monkeypatch.setenv("ROBOCO_GROK_REASONING_EFFORT", "default")
    assert _reasoning_effort_for("be-pm") is None  # "default" => full reasoning


# ---------------------------------------------------------------------------
# Per-role opencode permissions (Claude-parity)
# ---------------------------------------------------------------------------


def test_edit_permission_allows_only_writer_roles() -> None:
    assert _edit_permission_for("be-dev-1") == "allow"
    assert _edit_permission_for("be-doc") == "allow"
    for slug in ("be-qa", "pr-reviewer-1", "be-pm", "main-pm", "auditor"):
        assert _edit_permission_for(slug) == "deny", slug


def test_bash_permission_allows_only_shell_roles() -> None:
    for slug in ("be-dev-1", "be-doc", "be-pm", "main-pm"):
        assert _bash_permission_for(slug) == "allow", slug
    for slug in ("be-qa", "pr-reviewer-1", "auditor", "product-owner"):
        assert _bash_permission_for(slug) == "deny", slug


def test_external_dir_permission_only_pr_reviewer() -> None:
    assert _external_dir_permission_for("pr-reviewer-1") == "allow"
    for slug in ("be-dev-1", "be-qa", "be-pm", "auditor"):
        assert _external_dir_permission_for(slug) == "deny", slug


async def test_grok_spawn_sets_readonly_permissions_for_reviewer() -> None:
    # A read-only reviewer (qa) gets edit=deny + bash=deny + external_dir=deny.
    host = _FakeHost()
    provider = GrokProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(agent_id="be-qa"))
    cmd = list(exec_mock.call_args.args)
    assert "ROBOCO_GROK_EDIT_PERMISSION=deny" in cmd
    assert "ROBOCO_GROK_BASH_PERMISSION=deny" in cmd
    assert "ROBOCO_GROK_EXTERNAL_DIR_PERMISSION=deny" in cmd


async def test_grok_spawn_pr_reviewer_is_read_only_but_reads_scratch() -> None:
    # The pr-reviewer never writes code (edit=deny) but reads its /tmp diff
    # (external_directory=allow) — the one role that needs it.
    host = _FakeHost()
    provider = GrokProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(agent_id="pr-reviewer-1"))
    cmd = list(exec_mock.call_args.args)
    assert "ROBOCO_GROK_EDIT_PERMISSION=deny" in cmd
    assert "ROBOCO_GROK_EXTERNAL_DIR_PERMISSION=allow" in cmd


async def test_grok_spawn_sets_variant_for_minimal_role() -> None:
    host = _FakeHost()
    provider = GrokProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(agent_id="be-pm"))  # cell_pm -> minimal
    cmd = list(exec_mock.call_args.args)
    assert "ROBOCO_GROK_VARIANT=minimal" in cmd


async def test_grok_spawn_no_variant_for_dev_role() -> None:
    host = _FakeHost()
    provider = GrokProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(agent_id="be-dev-1"))  # developer -> full
    cmd = list(exec_mock.call_args.args)
    assert not any(c.startswith("ROBOCO_GROK_VARIANT=") for c in cmd)


# ---------------------------------------------------------------------------
# ClaudeCodeProvider
# ---------------------------------------------------------------------------


async def test_claude_spawn_delegates_to_host() -> None:
    host = _FakeHost()
    provider = ClaudeCodeProvider(host)
    result = await provider.spawn(_config(provider_type="anthropic"), "prompt")
    assert host.spawn_args is not None
    assert result.instance_id == "roboco-agent-be-dev-1"
    assert result.extra["container_id"] == "container-id-abc123"


async def test_claude_spawn_wraps_host_error() -> None:
    host = _FakeHost()
    host._spawn_container = AsyncMock(side_effect=RuntimeError("docker down"))  # type: ignore[method-assign]
    provider = ClaudeCodeProvider(host)
    with pytest.raises(ProviderError, match="docker down"):
        await provider.spawn(_config())


async def test_claude_remove_delegates_to_host() -> None:
    host = _FakeHost()
    provider = ClaudeCodeProvider(host)
    await provider.remove("roboco-agent-be-dev-1")
    assert host.removed == ["roboco-agent-be-dev-1"]
