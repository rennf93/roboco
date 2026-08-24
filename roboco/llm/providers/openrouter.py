"""OpenRouter provider — any of OpenRouter's models via the opencode CLI.

OpenRouter (https://openrouter.ai) exposes hundreds of models (GLM/Z.AI,
DeepSeek, Qwen, Claude, GPT, ...) behind one metered API key speaking the
OpenAI Chat Completions protocol. RoboCo runs OpenRouter agents through the
opencode CLI (``opencode run``) the same way it runs Kimi agents through the
``kimi`` CLI: the orchestrator's shared container assembly mounts the RoboCo
MCP gateway (``mcp-config.json``), the agent HMAC identity, and the git
context; this provider adds the runtime env the opencode entrypoint reads
(OPENROUTER_API_KEY + OPENROUTER_BASE_URL + the rendered opencode.json), then
launches the ``roboco-agent-openrouter`` image — whose entrypoint renders the
opencode config (see :mod:`roboco.llm.providers.openrouter_cli_config`) and
runs ``opencode run`` headless.

The Ollama shape, not Grok (the decisive contrast with kimi/codex/gemini):
  * **Auth** — a static metered API key injected via env
    (``OPENROUTER_API_KEY`` + ``OPENROUTER_BASE_URL``), NOT a mounted
    subscription ``~/.`` credential. No ``_auth.py`` refresh loop (the key is
    static, never rotates server-side; OpenRouter does not use OAuth refresh
    tokens). No ``ROBOCO_HOST_OPENROUTER_DIR`` host mount exists. The provider
    routing fields ARE the key source here: ``provider_auth_token`` is the
    OpenRouter key and ``provider_base_url`` is the endpoint — they are
    injected as ``OPENROUTER_API_KEY`` / ``OPENROUTER_BASE_URL`` (NOT
    ``ANTHROPIC_*``), so the shared builder is handed a blanked mount_config
    (the kimi/codex pattern) to suppress the wrong-runtime injection, and this
    provider re-injects them under the OpenRouter env names.
  * **Runtime** — the ``roboco-agent-openrouter`` image (opencode CLI) instead
    of ``claude``.

The initial prompt travels as an **env var, not a positional CLI arg** (the
entrypoint folds it into a single quoted ``opencode run`` argv token), which
structurally avoids a flag-injection vector (the PR #170 gap #2 fix).

**V1 scope**: one-shot delivery roles only (developer / qa / documenter /
cell_pm / main_pm / pr_reviewer / board). No interactive intake/secretary
support — there is no ``roboco-agent-openrouter-prompter`` / ``-secretary``
image.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, Protocol

from roboco.config import settings
from roboco.llm.providers._docker import container_running, stop_container
from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult
from roboco.runtime.compose_labels import compose_label_args

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig

_log = logging.getLogger(__name__)

# The OpenRouter agent image (own image, like every other agent role).
_DEFAULT_OPENROUTER_IMAGE = "roboco-agent-openrouter:latest"

# The opencode CLI default model — a floor only; the routing assignment pins
# the real model id per agent (the live catalog is searched on demand).
_OPENROUTER_CLI_MODEL = settings.openrouter_cli_model

# In-container paths.
_MCP_CONFIG_IN_CONTAINER = "/app/mcp-config.json"
# Per-agent data dir (the host side is reused from the shared assembly): the
# entrypoint writes the captured token usage here so the orchestrator reads it
# back at finalize, the openrouter analogue of the mounted Claude transcript.
_OPENROUTER_USAGE_DIR_IN_CONTAINER = "/home/agent/.opencode-usage"
_OPENROUTER_USAGE_FILE_IN_CONTAINER = f"{_OPENROUTER_USAGE_DIR_IN_CONTAINER}/usage.json"


def _container_name(agent_id: str) -> str:
    return f"roboco-agent-{agent_id}"


class _OpenRouterHost(Protocol):
    """The orchestrator surface OpenRouterProvider reuses for container assembly.

    Typed as a Protocol so this module never imports ``AgentOrchestrator`` (no
    import cycle) and is trivially mockable in tests.
    """

    async def _remove_container(
        self, container_name: str, *, stop_reason: str | None = None
    ) -> None: ...

    def _ensure_openrouter_usage_dir(self, agent_id: str) -> None: ...

    def _resolve_host_paths(
        self, config: AgentConfig, agent_settings_path: Path | None
    ) -> dict[str, str | None]: ...

    def _build_mount_args(
        self,
        container_name: str,
        config: AgentConfig,
        hosts: dict[str, str | None],
    ) -> list[str]: ...

    def _append_agent_auth_env(self, cmd: list[str], config: AgentConfig) -> None: ...

    def _append_git_context_env(self, cmd: list[str], config: AgentConfig) -> None: ...


class OpenRouterProvider(AgentProvider):
    """Spawn an OpenRouter (opencode CLI) agent as a gateway-wired container.

    The Ollama shape: a static metered API key + base URL injected via env,
    no ``~/.`` auth mount, no refresh loop — contrast
    :class:`~roboco.llm.providers.kimi.KimiCliProvider` /
    :class:`~roboco.llm.providers.codex.CodexCliProvider` which mount a
    subscription credential.
    """

    def __init__(self, host: _OpenRouterHost, image: str | None = None) -> None:
        self._host = host
        self._image = image or _DEFAULT_OPENROUTER_IMAGE

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        if not config.mcp_config_path:
            raise ProviderError(
                "OPENROUTER spawn requires an MCP config (gateway access).",
                agent_id=config.agent_id,
            )
        if not config.provider_auth_token:
            # The OpenRouter API key is the spawn credential — without it the
            # container starts but the entrypoint's auth preflight refuses to
            # run (exit 78) and the agent is doomed. Fail loud at spawn time so
            # the operator sees the missing key immediately.
            raise ProviderError(
                "OPENROUTER spawn requires an OpenRouter API key "
                "(provider_auth_token); set one via the routing service "
                "(PUT /api/providers/openrouter-key).",
                agent_id=config.agent_id,
            )

        container_name = _container_name(config.agent_id)
        await self._host._remove_container(
            container_name, stop_reason="pre_spawn_stale_clear"
        )
        # Pre-create the per-agent data dir (world-writable) before the bind
        # mount so the non-root agent can write the usage file (else EACCES).
        self._host._ensure_openrouter_usage_dir(config.agent_id)

        # Reuse the orchestrator's mount/auth/git assembly so the agent gets
        # the full MCP gateway + identity wiring. Blank the provider routing
        # fields first: otherwise the shared builder would inject the
        # OpenRouter endpoint as ANTHROPIC_BASE_URL/AUTH_TOKEN (the wrong
        # runtime) — OpenRouter authenticates from OPENROUTER_API_KEY, which
        # this provider re-injects below under the OpenRouter env names.
        mount_config = dataclasses.replace(
            config, provider_base_url=None, provider_auth_token=None
        )
        hosts = self._host._resolve_host_paths(config, agent_settings_path)
        cmd = self._host._build_mount_args(container_name, mount_config, hosts)
        self._host._append_agent_auth_env(cmd, config)
        self._host._append_git_context_env(cmd, config)
        # No auth mount — the Ollama shape (static key via env, no ~/. mount).
        self._append_usage_mount(cmd, hosts)
        self._append_openrouter_env(cmd, config, initial_prompt)
        cmd.extend(await compose_label_args(config.agent_id))
        cmd.append(self._image)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ProviderError(
                f"Failed to start OpenRouter container: {stderr.decode().strip()}",
                agent_id=config.agent_id,
            )
        return SpawnResult(
            instance_id=container_name,
            extra={
                "container_id": stdout.decode().strip(),
                "model": config.model or _OPENROUTER_CLI_MODEL,
            },
        )

    @staticmethod
    def _append_usage_mount(cmd: list[str], hosts: dict[str, str | None]) -> None:
        """Mount the per-agent data dir so the orchestrator reads usage back.

        Reuses the shared per-agent host dir (``hosts["openrouter_usage"]``);
        the entrypoint writes ``usage.json`` here after the run. Without it an
        OpenRouter agent finalizes at 0 tokens / $0.
        """
        data_host = hosts.get("openrouter_usage")
        if data_host:
            cmd.extend(["-v", f"{data_host}:{_OPENROUTER_USAGE_DIR_IN_CONTAINER}"])

    def _append_openrouter_env(
        self, cmd: list[str], config: AgentConfig, initial_prompt: str | None
    ) -> None:
        """Append the runtime env the opencode entrypoint + renderer read.

        ``OPENROUTER_API_KEY`` / ``OPENROUTER_BASE_URL`` are the static metered
        credential (the Ollama shape — no refresh loop); ``HTTP_REFERER`` /
        ``X_OPENROUTER_TITLE`` are OpenRouter's optional attribution headers
        (surfaced on its usage dashboard). ``ROBOCO_AGENT_ID`` lets the
        renderer compute the per-role permission deny-rules; ``ROBOCO_MCP_CONFIG``
        points it at the mounted gateway config; the prompt travels as an env
        var (never an argv positional).
        """
        base_url = config.provider_base_url or settings.openrouter_base_url
        model = config.model or _OPENROUTER_CLI_MODEL
        env: list[str] = [
            "-e",
            f"ROBOCO_AGENT_ID={config.agent_id}",
            "-e",
            f"ROBOCO_AGENT_MODEL={model}",
            "-e",
            f"ROBOCO_MCP_CONFIG={_MCP_CONFIG_IN_CONTAINER}",
            "-e",
            f"ROBOCO_INITIAL_PROMPT={initial_prompt or ''}",
            "-e",
            f"ROBOCO_OPENROUTER_USAGE_FILE={_OPENROUTER_USAGE_FILE_IN_CONTAINER}",
            "-e",
            f"OPENROUTER_API_KEY={config.provider_auth_token or ''}",
            "-e",
            f"OPENROUTER_BASE_URL={base_url}",
        ]
        # Optional OpenRouter attribution headers (surfaced on the usage
        # dashboard). Empty HTTP_REFERER is omitted; X_OPENROUTER_TITLE defaults
        # to "RoboCo" via settings.
        if settings.openrouter_http_referer:
            env.extend(["-e", f"HTTP_REFERER={settings.openrouter_http_referer}"])
        if settings.openrouter_x_title:
            env.extend(["-e", f"X_OPENROUTER_TITLE={settings.openrouter_x_title}"])
        cmd.extend(env)

    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        await stop_container(instance_id, graceful)

    async def health_check(self, instance_id: str) -> bool:
        return await container_running(instance_id)

    async def remove(self, instance_id: str) -> None:
        await self._host._remove_container(instance_id)
