"""Tests for GeminiCliProvider (Google Gemini via the official ``gemini`` CLI).

Mirrors ``tests/unit/llm/test_providers.py``'s Grok coverage — the same safety
properties matter here:

  * the agent gets the MCP gateway wiring (reuses the orchestrator mount path);
  * the OAuth credential (~/.gemini) is mounted, and the provider routing
    fields are blanked so the gemini endpoint is never mislabelled ANTHROPIC_*;
  * the prompt travels via env, so a leading ``--`` cannot become a CLI flag.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.llm.providers import GeminiCliProvider, ProviderError, SpawnResult
from roboco.models.runtime import OrchestratorAgentConfig


@pytest.fixture(autouse=True)
def _isolate_gemini_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point GEMINI_AUTH_HOST_PATH at a fresh tmp dir so tests never mount the
    real ~/.gemini. Tests that exercise the auth mount create oauth_creds.json
    themselves."""
    monkeypatch.setattr(
        "roboco.llm.providers.gemini.GEMINI_AUTH_HOST_PATH", str(tmp_path)
    )
    return tmp_path


def _config(
    *,
    agent_id: str = "be-dev-1",
    provider_type: str = "gemini",
    provider_base_url: str | None = None,
    provider_auth_token: str | None = None,
    mcp_config_path: Path | None = Path("/host/mcp-configs/be-dev-1.json"),
) -> OrchestratorAgentConfig:
    return OrchestratorAgentConfig(
        agent_id=agent_id,
        blueprint_path=Path("/app/system-prompt.md"),
        model="gemini-2.5-pro",
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

    def _ensure_gemini_usage_dir(self, agent_id: str) -> None:
        self.data_dirs_ensured.append(agent_id)

    def _resolve_host_paths(
        self, config: OrchestratorAgentConfig, agent_settings_path: Path | None
    ) -> dict[str, str | None]:
        return {
            "mcp_config": str(config.mcp_config_path)
            if config.mcp_config_path
            else None,
            "settings": str(agent_settings_path) if agent_settings_path else None,
            "gemini_usage": f"/host/data/gemini-usage/{config.agent_id}",
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


async def test_gemini_spawn_requires_mcp_config() -> None:
    provider = GeminiCliProvider(_FakeHost())
    with pytest.raises(ProviderError, match="MCP config"):
        await provider.spawn(_config(mcp_config_path=None))


async def test_gemini_spawn_does_not_require_api_key() -> None:
    # OAuth login (mounted ~/.gemini) — a missing provider key/url is fine.
    host = _FakeHost()
    provider = GeminiCliProvider(host)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())):
        result = await provider.spawn(_config(provider_auth_token=None))
    assert result.instance_id == "roboco-agent-be-dev-1"


async def test_gemini_spawn_no_anthropic_leak() -> None:
    host = _FakeHost()
    provider = GeminiCliProvider(host, image="roboco-agent-gemini:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(
            _config(provider_base_url="https://ignored", provider_auth_token="ignored"),
            initial_prompt="do the work",
        )
    cmd = list(exec_mock.call_args.args)
    # The provider endpoint must NOT be injected as an Anthropic var.
    assert not any(c.startswith("ANTHROPIC_BASE_URL=") for c in cmd)
    assert not any(c.startswith("ANTHROPIC_AUTH_TOKEN=") for c in cmd)
    # Provider fields were blanked before the shared mount step.
    assert host.mount_config is not None
    assert host.mount_config.provider_base_url is None
    assert host.mount_config.provider_auth_token is None


async def test_gemini_spawn_wires_gateway_env_and_image_last() -> None:
    host = _FakeHost()
    provider = GeminiCliProvider(host, image="roboco-agent-gemini:test")
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        result = await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    assert "ROBOCO_MCP_CONFIG=/app/mcp-config.json" in cmd
    assert "ROBOCO_AGENT_ID=be-dev-1" in cmd
    assert "ROBOCO_AGENT_MODEL=gemini-2.5-pro" in cmd
    # Usage capture: per-agent data dir mounted + the entrypoint's usage file.
    assert host.data_dirs_ensured == ["be-dev-1"]
    assert "/host/data/gemini-usage/be-dev-1:/home/agent/.gemini-usage" in cmd
    assert "ROBOCO_GEMINI_USAGE_FILE=/home/agent/.gemini-usage/usage.json" in cmd
    # Identity wiring from the shared host helpers is present.
    assert "ROBOCO_AGENT_TOKEN=hmac-be-dev-1" in cmd
    # The image is the final docker-run argument.
    assert cmd[-1] == "roboco-agent-gemini:test"
    assert host.removed == ["roboco-agent-be-dev-1"]
    assert host.remove_stop_reasons == ["pre_spawn_stale_clear"]
    assert result == SpawnResult(
        instance_id="roboco-agent-be-dev-1",
        extra={"container_id": "cid", "model": "gemini-2.5-pro"},
    )


async def test_gemini_spawn_mounts_auth_when_present(
    _isolate_gemini_auth: Path,
) -> None:
    (_isolate_gemini_auth / "oauth_creds.json").write_text("{}", encoding="utf-8")
    host = _FakeHost()
    provider = GeminiCliProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    expected = f"{_isolate_gemini_auth}:/home/agent/.gemini-auth-ro:ro"
    assert expected in cmd


async def test_gemini_spawn_omits_auth_mount_when_absent() -> None:
    # No oauth_creds.json in the (tmp) GEMINI_AUTH_HOST_PATH -> no mount, no crash.
    host = _FakeHost()
    provider = GeminiCliProvider(host)
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config())
    cmd = list(exec_mock.call_args.args)
    assert not any("/home/agent/.gemini-auth-ro" in c for c in cmd)


async def test_gemini_spawn_warns_when_auth_absent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing host oauth_creds.json must not be silent — the spawn is doomed
    to exit 41, so the operator gets a spawn-time WARNING naming the missing
    file and the remediation."""
    caplog.set_level("WARNING", logger="roboco.llm.providers.gemini")
    host = _FakeHost()
    provider = GeminiCliProvider(host)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())):
        await provider.spawn(_config())
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "expected a spawn-time WARNING for the missing oauth_creds.json"
    msg = warnings[0].getMessage()
    assert "oauth_creds.json" in msg
    assert "gemini" in msg  # names the remediation


async def test_gemini_spawn_prompt_is_injection_safe() -> None:
    host = _FakeHost()
    provider = GeminiCliProvider(host)
    nasty = "--model evil --approval-mode yolo-override"
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_proc())
    ) as exec_mock:
        await provider.spawn(_config(), initial_prompt=nasty)
    cmd = list(exec_mock.call_args.args)
    # Passed only as an env value, never as a bare argv token.
    assert f"ROBOCO_INITIAL_PROMPT={nasty}" in cmd
    assert nasty not in cmd


async def test_gemini_spawn_raises_on_docker_failure() -> None:
    provider = GeminiCliProvider(_FakeHost())
    with (
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_proc(returncode=1, stderr=b"boom")),
        ),
        pytest.raises(ProviderError, match="boom"),
    ):
        await provider.spawn(_config())


async def test_gemini_remove_delegates_to_host() -> None:
    host = _FakeHost()
    provider = GeminiCliProvider(host)
    await provider.remove("roboco-agent-be-dev-1")
    assert host.removed == ["roboco-agent-be-dev-1"]
