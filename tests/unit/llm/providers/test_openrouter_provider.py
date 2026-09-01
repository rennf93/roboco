"""Tests for OpenRouterProvider (any OpenRouter model via the opencode CLI).

Mirrors ``test_gemini_provider.py``'s Grok coverage — the same safety
properties matter here, with ONE decisive contrast: the Ollama shape, not the
Grok/Kimi/Codex/Gemini shape — a static metered API key injected via env
(OPENROUTER_API_KEY + OPENROUTER_BASE_URL), NO ``~/.`` auth mount, NO
``_auth.py`` refresh loop. The provider routing fields are blanked before the
shared mount step (same pattern) so the OpenRouter endpoint is never mislabelled
ANTHROPIC_*; this provider re-injects them under the OpenRouter env names.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.llm.providers import OpenRouterProvider, ProviderError, SpawnResult
from roboco.llm.providers import openrouter as openrouter_module
from roboco.models.runtime import OrchestratorAgentConfig


def test_openrouter_cli_model_is_a_real_settings_field() -> None:
    assert settings.openrouter_cli_model == openrouter_module._OPENROUTER_CLI_MODEL
    assert settings.openrouter_cli_model == "anthropic/claude-sonnet-4"


_OPENROUTER_MODEL = "anthropic/claude-sonnet-4"


def _config(
    *,
    agent_id: str = "be-dev-1",
    provider_type: str = "openrouter",
    provider_base_url: str | None = "https://openrouter.ai/api/v1",
    provider_auth_token: str | None = "sk-or-v1-test-key",
    mcp_config_path: Path | None = Path("/host/mcp-configs/be-dev-1.json"),
) -> OrchestratorAgentConfig:
    return OrchestratorAgentConfig(
        agent_id=agent_id,
        blueprint_path=Path("/app/system-prompt.md"),
        model=_OPENROUTER_MODEL,
        mcp_config_path=mcp_config_path,
        claude_session_id="sess-1",
        provider_type=provider_type,
        provider_base_url=provider_base_url,
        provider_auth_token=provider_auth_token,
    )


class _FakeHost:
    """Implements the orchestrator surface the provider delegates to."""

    def __init__(self) -> None:
        self.removed: list[str] = []
        self.remove_stop_reasons: list[str | None] = []
        self.mount_config: OrchestratorAgentConfig | None = None
        self.data_dirs_ensured: list[str] = []

    async def _remove_container(
        self, container_name: str, *, stop_reason: str | None = None
    ) -> None:
        self.removed.append(container_name)
        self.remove_stop_reasons.append(stop_reason)

    def _ensure_openrouter_usage_dir(self, agent_id: str) -> None:
        self.data_dirs_ensured.append(agent_id)

    def _resolve_host_paths(
        self, config: OrchestratorAgentConfig, agent_settings_path: Path | None
    ) -> dict[str, str | None]:
        return {
            "mcp_config": str(config.mcp_config_path)
            if config.mcp_config_path
            else None,
            "settings": str(agent_settings_path) if agent_settings_path else None,
            "openrouter_usage": f"/host/data/openrouter-usage/{config.agent_id}",
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
# Spawn preconditions
# ---------------------------------------------------------------------------


async def test_openrouter_spawn_requires_mcp_config() -> None:
    provider = OpenRouterProvider(_FakeHost())
    with pytest.raises(ProviderError, match="MCP config"):
        await provider.spawn(_config(mcp_config_path=None))


async def test_openrouter_spawn_requires_api_key() -> None:
    # The Ollama shape — the key is the spawn credential. No mounted ~/. to
    # fall back on; a missing key is a doomed spawn (exit 78 at runtime).
    provider = OpenRouterProvider(_FakeHost())
    with pytest.raises(ProviderError, match="OpenRouter API key"):
        await provider.spawn(_config(provider_auth_token=None))


# ---------------------------------------------------------------------------
# The Ollama shape — no auth mount, static key via env
# ---------------------------------------------------------------------------


async def test_openrouter_spawn_no_anthropic_leak() -> None:
    host = _FakeHost()
    provider = OpenRouterProvider(host, image="roboco-agent-openrouter:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    # The provider endpoint must NOT be injected as an Anthropic var.
    assert not any(c.startswith("ANTHROPIC_BASE_URL=") for c in cmd)
    assert not any(c.startswith("ANTHROPIC_AUTH_TOKEN=") for c in cmd)
    # Provider fields were blanked before the shared mount step.
    assert host.mount_config is not None
    assert host.mount_config.provider_base_url is None
    assert host.mount_config.provider_auth_token is None


async def test_openrouter_spawn_no_auth_mount() -> None:
    # The Ollama shape: NO ~/. auth mount — contrast kimi/codex/gemini/grok
    # which mount a subscription credential directory. OpenRouter injects the
    # static key via env, so no host auth dir is ever bind-mounted.
    host = _FakeHost()
    provider = OpenRouterProvider(host, image="roboco-agent-openrouter:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    assert not any(".openrouter-auth" in c for c in cmd)
    assert not any(".opencode-auth" in c for c in cmd)
    # No _auth.py module exists — the key is a plain env var.
    assert not hasattr(openrouter_module, "_auth")
    assert not hasattr(openrouter_module, "OPENROUTER_AUTH_HOST_PATH")


async def test_openrouter_spawn_injects_key_and_base_url_via_env() -> None:
    host = _FakeHost()
    provider = OpenRouterProvider(host, image="roboco-agent-openrouter:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(
            _config(
                provider_auth_token="sk-or-v1-secret",
                provider_base_url="https://openrouter.ai/api/v1",
            )
        )
    cmd = list(exec_mock.call_args.args)
    assert "OPENROUTER_API_KEY=sk-or-v1-secret" in cmd
    assert "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1" in cmd


# ---------------------------------------------------------------------------
# Gateway + identity + usage wiring
# ---------------------------------------------------------------------------


async def test_openrouter_spawn_wires_gateway_env_and_image_last() -> None:
    host = _FakeHost()
    provider = OpenRouterProvider(host, image="roboco-agent-openrouter:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        result = await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    assert "ROBOCO_MCP_CONFIG=/app/mcp-config.json" in cmd
    assert "ROBOCO_AGENT_ID=be-dev-1" in cmd
    assert "ROBOCO_AGENT_MODEL=anthropic/claude-sonnet-4" in cmd
    # Usage capture: per-agent data dir mounted + the entrypoint's usage file.
    assert host.data_dirs_ensured == ["be-dev-1"]
    assert "/host/data/openrouter-usage/be-dev-1:/home/agent/.opencode-usage" in cmd
    assert "ROBOCO_OPENROUTER_USAGE_FILE=/home/agent/.opencode-usage/usage.json" in cmd
    # Identity wiring from the shared host helpers is present.
    assert "ROBOCO_AGENT_TOKEN=hmac-be-dev-1" in cmd
    # The image is the final docker-run argument.
    assert cmd[-1] == "roboco-agent-openrouter:test"
    assert host.removed == ["roboco-agent-be-dev-1"]
    assert host.remove_stop_reasons == ["pre_spawn_stale_clear"]
    assert result == SpawnResult(
        instance_id="roboco-agent-be-dev-1",
        extra={"container_id": "cid", "model": "anthropic/claude-sonnet-4"},
    )


async def test_openrouter_spawn_adds_compose_labels_before_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_label_args(service: str) -> list[str]:
        return ["--label", f"com.docker.compose.service={service}"]

    monkeypatch.setattr(
        "roboco.llm.providers.openrouter.compose_label_args", _fake_label_args
    )
    host = _FakeHost()
    provider = OpenRouterProvider(host, image="roboco-agent-openrouter:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    assert "com.docker.compose.service=be-dev-1" in cmd
    assert cmd.index("com.docker.compose.service=be-dev-1") < cmd.index(
        "roboco-agent-openrouter:test"
    )
    assert cmd[-1] == "roboco-agent-openrouter:test"


# ---------------------------------------------------------------------------
# Prompt injection safety (PR #170 gap #2 fix)
# ---------------------------------------------------------------------------


async def test_openrouter_spawn_prompt_is_injection_safe() -> None:
    host = _FakeHost()
    provider = OpenRouterProvider(host)
    nasty = "--model evil --auto --format json --dangerous-flag"
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(), initial_prompt=nasty)
    cmd = list(exec_mock.call_args.args)
    # Passed only as an env value, never as a bare argv token.
    assert f"ROBOCO_INITIAL_PROMPT={nasty}" in cmd
    assert nasty not in cmd


async def test_openrouter_spawn_empty_prompt_when_none() -> None:
    host = _FakeHost()
    provider = OpenRouterProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(), initial_prompt=None)
    cmd = list(exec_mock.call_args.args)
    assert "ROBOCO_INITIAL_PROMPT=" in cmd


# ---------------------------------------------------------------------------
# Error handling + remove
# ---------------------------------------------------------------------------


async def test_openrouter_spawn_raises_on_docker_failure() -> None:
    provider = OpenRouterProvider(_FakeHost())
    with (
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_proc(returncode=1, stderr=b"boom")),
        ),
        pytest.raises(ProviderError, match="boom"),
    ):
        await provider.spawn(_config())


async def test_openrouter_remove_delegates_to_host() -> None:
    host = _FakeHost()
    provider = OpenRouterProvider(host)
    await provider.remove("roboco-agent-be-dev-1")
    assert host.removed == ["roboco-agent-be-dev-1"]


# ---------------------------------------------------------------------------
# Optional attribution headers
# ---------------------------------------------------------------------------


async def test_openrouter_spawn_includes_attribution_headers_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openrouter_http_referer", "https://roboco.tech")
    monkeypatch.setattr(settings, "openrouter_x_title", "RoboCo")
    host = _FakeHost()
    provider = OpenRouterProvider(host, image="roboco-agent-openrouter:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    assert "HTTP_REFERER=https://roboco.tech" in cmd
    assert "X_OPENROUTER_TITLE=RoboCo" in cmd


async def test_openrouter_spawn_omits_empty_referer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openrouter_http_referer", "")
    monkeypatch.setattr(settings, "openrouter_x_title", "RoboCo")
    host = _FakeHost()
    provider = OpenRouterProvider(host, image="roboco-agent-openrouter:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    assert not any(c.startswith("HTTP_REFERER=") for c in cmd)
    assert "X_OPENROUTER_TITLE=RoboCo" in cmd
