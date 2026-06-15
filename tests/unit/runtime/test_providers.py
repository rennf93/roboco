"""Unit tests for the provider system (roboco.llm.providers)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from roboco.llm.providers import (
    ClaudeCodeProvider,
    OllamaLocalProvider,
    ProviderRegistry,
)
from roboco.llm.providers.base import ProviderError, SpawnResult
from roboco.models.base import ModelProvider


class TestProviderRegistry:
    """ProviderRegistry: registration, lookup, and lifecycle."""

    def test_register_and_get(self) -> None:
        registry = ProviderRegistry()
        provider = ClaudeCodeProvider()
        registry.register(ModelProvider.ANTHROPIC, provider)
        assert registry.get(ModelProvider.ANTHROPIC) is provider

    def test_get_unregistered_raises(self) -> None:
        registry = ProviderRegistry()
        with pytest.raises(LookupError, match="No provider registered"):
            registry.get(ModelProvider.OPENAI)

    def test_is_registered(self) -> None:
        registry = ProviderRegistry()
        assert not registry.is_registered(ModelProvider.LOCAL)
        registry.register(ModelProvider.LOCAL, OllamaLocalProvider())
        assert registry.is_registered(ModelProvider.LOCAL)

    def test_unregister(self) -> None:
        registry = ProviderRegistry()
        registry.register(ModelProvider.ANTHROPIC, ClaudeCodeProvider())
        assert registry.is_registered(ModelProvider.ANTHROPIC)
        registry.unregister(ModelProvider.ANTHROPIC)
        assert not registry.is_registered(ModelProvider.ANTHROPIC)

    def test_registered_types(self) -> None:
        registry = ProviderRegistry()
        registry.register(ModelProvider.ANTHROPIC, ClaudeCodeProvider())
        registry.register(ModelProvider.LOCAL, OllamaLocalProvider())
        types = registry.registered_types()
        assert ModelProvider.ANTHROPIC in types
        assert ModelProvider.LOCAL in types
        assert ModelProvider.OPENAI not in types


class TestClaudeCodeProvider:
    """ClaudeCodeProvider: Docker lifecycle wrappers.

    Most tests use the orchestrator's existing ``_spawn_container`` — these
    smoke tests verify the stop/health/remove paths via
    ``asyncio.create_subprocess_exec`` which we mock heavily.
    """

    # ======================================================================
    # Existing smoke tests (stop / health / remove)
    # ======================================================================

    @patch("roboco.llm.providers.claude_code.asyncio.create_subprocess_exec")
    async def test_stop_graceful(self, mock_subproc: AsyncMock) -> None:
        mock_proc = AsyncMock()
        mock_proc.wait.return_value = 0
        mock_subproc.return_value = mock_proc

        provider = ClaudeCodeProvider()
        await provider.stop("roboco-agent-be-dev-1", graceful=True)

        mock_subproc.assert_called_once()
        args = mock_subproc.call_args[0]
        assert "docker" in args
        assert "stop" in args
        assert "roboco-agent-be-dev-1" in args

    @patch("roboco.llm.providers.claude_code.asyncio.create_subprocess_exec")
    async def test_stop_force(self, mock_subproc: AsyncMock) -> None:
        mock_proc = AsyncMock()
        mock_proc.wait.return_value = 0
        mock_subproc.return_value = mock_proc

        provider = ClaudeCodeProvider()
        await provider.stop("roboco-agent-be-dev-1", graceful=False)

        args = mock_subproc.call_args[0]
        assert "kill" in args

    @patch("roboco.llm.providers.claude_code.asyncio.create_subprocess_exec")
    async def test_health_check_running(self, mock_subproc: AsyncMock) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"running\n", b"")
        mock_subproc.return_value = mock_proc

        provider = ClaudeCodeProvider()
        result = await provider.health_check("roboco-agent-be-dev-1")
        assert result is True

    @patch("roboco.llm.providers.claude_code.asyncio.create_subprocess_exec")
    async def test_health_check_not_running(self, mock_subproc: AsyncMock) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"exited\n", b"")
        mock_subproc.return_value = mock_proc

        provider = ClaudeCodeProvider()
        result = await provider.health_check("roboco-agent-be-dev-1")
        assert result is False

    @patch("roboco.llm.providers.claude_code.asyncio.create_subprocess_exec")
    async def test_remove(self, mock_subproc: AsyncMock) -> None:
        mock_proc = AsyncMock()
        mock_proc.wait.return_value = 0
        mock_subproc.return_value = mock_proc

        provider = ClaudeCodeProvider()
        await provider.remove("roboco-agent-be-dev-1")

        args = mock_subproc.call_args[0]
        assert "rm" in args

    # ======================================================================
    # New: _container_name
    # ======================================================================

    def test_container_name(self) -> None:
        provider = ClaudeCodeProvider()
        name = provider._container_name("be-dev-1")
        assert name == "roboco-agent-be-dev-1"

    # ======================================================================
    # New: _default_spawn_prompt
    # ======================================================================

    def test_default_spawn_prompt(self) -> None:
        prompt = ClaudeCodeProvider._default_spawn_prompt()
        assert "give_me_work()" in prompt
        assert "UNDERSTAND" in prompt

    # ======================================================================
    # New: _resolve_host_paths — two branches
    # ======================================================================

    def test_resolve_host_paths_with_project_root(self, tmp_path: object) -> None:
        """When PROJECT_HOST_PATH is falsy, fall back to project_root."""
        provider = ClaudeCodeProvider(project_root=Path(str(tmp_path)))
        config = _make_config("be-dev-1")
        # patch PROJECT_HOST_PATH to empty so we hit the fallback branch
        with patch("roboco.llm.providers.claude_code.PROJECT_HOST_PATH", ""):
            hosts = provider._resolve_host_paths(config, None)

        assert hosts["docs"] == str(tmp_path / "docs")
        assert hosts["workspaces"] is not None
        assert hosts["claude"] is not None
        assert hosts["settings"] is None  # no agent_settings_path
        assert hosts["briefing"] is None  # no briefing_path

    @patch("roboco.llm.providers.claude_code.PROJECT_HOST_PATH", "/data/project")
    def test_resolve_host_paths_with_project_host_path(self) -> None:
        """When PROJECT_HOST_PATH is set, use it."""
        provider = ClaudeCodeProvider()
        config = _make_config("be-dev-1")
        hosts = provider._resolve_host_paths(config, Path("/tmp/settings.json"))

        assert hosts["docs"] == "/data/project/docs"
        assert hosts["settings"] is not None
        assert "settings.json" in str(hosts["settings"])

    @patch("roboco.llm.providers.claude_code.PROJECT_HOST_PATH", "/data/project")
    def test_resolve_host_paths_with_briefing(self) -> None:
        """Briefing path is included when config.briefing_path is set."""
        provider = ClaudeCodeProvider()
        config = _make_config("be-dev-1", briefing_path=Path("/tmp/brief.md"))
        hosts = provider._resolve_host_paths(config, None)

        assert hosts["briefing"] is not None
        assert "be-dev-1.md" in str(hosts["briefing"])

    # ======================================================================
    # New: _append_claude_json_mount
    # ======================================================================

    @patch("roboco.llm.providers.claude_code.Path.exists", return_value=True)
    def test_append_claude_json_mount_exists(self, _mock_exists: AsyncMock) -> None:
        cmd: list[str] = []
        hosts = {"claude": "/data/claude"}
        ClaudeCodeProvider._append_claude_json_mount(cmd, hosts)
        assert any(".claude.json" in arg for arg in cmd)

    @patch("roboco.llm.providers.claude_code.Path.exists", return_value=False)
    def test_append_claude_json_mount_missing(self, _mock_exists: AsyncMock) -> None:
        cmd: list[str] = []
        hosts = {"claude": "/data/claude"}
        ClaudeCodeProvider._append_claude_json_mount(cmd, hosts)
        assert cmd == []  # nothing appended

    # ======================================================================
    # New: _append_optional_host_mounts
    # ======================================================================

    def test_append_optional_host_mounts_settings_only(self) -> None:
        cmd: list[str] = []
        hosts = {"settings": "/data/settings.json", "briefing": None}
        ClaudeCodeProvider._append_optional_host_mounts(cmd, hosts)
        assert "settings.json:ro" in " ".join(cmd)

    def test_append_optional_host_mounts_briefing_only(self) -> None:
        cmd: list[str] = []
        hosts = {"settings": None, "briefing": "/data/brief.md"}
        ClaudeCodeProvider._append_optional_host_mounts(cmd, hosts)
        joined = " ".join(cmd)
        assert "brief.md" in joined
        assert "briefing.md:ro" in joined

    def test_append_optional_host_mounts_none(self) -> None:
        cmd: list[str] = []
        hosts = {"settings": None, "briefing": None}
        ClaudeCodeProvider._append_optional_host_mounts(cmd, hosts)
        assert cmd == []

    # ======================================================================
    # New: _append_provider_env
    # ======================================================================

    def test_append_provider_env_with_base_url(self) -> None:
        cmd: list[str] = []
        config = _make_config("be-dev-1", provider_base_url="http://proxy:8080")
        ClaudeCodeProvider._append_provider_env(cmd, config)
        joined = " ".join(cmd)
        assert "ANTHROPIC_BASE_URL=http://proxy:8080" in joined

    def test_append_provider_env_with_auth_token(self) -> None:
        cmd: list[str] = []
        config = _make_config("be-dev-1", provider_auth_token="sk-test")
        ClaudeCodeProvider._append_provider_env(cmd, config)
        joined = " ".join(cmd)
        assert "ANTHROPIC_AUTH_TOKEN=sk-test" in joined

    def test_append_provider_env_with_both(self) -> None:
        cmd: list[str] = []
        config = _make_config(
            "be-dev-1",
            provider_base_url="http://proxy:8080",
            provider_auth_token="sk-test",
        )
        ClaudeCodeProvider._append_provider_env(cmd, config)
        joined = " ".join(cmd)
        assert "ANTHROPIC_BASE_URL" in joined
        assert "ANTHROPIC_AUTH_TOKEN" in joined

    def test_append_provider_env_none(self) -> None:
        cmd: list[str] = []
        config = _make_config("be-dev-1")
        ClaudeCodeProvider._append_provider_env(cmd, config)
        assert cmd == []

    # ======================================================================
    # New: _append_manifest_args
    # ======================================================================

    @patch(
        "roboco.llm.providers.claude_code._build_manifest_for_agent",
        return_value="/data/manifests/be-dev-1.json",
    )
    def test_append_manifest_args_with_manifest(self, _mock_build: AsyncMock) -> None:
        cmd: list[str] = []
        config = _make_config("be-dev-1")
        ClaudeCodeProvider._append_manifest_args(cmd, config, "sonnet")
        joined = " ".join(cmd)
        assert "ROBOCO_GATEWAY_ENABLED=true" in joined
        assert "tool-manifest.json" in joined

    @patch(
        "roboco.llm.providers.claude_code._build_manifest_for_agent",
        return_value=None,
    )
    def test_append_manifest_args_no_manifest(self, _mock_build: AsyncMock) -> None:
        cmd: list[str] = []
        config = _make_config("be-dev-1")
        ClaudeCodeProvider._append_manifest_args(cmd, config, "sonnet")
        joined = " ".join(cmd)
        assert "ROBOCO_GATEWAY_ENABLED=false" in joined

    # ======================================================================
    # New: _append_git_context_env
    # ======================================================================

    def test_append_git_context_env_with_context(self) -> None:
        cmd: list[str] = []
        gc = SimpleNamespace(project_slug="roboco", branch_name="main")
        config = _make_config("be-dev-1", git_context=gc)
        ClaudeCodeProvider._append_git_context_env(cmd, config)
        joined = " ".join(cmd)
        assert "ROBOCO_PROJECT_SLUG=roboco" in joined
        assert "ROBOCO_BRANCH=main" in joined

    def test_append_git_context_env_no_context(self) -> None:
        cmd: list[str] = []
        config = _make_config("be-dev-1", git_context=None)
        ClaudeCodeProvider._append_git_context_env(cmd, config)
        assert cmd == []

    # ======================================================================
    # New: _append_image_and_claude_args
    # ======================================================================

    def test_append_image_and_claude_args_with_session(self) -> None:
        cmd: list[str] = []
        config = _make_config("be-dev-1")
        ClaudeCodeProvider._append_image_and_claude_args(cmd, config, "Go")
        joined = " ".join(cmd)
        assert "--session-id" in joined
        assert "test-session-123" in joined
        assert "-p" in joined
        assert "Go" in joined  # initial_prompt

    def test_append_image_and_claude_args_no_session(self) -> None:
        cmd: list[str] = []
        config = _make_config("be-dev-1", claude_session_id=None)
        ClaudeCodeProvider._append_image_and_claude_args(cmd, config, "Go")
        joined = " ".join(cmd)
        assert "--session-id" not in joined
        assert "-p" in joined
        assert "Go" in joined

    def test_append_image_and_claude_args_default_prompt(self) -> None:
        cmd: list[str] = []
        config = _make_config("be-dev-1", claude_session_id=None)
        ClaudeCodeProvider._append_image_and_claude_args(cmd, config, None)
        joined = " ".join(cmd)
        assert "give_me_work()" in joined  # default prompt

    # ======================================================================
    # New: _core_volume_and_env_args
    # ======================================================================

    def test_core_volume_and_env_args(self) -> None:
        hosts = {
            "prompt": "/data/prompt.md",
            "docs": "/data/docs",
            "workspaces": "/data/workspaces",
            "mcp_config": "/data/mcp.json",
        }
        config = _make_config("be-dev-1")
        args = ClaudeCodeProvider._core_volume_and_env_args(config, hosts, "developer")
        joined = " ".join(args)
        assert "/data/prompt.md" in joined
        assert "ROBOCO_AGENT_ID=be-dev-1" in joined
        assert "ROBOCO_AGENT_ROLE=developer" in joined

    # ======================================================================
    # New: spawn — full lifecycle
    # ======================================================================

    @patch("roboco.llm.providers.claude_code.asyncio.create_subprocess_exec")
    @patch("roboco.llm.providers.claude_code.ClaudeCodeProvider._resolve_host_paths")
    @patch("roboco.llm.providers.claude_code.PROJECT_HOST_PATH", "/data/project")
    async def test_spawn_success(
        self,
        mock_resolve_hosts: AsyncMock,
        mock_subproc: AsyncMock,
    ) -> None:
        """Happy path: spawn returns a SpawnResult with container ID."""
        # _remove_container: first two calls (inspect + rm)
        # spawn: final call (docker run)
        not_found = AsyncMock()
        not_found.wait.return_value = 1  # container doesn't exist
        rm_ok = AsyncMock()
        rm_ok.wait.return_value = 0
        docker_run = AsyncMock()
        docker_run.communicate.return_value = (
            b"abc123def456\n",
            b"",
        )
        docker_run.returncode = 0
        mock_subproc.side_effect = [not_found, rm_ok, docker_run]

        mock_resolve_hosts.return_value = {
            "docs": "/data/docs",
            "workspaces": "/data/workspaces",
            "claude": "/data/claude",
            "mcp_config": "/data/mcp.json",
            "prompt": "/data/prompt.md",
            "settings": None,
            "briefing": None,
        }

        provider = ClaudeCodeProvider()
        config = _make_config("be-dev-1")
        config.mcp_config_path = Path("/data/mcp.json")

        with patch(
            "roboco.llm.providers.claude_code.Path.exists",
            return_value=False,
        ):
            result = await provider.spawn(config, initial_prompt="Build it")

        assert isinstance(result, SpawnResult)
        assert result.instance_id == "abc123def456"
        assert result.agent_state == "active"

    @patch("roboco.llm.providers.claude_code.asyncio.create_subprocess_exec")
    @patch("roboco.llm.providers.claude_code.PROJECT_HOST_PATH", "/data/project")
    async def test_spawn_fails_without_mcp_config(
        self,
        mock_subproc: AsyncMock,
    ) -> None:
        """Missing MCP config raises ProviderError before spawning."""
        # _remove_container fires 2 subprocess calls (inspect + rm)
        not_found = AsyncMock()
        not_found.wait.return_value = 1
        rm_ok = AsyncMock()
        rm_ok.wait.return_value = 0
        # spawn() raises before docker run, so only need 2 mocks
        mock_subproc.side_effect = [not_found, rm_ok]

        provider = ClaudeCodeProvider()
        config = _make_config("be-dev-1")
        config.mcp_config_path = None  # no MCP config

        with pytest.raises(ProviderError, match="MCP config path not set"):
            await provider.spawn(config)

    @patch("roboco.llm.providers.claude_code.asyncio.create_subprocess_exec")
    @patch("roboco.llm.providers.claude_code.PROJECT_HOST_PATH", "/data/project")
    async def test_spawn_docker_run_fails(
        self,
        mock_subproc: AsyncMock,
    ) -> None:
        """Non-zero returncode from docker run raises ProviderError."""
        not_found = AsyncMock()
        not_found.wait.return_value = 1
        rm_ok = AsyncMock()
        rm_ok.wait.return_value = 0
        failed = AsyncMock()
        failed.communicate.return_value = (
            b"",
            b"docker: Error response from daemon.",
        )
        failed.returncode = 1
        mock_subproc.side_effect = [not_found, rm_ok, failed]

        provider = ClaudeCodeProvider()
        config = _make_config("be-dev-1")
        config.mcp_config_path = Path("/data/mcp.json")

        with pytest.raises(ProviderError, match="Failed to start container"):
            await provider.spawn(config)

    # ======================================================================
    # New: _build_mount_args — composed command structure
    # ======================================================================

    def test_build_mount_args_structure(self) -> None:
        """Verify the docker run command has expected structure."""
        hosts = {
            "docs": "/data/docs",
            "workspaces": "/data/workspaces",
            "claude": "/data/claude",
            "mcp_config": "/data/mcp.json",
            "prompt": "/data/prompt.md",
            "settings": None,
            "briefing": None,
        }
        config = _make_config("be-dev-1")
        cmd = ClaudeCodeProvider._build_mount_args(
            "roboco-agent-be-dev-1", config, hosts
        )

        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert cmd[2] == "-d"
        assert "--name" in cmd
        assert "roboco-agent-be-dev-1" in cmd


class TestOllamaLocalProvider:
    """OllamaLocalProvider: subprocess-based agent lifecycle.

    These tests mock httpx (to avoid needing a real Ollama server) and
    os.kill/asyncio.create_subprocess_exec for process management.
    """

    @patch(
        "roboco.llm.providers.ollama_local.OllamaLocalProvider._verify_ollama_reachable"
    )
    @patch("roboco.llm.providers.ollama_local.asyncio.create_subprocess_exec")
    @patch("roboco.llm.providers.ollama_local.os.kill")
    async def test_spawn_and_stop(
        self,
        mock_kill: AsyncMock,
        mock_subproc: AsyncMock,
        _mock_verify: AsyncMock,
    ) -> None:
        """Test full spawn → stop cycle with a mocked Ollama server."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = (b"", b"")
        mock_subproc.return_value = mock_proc

        provider = OllamaLocalProvider(
            ollama_base_url="http://localhost:11434",
            log_dir="/tmp/ollama-test",
        )

        # Spawn: needs a config-like object.
        config = _make_config("be-dev-1")

        with patch.object(provider, "_log_dir") as mock_log_dir:
            mock_log_dir.__truediv__.return_value = _FakePath()
            result = await provider.spawn(config, initial_prompt="Hello")

        assert isinstance(result, SpawnResult)
        assert result.instance_id == "12345"
        assert result.extra["model"] == "sonnet"

        # Stop the process.
        await provider.stop("12345", graceful=True)
        mock_kill.assert_called_with(12345, 15)  # SIGTERM

    @patch("roboco.llm.providers.ollama_local.httpx.AsyncClient")
    async def test_verify_reachable_ok(self, mock_client_cls: AsyncMock) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        provider = OllamaLocalProvider(ollama_base_url="http://localhost:11434")
        # Should not raise.
        await provider._verify_ollama_reachable()

    @patch("roboco.llm.providers.ollama_local.httpx.AsyncClient")
    async def test_verify_reachable_fails(self, mock_client_cls: AsyncMock) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("connection refused")
        mock_client_cls.return_value = mock_client

        provider = OllamaLocalProvider(ollama_base_url="http://localhost:11434")
        with pytest.raises(ProviderError, match="unreachable"):
            await provider._verify_ollama_reachable()


# =========================================================================
# Helpers
# =========================================================================


class _FakePath:
    """Minimal Path stand-in for OllamaLocalProvider's log dir operations."""

    def mkdir(self, **kwargs: object) -> None:
        pass

    def write_text(self, text: str) -> None:
        self.text = text

    def __truediv__(self, other: object) -> _FakePath:
        return _FakePath()


def _make_config(agent_id: str, **overrides: object) -> object:
    """Build a minimal dict-like config for provider.spawn() tests."""
    base = SimpleNamespace(
        agent_id=agent_id,
        model="sonnet",
        claude_session_id="test-session-123",
        mcp_config_path=None,
        provider_type="local",
        provider_base_url=None,
        provider_auth_token=None,
        git_context=None,
        briefing_path=None,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base
