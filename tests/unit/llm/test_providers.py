"""Tests for the LLM agent provider seam.

Covers the ProviderRegistry, the ClaudeCodeProvider adapter, and the
GrokCliProvider (xAI Grok Build via the official ``grok`` CLI) — especially the
safety properties the Grok provider must hold:

  * the agent gets the MCP gateway wiring (reuses the orchestrator mount path);
  * the subscription auth (~/.grok) is mounted, and the provider routing fields
    are blanked so the grok endpoint is never mislabelled ANTHROPIC_*;
  * the prompt travels via env, so a leading ``--`` cannot become a CLI flag.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.llm.providers import (
    ClaudeCodeProvider,
    CodexCliProvider,
    GrokCliProvider,
    ProviderError,
    ProviderNotRegisteredError,
    ProviderRegistry,
    SpawnResult,
)
from roboco.models.base import ModelProvider
from roboco.models.runtime import OrchestratorAgentConfig


@pytest.fixture(autouse=True)
def _isolate_grok_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point GROK_AUTH_HOST_PATH at a fresh tmp dir so tests never mount the real
    ~/.grok. Tests that exercise the auth mount create ``auth.json`` themselves."""
    monkeypatch.setattr("roboco.llm.providers.grok.GROK_AUTH_HOST_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_codex_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CODEX_AUTH_HOST_PATH at a fresh tmp dir (parity with grok above)."""
    codex_dir = tmp_path / "codex-auth"
    monkeypatch.setattr(
        "roboco.llm.providers.codex.CODEX_AUTH_HOST_PATH", str(codex_dir)
    )
    return codex_dir


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
        self.remove_stop_reasons: list[str | None] = []
        self.spawn_args: tuple[object, ...] | None = None
        self.mount_config: OrchestratorAgentConfig | None = None
        self.data_dirs_ensured: list[str] = []

    async def _spawn_container(
        self,
        config: OrchestratorAgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> str:
        self.spawn_args = (config, initial_prompt, agent_settings_path)
        return "container-id-abc123"

    async def _remove_container(
        self, container_name: str, *, stop_reason: str | None = None
    ) -> None:
        self.removed.append(container_name)
        self.remove_stop_reasons.append(stop_reason)

    def _ensure_grok_usage_dir(self, agent_id: str) -> None:
        self.data_dirs_ensured.append(agent_id)

    def _ensure_codex_usage_dir(self, agent_id: str) -> None:
        self.data_dirs_ensured.append(agent_id)

    def _resolve_host_paths(
        self, config: OrchestratorAgentConfig, agent_settings_path: Path | None
    ) -> dict[str, str | None]:
        return {
            "mcp_config": str(config.mcp_config_path)
            if config.mcp_config_path
            else None,
            "settings": str(agent_settings_path) if agent_settings_path else None,
            "grok_usage": f"/host/data/grok-usage/{config.agent_id}",
            "codex_usage": f"/host/data/codex-usage/{config.agent_id}",
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
    provider = GrokCliProvider(_FakeHost())
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
    registry.register(ModelProvider.GROK, GrokCliProvider(_FakeHost()))
    registry.unregister(ModelProvider.GROK)
    assert not registry.is_registered(ModelProvider.GROK)
    registry.unregister(ModelProvider.GROK)  # idempotent


# ---------------------------------------------------------------------------
# GrokCliProvider
# ---------------------------------------------------------------------------


async def test_grok_spawn_requires_mcp_config() -> None:
    provider = GrokCliProvider(_FakeHost())
    with pytest.raises(ProviderError, match="MCP config"):
        await provider.spawn(_config(mcp_config_path=None))


async def test_grok_spawn_does_not_require_api_key() -> None:
    # Subscription auth (mounted ~/.grok) — a missing provider key is fine.
    host = _FakeHost()
    provider = GrokCliProvider(host)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())):
        result = await provider.spawn(_config(provider_auth_token=None))
    assert result.instance_id == "roboco-agent-be-dev-1"


async def test_grok_spawn_no_xai_key_and_no_anthropic_leak() -> None:
    host = _FakeHost()
    provider = GrokCliProvider(host, image="roboco-agent-grok:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(), initial_prompt="do the work")
    cmd = list(exec_mock.call_args.args)
    # The CLI authenticates from the mounted ~/.grok — the xAI key is never used.
    assert not any(c.startswith("XAI_API_KEY=") for c in cmd)
    # The provider endpoint must NOT be injected as an Anthropic var.
    assert not any(c.startswith("ANTHROPIC_BASE_URL=") for c in cmd)
    assert not any(c.startswith("ANTHROPIC_AUTH_TOKEN=") for c in cmd)
    # Provider fields were blanked before the shared mount step.
    assert host.mount_config is not None
    assert host.mount_config.provider_base_url is None
    assert host.mount_config.provider_auth_token is None


async def test_grok_spawn_wires_gateway_env_and_image_last() -> None:
    host = _FakeHost()
    provider = GrokCliProvider(host, image="roboco-agent-grok:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        result = await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    # Gateway + operational env the grok-cli entrypoint + renderer consume.
    assert "ROBOCO_MCP_CONFIG=/app/mcp-config.json" in cmd
    assert "ROBOCO_AGENT_ID=be-dev-1" in cmd  # renderer computes per-role flags
    assert "ROBOCO_AGENT_MODEL=grok-build" in cmd
    # No session id is injected: grok ignores a requested id, so the entrypoint
    # reads the real one back from the run log for usage capture.
    assert not any(c.startswith("ROBOCO_AGENT_SESSION_ID=") for c in cmd)
    # Usage capture: per-agent data dir mounted + the entrypoint's usage file.
    assert host.data_dirs_ensured == ["be-dev-1"]
    assert "/host/data/grok-usage/be-dev-1:/home/agent/.grok-usage" in cmd
    assert "ROBOCO_GROK_USAGE_FILE=/home/agent/.grok-usage/usage.json" in cmd
    # Identity wiring from the shared host helpers is present.
    assert "ROBOCO_AGENT_TOKEN=hmac-be-dev-1" in cmd
    # The image is the final docker-run argument.
    assert cmd[-1] == "roboco-agent-grok:test"
    assert host.removed == ["roboco-agent-be-dev-1"]
    assert host.remove_stop_reasons == ["pre_spawn_stale_clear"]
    assert result == SpawnResult(
        instance_id="roboco-agent-be-dev-1",
        extra={"container_id": "cid", "model": "grok-build"},
    )


async def test_grok_spawn_mounts_auth_when_present(_isolate_grok_auth: Path) -> None:
    (_isolate_grok_auth / "auth.json").write_text("{}", encoding="utf-8")
    host = _FakeHost()
    provider = GrokCliProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    # mount the host ~/.grok DIRECTORY (ro), not the single auth.json file — a
    # single-file bind mount pins the inode, so the orchestrator's atomic
    # auth.json refresh (rename) never reaches a running container.
    expected = f"{_isolate_grok_auth}:/home/agent/.grok-auth-ro:ro"
    assert expected in cmd


async def test_grok_spawn_omits_auth_mount_when_absent() -> None:
    # No auth.json in the (tmp) GROK_AUTH_HOST_PATH → no mount, no crash.
    host = _FakeHost()
    provider = GrokCliProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    assert not any("/home/agent/.grok-auth-ro" in c for c in cmd)


async def test_grok_spawn_warns_when_auth_absent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing host auth.json must not be silent — the spawn is doomed to
    exit 78, so the operator gets a spawn-time WARNING naming the missing file
    and the remediation (``grok login`` on the host). Without it the container
    silently started and only failed later at the entrypoint ``--check``.
    """
    caplog.set_level("WARNING", logger="roboco.llm.providers.grok")
    host = _FakeHost()
    provider = GrokCliProvider(host)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())):
        await provider.spawn(_config())
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "expected a spawn-time WARNING for the missing host auth.json"
    msg = warnings[0].getMessage()
    assert "auth.json" in msg
    assert "grok login" in msg  # names the remediation


async def test_grok_spawn_prompt_is_injection_safe() -> None:
    host = _FakeHost()
    provider = GrokCliProvider(host)
    nasty = "--model evil --session-id pwned"
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(), initial_prompt=nasty)
    cmd = list(exec_mock.call_args.args)
    # Passed only as an env value, never as a bare argv token.
    assert f"ROBOCO_INITIAL_PROMPT={nasty}" in cmd
    assert nasty not in cmd


async def test_grok_spawn_raises_on_docker_failure() -> None:
    provider = GrokCliProvider(_FakeHost())
    with (
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_proc(returncode=1, stderr=b"boom")),
        ),
        pytest.raises(ProviderError, match="boom"),
    ):
        await provider.spawn(_config())


# ---------------------------------------------------------------------------
# CodexCliProvider
# ---------------------------------------------------------------------------


def _codex_config(
    *,
    agent_id: str = "be-dev-1",
    provider_base_url: str | None = "https://api.x.ai/v1",
    provider_auth_token: str | None = "should-not-leak",
    mcp_config_path: Path | None = Path("/host/mcp-configs/be-dev-1.json"),
) -> OrchestratorAgentConfig:
    return OrchestratorAgentConfig(
        agent_id=agent_id,
        blueprint_path=Path("/app/system-prompt.md"),
        model="gpt-5.3-codex",
        mcp_config_path=mcp_config_path,
        claude_session_id="sess-1",
        provider_type="openai",
        provider_base_url=provider_base_url,
        provider_auth_token=provider_auth_token,
    )


async def test_codex_spawn_requires_mcp_config() -> None:
    provider = CodexCliProvider(_FakeHost())
    with pytest.raises(ProviderError, match="MCP config"):
        await provider.spawn(_codex_config(mcp_config_path=None))


async def test_codex_spawn_does_not_require_api_key() -> None:
    # Subscription auth (mounted ~/.codex) — a missing provider key is fine.
    host = _FakeHost()
    provider = CodexCliProvider(host)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())):
        result = await provider.spawn(_codex_config(provider_auth_token=None))
    assert result.instance_id == "roboco-agent-be-dev-1"


async def test_codex_spawn_no_leaked_key_and_no_anthropic_leak() -> None:
    host = _FakeHost()
    provider = CodexCliProvider(host, image="roboco-agent-codex:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_codex_config(), initial_prompt="do the work")
    cmd = list(exec_mock.call_args.args)
    assert not any(c.startswith("OPENAI_API_KEY=") for c in cmd)
    # The provider endpoint must NOT be injected as an Anthropic var.
    assert not any(c.startswith("ANTHROPIC_BASE_URL=") for c in cmd)
    assert not any(c.startswith("ANTHROPIC_AUTH_TOKEN=") for c in cmd)
    assert host.mount_config is not None
    assert host.mount_config.provider_base_url is None
    assert host.mount_config.provider_auth_token is None


async def test_codex_spawn_wires_gateway_env_and_image_last() -> None:
    host = _FakeHost()
    provider = CodexCliProvider(host, image="roboco-agent-codex:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        result = await provider.spawn(_codex_config())
    cmd = list(exec_mock.call_args.args)
    assert "ROBOCO_MCP_CONFIG=/app/mcp-config.json" in cmd
    assert "ROBOCO_AGENT_ID=be-dev-1" in cmd
    assert "ROBOCO_AGENT_MODEL=gpt-5.3-codex" in cmd
    # Usage capture: per-agent data dir mounted + the entrypoint's usage file.
    assert host.data_dirs_ensured == ["be-dev-1"]
    assert "/host/data/codex-usage/be-dev-1:/home/agent/.codex-usage" in cmd
    assert "ROBOCO_CODEX_USAGE_FILE=/home/agent/.codex-usage/usage.json" in cmd
    assert "ROBOCO_AGENT_TOKEN=hmac-be-dev-1" in cmd
    assert cmd[-1] == "roboco-agent-codex:test"
    assert host.removed == ["roboco-agent-be-dev-1"]
    assert host.remove_stop_reasons == ["pre_spawn_stale_clear"]
    assert result == SpawnResult(
        instance_id="roboco-agent-be-dev-1",
        extra={"container_id": "cid", "model": "gpt-5.3-codex"},
    )


async def test_codex_spawn_mounts_auth_when_present(
    _isolate_codex_auth: Path,
) -> None:
    _isolate_codex_auth.mkdir(parents=True, exist_ok=True)
    (_isolate_codex_auth / "auth.json").write_text("{}", encoding="utf-8")
    host = _FakeHost()
    provider = CodexCliProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_codex_config())
    cmd = list(exec_mock.call_args.args)
    # Mount the host ~/.codex DIRECTORY (ro), not the single auth.json file —
    # a single-file bind mount pins the inode (same concern grok documents).
    expected = f"{_isolate_codex_auth}:/home/agent/.codex-auth-ro:ro"
    assert expected in cmd


async def test_codex_spawn_omits_auth_mount_when_absent() -> None:
    host = _FakeHost()
    provider = CodexCliProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_codex_config())
    cmd = list(exec_mock.call_args.args)
    assert not any("/home/agent/.codex-auth-ro" in c for c in cmd)


async def test_codex_spawn_warns_when_auth_absent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="roboco.llm.providers.codex")
    host = _FakeHost()
    provider = CodexCliProvider(host)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())):
        await provider.spawn(_codex_config())
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "expected a spawn-time WARNING for the missing host auth.json"
    msg = warnings[0].getMessage()
    assert "auth.json" in msg
    assert "codex login" in msg


async def test_codex_spawn_prompt_is_injection_safe() -> None:
    host = _FakeHost()
    provider = CodexCliProvider(host)
    nasty = "--model evil --session-id pwned"
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_codex_config(), initial_prompt=nasty)
    cmd = list(exec_mock.call_args.args)
    assert f"ROBOCO_INITIAL_PROMPT={nasty}" in cmd
    assert nasty not in cmd


async def test_codex_spawn_raises_on_docker_failure() -> None:
    provider = CodexCliProvider(_FakeHost())
    with (
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_proc(returncode=1, stderr=b"boom")),
        ),
        pytest.raises(ProviderError, match="boom"),
    ):
        await provider.spawn(_codex_config())


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
    cc: Any = host
    cc._spawn_container = AsyncMock(side_effect=RuntimeError("docker down"))
    provider = ClaudeCodeProvider(host)
    with pytest.raises(ProviderError, match="docker down"):
        await provider.spawn(_config())


async def test_claude_remove_delegates_to_host() -> None:
    host = _FakeHost()
    provider = ClaudeCodeProvider(host)
    await provider.remove("roboco-agent-be-dev-1")
    assert host.removed == ["roboco-agent-be-dev-1"]
