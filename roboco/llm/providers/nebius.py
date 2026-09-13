"""Nebius Token Factory provider - NVIDIA Nemotron + 60 open models via opencode.

Nebius Token Factory (https://tokenfactory.nebius.com) is Nebius's
OpenAI-compatible inference API serving 60+ open models (NVIDIA Nemotron,
DeepSeek, Qwen, Llama, ...) behind one metered API key speaking the OpenAI
Chat Completions protocol at ``https://api.tokenfactory.nebius.com/v1``.
RoboCo runs Nebius agents through the opencode CLI (``opencode run``) the
same way it runs OpenRouter agents: the orchestrator's shared container
assembly mounts the RoboCo MCP gateway (``mcp-config.json``), the agent HMAC
identity, and the git context; this provider adds the runtime env the
opencode entrypoint reads (NEBIUS_API_KEY + NEBIUS_BASE_URL + the rendered
opencode.json), then launches the ``roboco-agent-nebius`` image - whose
entrypoint renders the opencode config (see
:mod:`roboco.llm.providers.nebius_cli_config`) and runs ``opencode run``
headless.

The Ollama shape, not Grok (the decisive contrast with kimi/codex/gemini):
  * **Auth** - a static metered API key injected via env
    (``NEBIUS_API_KEY`` + ``NEBIUS_BASE_URL``), NOT a mounted
    subscription ``~/.`` credential. No ``_auth.py`` refresh loop (the key is
    static, never rotates server-side; Token Factory keys are plain bearer
    tokens). No ``ROBOCO_HOST_NEBIUS_DIR`` host mount exists. The provider
    routing fields ARE the key source here: ``provider_auth_token`` is the
    Nebius key and ``provider_base_url`` is the endpoint - they are
    injected as ``NEBIUS_API_KEY`` / ``NEBIUS_BASE_URL`` (NOT
    ``ANTHROPIC_*``), so the shared builder is handed a blanked mount_config
    (the kimi/codex pattern) to suppress the wrong-runtime injection, and this
    provider re-injects them under the Nebius env names.
  * **Runtime** - the ``roboco-agent-nebius`` image (opencode CLI) instead
    of ``claude``.

The initial prompt travels as an **env var, not a positional CLI arg** (the
entrypoint folds it into a single quoted ``opencode run`` argv token), which
structurally avoids a flag-injection vector (the PR #170 gap #2 fix).

**V1 scope**: one-shot delivery roles only (developer / qa / documenter /
cell_pm / main_pm / pr_reviewer / board). No interactive intake/secretary
support - there is no ``roboco-agent-nebius-prompter`` / ``-secretary``
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
from roboco.llm.providers.nebius_cli_config import opencode_model_ref
from roboco.runtime.compose_labels import compose_label_args

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig

_log = logging.getLogger(__name__)

# The Nebius agent image (own image, like every other agent role).
_DEFAULT_NEBIUS_IMAGE = "roboco-agent-nebius:latest"

# The opencode CLI default model - a floor only; the routing assignment pins
# the real model id per agent (the live catalog is searched on demand).
_NEBIUS_CLI_MODEL = settings.nebius_cli_model

# In-container paths.
_MCP_CONFIG_IN_CONTAINER = "/app/mcp-config.json"
# Per-agent data dir (the host side is reused from the shared assembly): the
# entrypoint writes the captured token usage here so the orchestrator reads it
# back at finalize, the nebius analogue of the mounted Claude transcript.
_NEBIUS_USAGE_DIR_IN_CONTAINER = "/home/agent/.opencode-usage"
_NEBIUS_USAGE_FILE_IN_CONTAINER = f"{_NEBIUS_USAGE_DIR_IN_CONTAINER}/usage.json"


def _container_name(agent_id: str) -> str:
    return f"roboco-agent-{agent_id}"


class _NebiusHost(Protocol):
    """The orchestrator surface NebiusProvider reuses for container assembly.

    Typed as a Protocol so this module never imports ``AgentOrchestrator`` (no
    import cycle) and is trivially mockable in tests.
    """

    async def _remove_container(
        self, container_name: str, *, stop_reason: str | None = None
    ) -> None: ...

    def _ensure_nebius_usage_dir(self, agent_id: str) -> None: ...

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


class NebiusProvider(AgentProvider):
    """Spawn a Nebius Token Factory (opencode CLI) agent as a gateway-wired container.

    The Ollama shape: a static metered API key + base URL injected via env,
    no ``~/.`` auth mount, no refresh loop - contrast
    :class:`~roboco.llm.providers.kimi.KimiCliProvider` /
    :class:`~roboco.llm.providers.codex.CodexCliProvider` which mount a
    subscription credential.
    """

    def __init__(self, host: _NebiusHost, image: str | None = None) -> None:
        self._host = host
        self._image = image or _DEFAULT_NEBIUS_IMAGE

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        if not config.mcp_config_path:
            raise ProviderError(
                "NEBIUS spawn requires an MCP config (gateway access).",
                agent_id=config.agent_id,
            )
        if not config.provider_auth_token:
            # The Nebius API key is the spawn credential - without it the
            # container starts but the entrypoint's auth preflight refuses to
            # run (exit 78) and the agent is doomed. Fail loud at spawn time so
            # the operator sees the missing key immediately.
            raise ProviderError(
                "NEBIUS spawn requires a Nebius Token Factory API key "
                "(provider_auth_token); set one via the routing service "
                "(PUT /api/providers/nebius-key).",
                agent_id=config.agent_id,
            )

        container_name = _container_name(config.agent_id)
        await self._host._remove_container(
            container_name, stop_reason="pre_spawn_stale_clear"
        )
        # Pre-create the per-agent data dir (world-writable) before the bind
        # mount so the non-root agent can write the usage file (else EACCES).
        self._host._ensure_nebius_usage_dir(config.agent_id)

        # Reuse the orchestrator's mount/auth/git assembly so the agent gets
        # the full MCP gateway + identity wiring. Blank the provider routing
        # fields first: otherwise the shared builder would inject the
        # Nebius endpoint as ANTHROPIC_BASE_URL/AUTH_TOKEN (the wrong
        # runtime) - Token Factory authenticates from NEBIUS_API_KEY, which
        # this provider re-injects below under the Nebius env names.
        mount_config = dataclasses.replace(
            config, provider_base_url=None, provider_auth_token=None
        )
        hosts = self._host._resolve_host_paths(config, agent_settings_path)
        cmd = self._host._build_mount_args(container_name, mount_config, hosts)
        self._host._append_agent_auth_env(cmd, config)
        self._host._append_git_context_env(cmd, config)
        # No auth mount - the Ollama shape (static key via env, no ~/. mount).
        self._append_usage_mount(cmd, hosts)
        self._append_nebius_env(cmd, config, initial_prompt)
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
                f"Failed to start Nebius container: {stderr.decode().strip()}",
                agent_id=config.agent_id,
            )
        return SpawnResult(
            instance_id=container_name,
            extra={
                "container_id": stdout.decode().strip(),
                "model": config.model or _NEBIUS_CLI_MODEL,
            },
        )

    @staticmethod
    def _append_usage_mount(cmd: list[str], hosts: dict[str, str | None]) -> None:
        """Mount the per-agent data dir so the orchestrator reads usage back.

        Reuses the shared per-agent host dir (``hosts["nebius_usage"]``);
        the entrypoint writes ``usage.json`` here after the run. Without it a
        Nebius agent finalizes at 0 tokens / $0.
        """
        data_host = hosts.get("nebius_usage")
        if data_host:
            cmd.extend(["-v", f"{data_host}:{_NEBIUS_USAGE_DIR_IN_CONTAINER}"])

    def _append_nebius_env(
        self, cmd: list[str], config: AgentConfig, initial_prompt: str | None
    ) -> None:
        """Append the runtime env the opencode entrypoint + renderer read.

        ``NEBIUS_API_KEY`` / ``NEBIUS_BASE_URL`` are the static metered
        credential (the Ollama shape - no refresh loop; Token Factory has no
        attribution-header surface, unlike OpenRouter's HTTP-Referer/X-Title).
        ``ROBOCO_AGENT_ID`` lets the renderer compute the per-role
        permission deny-rules; ``ROBOCO_MCP_CONFIG`` points it at the mounted
        gateway config; the prompt travels as an env var (never an argv
        positional). ``ROBOCO_AGENT_MODEL`` carries the opencode model REF
        (:func:`opencode_model_ref` - the bare catalog id prefixed with
        opencode's own ``nebius`` provider id), because the entrypoint passes
        it straight to ``--model``: unprefixed, opencode resolves it against
        its built-in anthropic provider and the run cannot authenticate.
        """
        base_url = config.provider_base_url or settings.nebius_base_url
        model = config.model or _NEBIUS_CLI_MODEL
        env: list[str] = [
            "-e",
            f"ROBOCO_AGENT_ID={config.agent_id}",
            "-e",
            f"ROBOCO_AGENT_MODEL={opencode_model_ref(model)}",
            "-e",
            f"ROBOCO_MCP_CONFIG={_MCP_CONFIG_IN_CONTAINER}",
            "-e",
            f"ROBOCO_INITIAL_PROMPT={initial_prompt or ''}",
            "-e",
            f"ROBOCO_NEBIUS_USAGE_FILE={_NEBIUS_USAGE_FILE_IN_CONTAINER}",
            "-e",
            f"NEBIUS_API_KEY={config.provider_auth_token or ''}",
            "-e",
            f"NEBIUS_BASE_URL={base_url}",
        ]
        cmd.extend(env)

    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        await stop_container(instance_id, graceful)

    async def health_check(self, instance_id: str) -> bool:
        return await container_running(instance_id)

    async def remove(self, instance_id: str) -> None:
        await self._host._remove_container(instance_id)
