"""Gemini CLI provider — Google Gemini via the official ``gemini`` CLI.

Google ships an official terminal coding agent (the ``gemini`` CLI)
authenticated by an OAuth login (``ROBOCO_HOST_GEMINI_DIR``, subscription-style
daily quota caps — the OAuth-login analogue of grok's SuperGrok subscription).
RoboCo runs Gemini agents on it the same way it runs grok agents: the
orchestrator's shared container assembly mounts the RoboCo MCP gateway
(``mcp-config.json``), the agent HMAC identity, and the git context; this
provider adds the OAuth credential mount and the runtime env the gemini-cli
entrypoint reads, then launches the ``roboco-agent-gemini`` image — whose
entrypoint renders ``~/.gemini/settings.json`` + a Policy Engine TOML from the
mounted mcp-config.json (see :mod:`roboco.llm.providers.gemini_cli_config`) and
runs ``gemini -p`` headless.

Two things differ from the Claude Code spawn (mirrors ``GrokCliProvider``):
  1. **Auth** — the host's ``~/.gemini`` (OAuth credential from a one-time
     interactive ``gemini`` login) is mounted READ-ONLY at a staging path; the
     entrypoint COPIES it into a container-local, WRITABLE ``~/.gemini`` before
     running the CLI. This is where Gemini genuinely diverges from grok's
     symlink-to-a-live-RO-mount design (see :mod:`roboco.llm.providers.grok_auth`
     for the contrast):

     xAI's grok refresh token is SINGLE-USE — a rotated refresh token
     invalidates the prior one instantly, so a shared, live, host-writable
     credential needs one orchestrator-side writer serializing every refresh
     (that whole module exists to make that safe). Google's OAuth refresh
     token is REUSABLE — minting a new access token does not invalidate it —
     so there is no shared-writer race to serialize in the first place. Each
     container refreshing its OWN local copy in-process (the ``gemini`` CLI's
     bundled google-auth-library does this automatically) is therefore safe
     with NO orchestrator daemon: no rotation to lose, no concurrent-refresh
     race, and the host's read-only copy is never mutated (so it can never be
     corrupted by a container's write-back, and a per-container copy means one
     agent's refreshed token never propagates to (or conflicts with) a
     sibling's). This is why ``gemini_auth.py`` — the grok module this docstring
     contrasts against — has no counterpart here.
  2. **Runtime** — the ``roboco-agent-gemini`` image (Gemini CLI) instead of
     ``claude``.

The initial prompt is passed via an **env var, not a positional CLI arg**,
which structurally avoids a flag-injection vector (parity with grok).

V1 scope: one-shot delivery roles ONLY — no interactive Intake/Secretary driver
(contrast ``GrokCliProvider``, which also serves those via a resumable grok
session). A Gemini agent runs a single ``gemini -p`` invocation per task, the
same shape as a one-shot grok agent.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from roboco.llm.providers._docker import container_running, stop_container
from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult

if TYPE_CHECKING:
    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig

_log = logging.getLogger(__name__)

# The Gemini agent image (own image, like every other agent role). Overridable
# for tests / staged rollout.
_DEFAULT_GEMINI_IMAGE = os.environ.get(
    "ROBOCO_GEMINI_AGENT_IMAGE", "roboco-agent-gemini:latest"
)

# The gemini CLI model id. GA ids: gemini-2.5-pro / gemini-2.5-flash /
# gemini-2.5-flash-lite (spike-verified).
_GEMINI_CLI_MODEL = os.environ.get("ROBOCO_GEMINI_CLI_MODEL", "gemini-2.5-pro")

# Host directory holding the OAuth credential (``oauth_creds.json``, from a
# one-time interactive ``gemini`` login). Mounted into the agent's staging path
# like the grok path mounts ``~/.grok``. Override for docker-in-docker / NAS
# deploys (the orchestrator's home is not the host's).
GEMINI_AUTH_HOST_PATH = os.environ.get(
    "ROBOCO_HOST_GEMINI_DIR", str(Path.home() / ".gemini")
)

# In-container paths.
_MCP_CONFIG_IN_CONTAINER = "/app/mcp-config.json"
# The host ~/.gemini DIRECTORY (not the single oauth_creds.json file) is
# mounted RO here — a directory mount (not a single-file bind) so the
# entrypoint's copy step sees a consistent tree even mid-host-write; the
# entrypoint COPIES this into a container-local, writable ~/.gemini (see the
# module docstring for why a copy, not grok's live-symlink, is the right and
# SAFE choice for Gemini's reusable refresh token).
_GEMINI_AUTH_STAGING_DIR_IN_CONTAINER = "/home/agent/.gemini-auth-ro"
# Per-agent data dir (the host side is reused from the shared assembly): the
# entrypoint writes the captured token usage here so the orchestrator reads it
# back at finalize, the gemini analogue of the mounted Claude transcript.
_GEMINI_USAGE_DIR_IN_CONTAINER = "/home/agent/.gemini-usage"
_GEMINI_USAGE_FILE_IN_CONTAINER = f"{_GEMINI_USAGE_DIR_IN_CONTAINER}/usage.json"


def _container_name(agent_id: str) -> str:
    return f"roboco-agent-{agent_id}"


class _GeminiHost(Protocol):
    """The orchestrator surface GeminiCliProvider reuses for container assembly.

    Typed as a Protocol so this module never imports ``AgentOrchestrator`` (no
    import cycle) and is trivially mockable in tests.
    """

    async def _remove_container(
        self, container_name: str, *, stop_reason: str | None = None
    ) -> None: ...

    def _ensure_gemini_usage_dir(self, agent_id: str) -> None: ...

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


class GeminiCliProvider(AgentProvider):
    """Spawn a Gemini (Google, official CLI) agent as a gateway-wired container."""

    def __init__(self, host: _GeminiHost, image: str | None = None) -> None:
        self._host = host
        self._image = image or _DEFAULT_GEMINI_IMAGE

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        if not config.mcp_config_path:
            raise ProviderError(
                "GEMINI spawn requires an MCP config (gateway access).",
                agent_id=config.agent_id,
            )

        container_name = _container_name(config.agent_id)
        await self._host._remove_container(
            container_name, stop_reason="pre_spawn_stale_clear"
        )
        # Pre-create the per-agent data dir (world-writable) before the bind
        # mount so the non-root agent can write the usage file (else EACCES).
        self._host._ensure_gemini_usage_dir(config.agent_id)

        # Reuse the orchestrator's mount/auth/git assembly so the agent gets the
        # full MCP gateway + identity wiring. Blank the provider routing fields
        # first: otherwise the shared builder would inject the provider endpoint
        # as ANTHROPIC_BASE_URL/AUTH_TOKEN — gemini authenticates from the
        # mounted ~/.gemini OAuth credential, not a provider key.
        mount_config = dataclasses.replace(
            config, provider_base_url=None, provider_auth_token=None
        )
        hosts = self._host._resolve_host_paths(config, agent_settings_path)
        cmd = self._host._build_mount_args(container_name, mount_config, hosts)
        self._host._append_agent_auth_env(cmd, config)
        self._host._append_git_context_env(cmd, config)
        self._append_gemini_auth_mount(cmd)
        self._append_usage_mount(cmd, hosts)
        self._append_gemini_env(cmd, config, initial_prompt)
        cmd.append(self._image)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ProviderError(
                f"Failed to start Gemini container: {stderr.decode().strip()}",
                agent_id=config.agent_id,
            )
        return SpawnResult(
            instance_id=container_name,
            extra={"container_id": stdout.decode().strip(), "model": _GEMINI_CLI_MODEL},
        )

    @staticmethod
    def _append_gemini_auth_mount(cmd: list[str]) -> None:
        """Mount the host's OAuth credential directory (read-only).

        See the module docstring: unlike grok's live-symlinked RO mount, the
        entrypoint COPIES this staged mount into a container-local, writable
        ``~/.gemini`` — safe here because Google's refresh token is reusable
        (no single-use rotation to lose, no shared-writer race to serialize).
        """
        auth_dir = Path(GEMINI_AUTH_HOST_PATH)
        if (auth_dir / "oauth_creds.json").exists():
            cmd.extend(["-v", f"{auth_dir}:{_GEMINI_AUTH_STAGING_DIR_IN_CONTAINER}:ro"])
        else:
            # The mount is the Gemini OAuth credential — without it the
            # container starts but the entrypoint's preflight refuses to run
            # (exit 41, no credential). Fail loud at spawn time so the operator
            # sees the missing credential immediately instead of diagnosing a
            # later exit-41 from the container log markers.
            _log.warning(
                "gemini host oauth_creds.json not found at %s — spawn will start "
                "the container but it is doomed to exit 41 (no OAuth credential). "
                "Run `gemini` interactively once on the host (or set "
                "ROBOCO_HOST_GEMINI_DIR to the directory holding oauth_creds.json) "
                "before spawning Gemini agents.",
                auth_dir / "oauth_creds.json",
            )

    @staticmethod
    def _append_usage_mount(cmd: list[str], hosts: dict[str, str | None]) -> None:
        """Mount the per-agent data dir so the orchestrator reads usage back.

        Reuses the shared per-agent host dir (``hosts["gemini_usage"]``); the
        entrypoint writes ``usage.json`` here after the run and the
        orchestrator reads it back at finalize. Without it a Gemini agent
        finalizes at 0 tokens / $0.
        """
        data_host = hosts.get("gemini_usage")
        if data_host:
            cmd.extend(["-v", f"{data_host}:{_GEMINI_USAGE_DIR_IN_CONTAINER}"])

    def _append_gemini_env(
        self, cmd: list[str], config: AgentConfig, initial_prompt: str | None
    ) -> None:
        """Append the runtime env the gemini-cli entrypoint + renderer read.

        ``ROBOCO_AGENT_ID`` lets the renderer compute the per-role
        settings/policy; ``ROBOCO_MCP_CONFIG`` points it at the mounted gateway
        config; the prompt travels as an env var (never an argv positional).
        """
        cmd.extend(
            [
                "-e",
                f"ROBOCO_AGENT_ID={config.agent_id}",
                "-e",
                f"ROBOCO_AGENT_MODEL={_GEMINI_CLI_MODEL}",
                "-e",
                f"ROBOCO_MCP_CONFIG={_MCP_CONFIG_IN_CONTAINER}",
                "-e",
                f"ROBOCO_INITIAL_PROMPT={initial_prompt or ''}",
                "-e",
                f"ROBOCO_GEMINI_USAGE_FILE={_GEMINI_USAGE_FILE_IN_CONTAINER}",
            ]
        )

    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        await stop_container(instance_id, graceful)

    async def health_check(self, instance_id: str) -> bool:
        return await container_running(instance_id)

    async def remove(self, instance_id: str) -> None:
        await self._host._remove_container(instance_id)
