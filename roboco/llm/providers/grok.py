"""Grok CLI provider — xAI Grok Build via the official ``grok`` CLI.

xAI ships an official terminal coding agent (the ``grok`` CLI, "Grok Build")
authenticated by the SuperGrok subscription. RoboCo runs Grok agents on it the
same way it runs Claude agents on ``claude``: the orchestrator's shared container
assembly mounts the RoboCo MCP gateway (``mcp-config.json``), the agent HMAC
identity, and the git context; this provider adds the subscription auth mount
(``~/.grok``) and the runtime env the grok-cli entrypoint reads, then launches
the ``roboco-agent-grok`` image — whose entrypoint renders ``~/.grok/config.toml``
+ per-role flags (see :mod:`roboco.llm.providers.grok_cli_config`) and runs
``grok -p`` headless.

Two things differ from the Claude Code spawn:
  1. **Auth** — the host's ``~/.grok`` (subscription credential from ``grok
     login``) is mounted instead of relying on a provider key; the xAI API key is
     never used. The provider routing fields are blanked before the shared mount
     step so the shared builder never injects them as ``ANTHROPIC_*`` (the wrong
     runtime) — grok authenticates from the mounted ``~/.grok``.
  2. **Runtime** — the ``roboco-agent-grok`` image (grok CLI) instead of
     ``claude``.

The initial prompt is passed via an **env var, not a positional CLI arg**, which
structurally avoids a flag-injection vector.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from roboco.llm.providers._docker import container_running, stop_container
from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult

if TYPE_CHECKING:
    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig

# The Grok agent image (own image, like every other agent role). Overridable for
# tests / staged rollout.
_DEFAULT_GROK_IMAGE = os.environ.get(
    "ROBOCO_GROK_AGENT_IMAGE", "roboco-agent-grok:latest"
)

# The grok CLI model id (the CLI uses ``grok-build``, verified live).
_GROK_CLI_MODEL = os.environ.get("ROBOCO_GROK_CLI_MODEL", "grok-build")

# Host directory holding the SuperGrok auth (from ``grok login``). Mounted into
# the agent's ``~/.grok`` like the Claude path mounts ``~/.claude``. Override for
# docker-in-docker / NAS deploys (the orchestrator's home is not the host's).
GROK_AUTH_HOST_PATH = os.environ.get(
    "ROBOCO_HOST_GROK_DIR", str(Path.home() / ".grok")
)

# In-container paths.
_MCP_CONFIG_IN_CONTAINER = "/app/mcp-config.json"
_GROK_AUTH_IN_CONTAINER = "/home/agent/.grok/auth.json"


def _container_name(agent_id: str) -> str:
    return f"roboco-agent-{agent_id}"


class _GrokHost(Protocol):
    """The orchestrator surface GrokCliProvider reuses for container assembly.

    Typed as a Protocol so this module never imports ``AgentOrchestrator`` (no
    import cycle) and is trivially mockable in tests.
    """

    async def _remove_container(self, container_name: str) -> None: ...

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


class GrokCliProvider(AgentProvider):
    """Spawn a Grok (xAI, official CLI) agent as a gateway-wired container."""

    def __init__(self, host: _GrokHost, image: str | None = None) -> None:
        self._host = host
        self._image = image or _DEFAULT_GROK_IMAGE

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        if not config.mcp_config_path:
            raise ProviderError(
                "GROK spawn requires an MCP config (gateway access).",
                agent_id=config.agent_id,
            )

        container_name = _container_name(config.agent_id)
        await self._host._remove_container(container_name)

        # Reuse the orchestrator's mount/auth/git assembly so the agent gets the
        # full MCP gateway + identity wiring. Blank the provider routing fields
        # first: otherwise the shared builder would inject the provider endpoint
        # as ANTHROPIC_BASE_URL/AUTH_TOKEN — grok authenticates from the mounted
        # ~/.grok, not a provider key.
        mount_config = dataclasses.replace(
            config, provider_base_url=None, provider_auth_token=None
        )
        hosts = self._host._resolve_host_paths(config, agent_settings_path)
        cmd = self._host._build_mount_args(container_name, mount_config, hosts)
        self._host._append_agent_auth_env(cmd, config)
        self._host._append_git_context_env(cmd, config)
        self._append_grok_auth_mount(cmd)
        self._append_grok_env(cmd, config, initial_prompt)
        cmd.append(self._image)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ProviderError(
                f"Failed to start Grok container: {stderr.decode().strip()}",
                agent_id=config.agent_id,
            )
        return SpawnResult(
            instance_id=container_name,
            extra={"container_id": stdout.decode().strip(), "model": _GROK_CLI_MODEL},
        )

    @staticmethod
    def _append_grok_auth_mount(cmd: list[str]) -> None:
        """Mount the host's SuperGrok ``auth.json`` (read-only) into ~/.grok.

        Read-only so concurrent containers can't corrupt the shared subscription
        credential; grok writes its per-run state (the rendered ``config.toml``,
        ``sessions/``) into the image's own ``~/.grok``. One-shot delivery runs
        are short, so the token needs no mid-run refresh.
        """
        auth_json = Path(GROK_AUTH_HOST_PATH) / "auth.json"
        if auth_json.exists():
            cmd.extend(["-v", f"{auth_json}:{_GROK_AUTH_IN_CONTAINER}:ro"])

    def _append_grok_env(
        self, cmd: list[str], config: AgentConfig, initial_prompt: str | None
    ) -> None:
        """Append the runtime env the grok-cli entrypoint + renderer read.

        ``ROBOCO_AGENT_ID`` lets the renderer compute the per-role flags;
        ``ROBOCO_MCP_CONFIG`` points it at the mounted gateway config; the prompt
        travels as an env var (never an argv positional).
        """
        cmd.extend(
            [
                "-e",
                f"ROBOCO_AGENT_ID={config.agent_id}",
                "-e",
                f"ROBOCO_AGENT_MODEL={_GROK_CLI_MODEL}",
                "-e",
                f"ROBOCO_MCP_CONFIG={_MCP_CONFIG_IN_CONTAINER}",
                "-e",
                f"ROBOCO_INITIAL_PROMPT={initial_prompt or ''}",
            ]
        )
        if config.claude_session_id:
            # A fixed session id (reused as the generic agent session id, as on
            # the Claude path) lets the entrypoint pin `grok -p -s <id>` so the
            # run's session store is locatable for token-usage capture.
            cmd.extend(["-e", f"ROBOCO_AGENT_SESSION_ID={config.claude_session_id}"])

    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        await stop_container(instance_id, graceful)

    async def health_check(self, instance_id: str) -> bool:
        return await container_running(instance_id)

    async def remove(self, instance_id: str) -> None:
        await self._host._remove_container(instance_id)
