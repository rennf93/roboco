"""Codex CLI provider — OpenAI via the official ``codex`` CLI.

OpenAI ships an official terminal coding agent (the ``codex`` CLI) authenticated
by a ChatGPT subscription. RoboCo runs Codex agents on it the same way it runs
Grok agents on ``grok``: the orchestrator's shared container assembly mounts
the RoboCo MCP gateway (``mcp-config.json``), the agent HMAC identity, and the
git context; this provider adds the subscription auth mount (``~/.codex``) and
the runtime env the codex-cli entrypoint reads, then launches the
``roboco-agent-codex`` image — whose entrypoint renders ``~/.codex/config.toml``
+ execpolicy rules + the per-role sandbox flag (see
:mod:`roboco.llm.providers.codex_cli_config`) and runs ``codex exec --json``
headless.

Two things differ from the Claude Code spawn (mirroring
:mod:`roboco.llm.providers.grok`):
  1. **Auth** — the host's ``~/.codex`` (subscription credential from ``codex
     login``) is mounted instead of relying on a provider key; no OpenAI API
     key is used. The provider routing fields are blanked before the shared
     mount step so the shared builder never injects them as ``ANTHROPIC_*``
     (the wrong runtime) — codex authenticates from the mounted ``~/.codex``.
  2. **Runtime** — the ``roboco-agent-codex`` image (codex CLI) instead of
     ``claude``.

The initial prompt is passed via an **env var, not a positional CLI arg**
(the entrypoint folds it into the rendered combined-prompt file), which
structurally avoids a flag-injection vector.

**V1 scope**: one-shot delivery roles only (developer / qa / documenter /
cell_pm / main_pm / pr_reviewer / board). No interactive intake/secretary
support — there is no ``roboco-agent-codex-prompter`` / ``-secretary`` image,
unlike grok's interactive pair.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from roboco.config import settings
from roboco.llm.providers._docker import container_running, stop_container
from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult

if TYPE_CHECKING:
    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig

_log = logging.getLogger(__name__)

# The Codex agent image (own image, like every other agent role).
_DEFAULT_CODEX_IMAGE = "roboco-agent-codex:latest"

# The codex CLI model id, pinned — codex has no reliable default model.
_CODEX_CLI_MODEL = settings.codex_cli_model

# Host directory holding the ChatGPT-subscription auth (from `codex login`).
# Mounted into the agent's ~/.codex like the grok path mounts ~/.grok.
CODEX_AUTH_HOST_PATH = settings.host_codex_dir

# In-container paths.
_MCP_CONFIG_IN_CONTAINER = "/app/mcp-config.json"
# The host ~/.codex DIRECTORY (not a single auth.json file) is mounted RO
# here: a single-file bind mount pins the inode, so the orchestrator's atomic
# tmp+rename refresh (codex_auth.refresh_if_stale) never reaches a running
# container — the exact concern grok's auth mount documents. The entrypoint
# symlinks ~/.codex/auth.json -> this RO mount; codex's writable state
# (config.toml, rules/, sessions/) lives in the image's own ~/.codex.
_CODEX_AUTH_DIR_IN_CONTAINER = "/home/agent/.codex-auth-ro"
# Per-agent data dir (the host side is reused from the shared assembly): the
# entrypoint writes the captured token usage here so the orchestrator reads it
# back at finalize, the codex analogue of the mounted Claude transcript.
_CODEX_USAGE_DIR_IN_CONTAINER = "/home/agent/.codex-usage"
_CODEX_USAGE_FILE_IN_CONTAINER = f"{_CODEX_USAGE_DIR_IN_CONTAINER}/usage.json"


def _container_name(agent_id: str) -> str:
    return f"roboco-agent-{agent_id}"


class _CodexHost(Protocol):
    """The orchestrator surface CodexCliProvider reuses for container assembly.

    Typed as a Protocol so this module never imports ``AgentOrchestrator`` (no
    import cycle) and is trivially mockable in tests.
    """

    async def _remove_container(
        self, container_name: str, *, stop_reason: str | None = None
    ) -> None: ...

    def _ensure_codex_usage_dir(self, agent_id: str) -> None: ...

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


class CodexCliProvider(AgentProvider):
    """Spawn a Codex (OpenAI, official CLI) agent as a gateway-wired container."""

    def __init__(self, host: _CodexHost, image: str | None = None) -> None:
        self._host = host
        self._image = image or _DEFAULT_CODEX_IMAGE

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        if not config.mcp_config_path:
            raise ProviderError(
                "OPENAI spawn requires an MCP config (gateway access).",
                agent_id=config.agent_id,
            )

        container_name = _container_name(config.agent_id)
        await self._host._remove_container(
            container_name, stop_reason="pre_spawn_stale_clear"
        )
        # Pre-create the per-agent data dir (world-writable) before the bind
        # mount so the non-root agent can write the usage file (else EACCES).
        self._host._ensure_codex_usage_dir(config.agent_id)

        # Reuse the orchestrator's mount/auth/git assembly so the agent gets
        # the full MCP gateway + identity wiring. Blank the provider routing
        # fields first: otherwise the shared builder would inject the
        # provider endpoint as ANTHROPIC_BASE_URL/AUTH_TOKEN — codex
        # authenticates from the mounted ~/.codex, not a provider key.
        mount_config = dataclasses.replace(
            config, provider_base_url=None, provider_auth_token=None
        )
        hosts = self._host._resolve_host_paths(config, agent_settings_path)
        cmd = self._host._build_mount_args(container_name, mount_config, hosts)
        self._host._append_agent_auth_env(cmd, config)
        self._host._append_git_context_env(cmd, config)
        self._append_codex_auth_mount(cmd)
        self._append_usage_mount(cmd, hosts)
        self._append_codex_env(cmd, config, initial_prompt)
        cmd.append(self._image)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ProviderError(
                f"Failed to start Codex container: {stderr.decode().strip()}",
                agent_id=config.agent_id,
            )
        return SpawnResult(
            instance_id=container_name,
            extra={"container_id": stdout.decode().strip(), "model": _CODEX_CLI_MODEL},
        )

    @staticmethod
    def _append_codex_auth_mount(cmd: list[str]) -> None:
        """Mount the host's ChatGPT-subscription ``~/.codex`` directory (read-only).

        The mount is the DIRECTORY, not the single ``auth.json`` file — a
        single-file bind mount pins the inode, so the orchestrator's atomic
        refresh (rename within the host ``~/.codex``) would never reach a
        running container. See ``_CODEX_AUTH_DIR_IN_CONTAINER``.
        """
        auth_dir = Path(CODEX_AUTH_HOST_PATH)
        if (auth_dir / "auth.json").exists():
            cmd.extend(["-v", f"{auth_dir}:{_CODEX_AUTH_DIR_IN_CONTAINER}:ro"])
        else:
            # The mount is the codex subscription credential — without it the
            # container starts but the entrypoint `--check` backstop refuses
            # to run (exit 78) and the agent is doomed. Fail loud at spawn
            # time so the operator sees the missing credential immediately.
            _log.warning(
                "codex host auth.json not found at %s — spawn will start the "
                "container but it is doomed to exit 78 (no Codex credential). "
                "Run `codex login` on the host (or set ROBOCO_HOST_CODEX_DIR to "
                "the directory holding auth.json) before spawning Codex agents.",
                auth_dir / "auth.json",
            )

    @staticmethod
    def _append_usage_mount(cmd: list[str], hosts: dict[str, str | None]) -> None:
        """Mount the per-agent data dir so the orchestrator reads usage back.

        Reuses the shared per-agent host dir (``hosts["codex_usage"]``); the
        entrypoint writes ``usage.json`` here after the run. Without it a
        Codex agent finalizes at 0 tokens / $0.
        """
        data_host = hosts.get("codex_usage")
        if data_host:
            cmd.extend(["-v", f"{data_host}:{_CODEX_USAGE_DIR_IN_CONTAINER}"])

    def _append_codex_env(
        self, cmd: list[str], config: AgentConfig, initial_prompt: str | None
    ) -> None:
        """Append the runtime env the codex-cli entrypoint + renderer read.

        ``ROBOCO_AGENT_ID`` lets the renderer compute the per-role sandbox
        flag; ``ROBOCO_MCP_CONFIG`` points it at the mounted gateway config;
        the prompt travels as an env var (never an argv positional) and the
        renderer folds it into the combined system+task prompt file.
        """
        cmd.extend(
            [
                "-e",
                f"ROBOCO_AGENT_ID={config.agent_id}",
                "-e",
                f"ROBOCO_AGENT_MODEL={_CODEX_CLI_MODEL}",
                "-e",
                f"ROBOCO_MCP_CONFIG={_MCP_CONFIG_IN_CONTAINER}",
                "-e",
                f"ROBOCO_INITIAL_PROMPT={initial_prompt or ''}",
                "-e",
                f"ROBOCO_CODEX_USAGE_FILE={_CODEX_USAGE_FILE_IN_CONTAINER}",
            ]
        )

    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        await stop_container(instance_id, graceful)

    async def health_check(self, instance_id: str) -> bool:
        return await container_running(instance_id)

    async def remove(self, instance_id: str) -> None:
        await self._host._remove_container(instance_id)
