"""Unit tests for the provider system (roboco.llm.providers)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from roboco.llm.providers import (
    ClaudeCodeProvider,
    OllamaLocalProvider,
    ProviderRegistry,
)
from roboco.llm.providers.base import SpawnResult
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
    smoke tests verify the stop/health/remove paths via ``asyncio.create_subprocess_exec``
    which we mock heavily.
    """

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


class TestOllamaLocalProvider:
    """OllamaLocalProvider: subprocess-based agent lifecycle.

    These tests mock httpx (to avoid needing a real Ollama server) and
    os.kill/asyncio.create_subprocess_exec for process management.
    """

    @patch("roboco.llm.providers.ollama_local.OllamaLocalProvider._verify_ollama_reachable")
    @patch("roboco.llm.providers.ollama_local.asyncio.create_subprocess_exec")
    @patch("roboco.llm.providers.ollama_local.os.kill")
    async def test_spawn_and_stop(
        self,
        mock_kill: AsyncMock,
        mock_subproc: AsyncMock,
        mock_verify: AsyncMock,
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
        from roboco.llm.providers.base import ProviderError

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

    def __truediv__(self, other: object) -> "_FakePath":
        return _FakePath()


def _make_config(agent_id: str) -> object:
    """Build a minimal dict-like config for provider.spawn() tests."""
    from types import SimpleNamespace

    return SimpleNamespace(
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
