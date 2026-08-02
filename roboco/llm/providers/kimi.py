"""Kimi CLI provider — Moonshot AI's Kimi K3 via the official ``kimi`` CLI.

Moonshot ships an official terminal coding agent (the ``kimi`` / kimi-code CLI)
authenticated by a Kimi subscription (OAuth device-code login), not a metered
key — the same posture as Grok (SuperGrok), Codex (ChatGPT), and Gemini
(Google OAuth). RoboCo runs Kimi agents on it the same way it runs those:
the orchestrator's shared container assembly mounts the RoboCo MCP gateway
(``mcp-config.json``), the agent HMAC identity, and the git context; this
provider adds the subscription auth mount (``~/.kimi-code``) and the runtime
env the kimi-cli entrypoint reads, then launches the ``roboco-agent-kimi``
image — whose entrypoint copies the mounted credential in, renders
``~/.kimi-code/config.toml`` + ``mcp.json`` + ``AGENTS.md`` from the mounted
mcp-config.json (see :mod:`roboco.llm.providers.kimi_cli_config`), and runs
``kimi -p`` headless.

Two things differ from the Claude Code spawn (mirrors ``CodexCliProvider``):
  1. **Auth** — the host's ``~/.kimi-code`` (subscription credential from
     ``kimi login``) is mounted READ-WRITE, shared across every container AND
     the host. Moonshot's refresh token is rotation-with-short-reuse-grace,
     NOT truly reusable: a first probe (two isolated copies of one credential
     snapshot both redeeming the same refresh token ~90s apart) looked
     reusable, but redeeming that SAME token again from the ORIGINAL home
     ~40min later was refused as reuse-after-grace and the CLI wiped the
     stored credentials outright (empty-string tokens — a real login died and
     needed a fresh device-code approval to recover). Per-container COPIES of
     the credential are therefore unsafe: N containers each refreshing a
     private snapshot will eventually cross-invalidate each other's tokens.
     The corrected design is ONE shared rotating chain: the entrypoint keeps
     a container-local writable ``~/.kimi-code`` for
     config.toml/mcp.json/AGENTS.md (rendered fresh — see
     :mod:`roboco.llm.providers.kimi_cli_config`) but SYMLINKS
     ``credentials/`` AND ``oauth/`` (the lock directory) into the shared RW
     mount, so every container plus the host redeem the same chain and the
     CLI's own cross-process lock (``oauth/kimi-code.lock``) serializes
     refreshes — the exact mechanism the CLI ships for multi-process sharing.
     Still NO orchestrator refresh daemon (the CLI refreshes itself; the
     orchestrator does nothing) — contrast
     :mod:`roboco.llm.providers.grok_auth` / :mod:`roboco.llm.providers.codex_auth`,
     which exist ONLY because their provider's refresh token has no
     multi-process sharing story at all. The provider routing fields are
     blanked before the shared mount step so the shared builder never
     injects them as ``ANTHROPIC_*`` (the wrong runtime) — kimi authenticates
     from the shared credential, not a provider key.
  2. **Runtime** — the ``roboco-agent-kimi`` image (kimi-code CLI) instead of
     ``claude``.

The initial prompt is passed via an **env var, not a positional CLI arg**
(the entrypoint folds it into the rendered prompt handoff), which
structurally avoids a flag-injection vector.

**V1 scope**: one-shot delivery roles only (developer / qa / documenter /
cell_pm / main_pm / pr_reviewer / board). No interactive intake/secretary
support — there is no ``roboco-agent-kimi-prompter`` / ``-secretary`` image.
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
from roboco.runtime.compose_labels import compose_label_args

if TYPE_CHECKING:
    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig

_log = logging.getLogger(__name__)

# The Kimi agent image (own image, like every other agent role).
_DEFAULT_KIMI_IMAGE = "roboco-agent-kimi:latest"

# The kimi CLI model alias, pinned via Settings (parity with codex_cli_model /
# gemini_cli_model — Kimi's login-managed aliases have no reliable "pick for
# me" default worth trusting blind).
_KIMI_CLI_MODEL = settings.kimi_cli_model

# Host directory holding the Kimi subscription auth (from `kimi login`).
# Mounted into the agent's staging path like the codex/gemini paths mount
# their own host auth dirs.
KIMI_AUTH_HOST_PATH = settings.host_kimi_dir

# In-container paths.
_MCP_CONFIG_IN_CONTAINER = "/app/mcp-config.json"
# The host ~/.kimi-code DIRECTORY is mounted RW here, shared by every
# container AND the host; the entrypoint symlinks its credentials/ and
# oauth/ subdirectories into a container-local, writable ~/.kimi-code
# (config.toml/mcp.json/AGENTS.md are rendered fresh — see the module
# docstring and kimi_cli_config for why a shared RW mount + symlinks, not
# codex's live-symlinked single-file mount or a per-container copy).
_KIMI_AUTH_DIR_IN_CONTAINER = "/home/agent/.kimi-code-auth"
# Per-agent data dir (the host side is reused from the shared assembly): the
# entrypoint writes the captured token usage here so the orchestrator reads it
# back at finalize, the kimi analogue of the mounted Claude transcript.
_KIMI_USAGE_DIR_IN_CONTAINER = "/home/agent/.kimi-usage"
_KIMI_USAGE_FILE_IN_CONTAINER = f"{_KIMI_USAGE_DIR_IN_CONTAINER}/usage.json"


def _container_name(agent_id: str) -> str:
    return f"roboco-agent-{agent_id}"


class _KimiHost(Protocol):
    """The orchestrator surface KimiCliProvider reuses for container assembly.

    Typed as a Protocol so this module never imports ``AgentOrchestrator`` (no
    import cycle) and is trivially mockable in tests.
    """

    async def _remove_container(
        self, container_name: str, *, stop_reason: str | None = None
    ) -> None: ...

    def _ensure_kimi_usage_dir(self, agent_id: str) -> None: ...

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


class KimiCliProvider(AgentProvider):
    """Spawn a Kimi (Moonshot, official CLI) agent as a gateway-wired container."""

    def __init__(self, host: _KimiHost, image: str | None = None) -> None:
        self._host = host
        self._image = image or _DEFAULT_KIMI_IMAGE

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        if not config.mcp_config_path:
            raise ProviderError(
                "KIMI spawn requires an MCP config (gateway access).",
                agent_id=config.agent_id,
            )

        container_name = _container_name(config.agent_id)
        await self._host._remove_container(
            container_name, stop_reason="pre_spawn_stale_clear"
        )
        # Pre-create the per-agent data dir (world-writable) before the bind
        # mount so the non-root agent can write the usage file (else EACCES).
        self._host._ensure_kimi_usage_dir(config.agent_id)

        # Reuse the orchestrator's mount/auth/git assembly so the agent gets
        # the full MCP gateway + identity wiring. Blank the provider routing
        # fields first: otherwise the shared builder would inject the
        # provider endpoint as ANTHROPIC_BASE_URL/AUTH_TOKEN — kimi
        # authenticates from the shared, symlinked-in ~/.kimi-code
        # credential, not a provider key.
        mount_config = dataclasses.replace(
            config, provider_base_url=None, provider_auth_token=None
        )
        hosts = self._host._resolve_host_paths(config, agent_settings_path)
        cmd = self._host._build_mount_args(container_name, mount_config, hosts)
        self._host._append_agent_auth_env(cmd, config)
        self._host._append_git_context_env(cmd, config)
        self._append_kimi_auth_mount(cmd)
        self._append_usage_mount(cmd, hosts)
        self._append_kimi_env(cmd, config, initial_prompt)
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
                f"Failed to start Kimi container: {stderr.decode().strip()}",
                agent_id=config.agent_id,
            )
        return SpawnResult(
            instance_id=container_name,
            extra={"container_id": stdout.decode().strip(), "model": _KIMI_CLI_MODEL},
        )

    @staticmethod
    def _append_kimi_auth_mount(cmd: list[str]) -> None:
        """Mount the host's Kimi subscription ``~/.kimi-code`` directory (read-write).

        RW, not RO: the refresh token is rotation-with-short-reuse-grace, so
        every container must redeem the SAME chain the host uses (see the
        module docstring) — the entrypoint symlinks ``credentials/`` and
        ``oauth/`` forward from this mount into a container-local writable
        ``~/.kimi-code`` — see ``_KIMI_AUTH_DIR_IN_CONTAINER``.
        """
        auth_dir = Path(KIMI_AUTH_HOST_PATH)
        if (auth_dir / "credentials" / "kimi-code.json").exists():
            cmd.extend(["-v", f"{auth_dir}:{_KIMI_AUTH_DIR_IN_CONTAINER}"])
        else:
            # The mount is the Kimi subscription credential — without it the
            # container starts but the entrypoint's auth preflight refuses to
            # run (exit 78) and the agent is doomed. Fail loud at spawn time
            # so the operator sees the missing credential immediately.
            _log.warning(
                "kimi host credentials/kimi-code.json not found at %s — spawn "
                "will start the container but it is doomed to exit 78 (no "
                "Kimi credential). Run `kimi login` on the host (or set "
                "ROBOCO_HOST_KIMI_DIR to the directory holding credentials/"
                "kimi-code.json) before spawning Kimi agents.",
                auth_dir / "credentials" / "kimi-code.json",
            )

    @staticmethod
    def _append_usage_mount(cmd: list[str], hosts: dict[str, str | None]) -> None:
        """Mount the per-agent data dir so the orchestrator reads usage back.

        Reuses the shared per-agent host dir (``hosts["kimi_usage"]``); the
        entrypoint writes ``usage.json`` here after the run. Without it a
        Kimi agent finalizes at 0 tokens / $0.
        """
        data_host = hosts.get("kimi_usage")
        if data_host:
            cmd.extend(["-v", f"{data_host}:{_KIMI_USAGE_DIR_IN_CONTAINER}"])

    def _append_kimi_env(
        self, cmd: list[str], config: AgentConfig, initial_prompt: str | None
    ) -> None:
        """Append the runtime env the kimi-cli entrypoint + renderer read.

        ``ROBOCO_AGENT_ID`` lets the renderer compute the per-role deny
        rules; ``ROBOCO_MCP_CONFIG`` points it at the mounted gateway config;
        the prompt travels as an env var (never an argv positional).
        """
        cmd.extend(
            [
                "-e",
                f"ROBOCO_AGENT_ID={config.agent_id}",
                "-e",
                f"ROBOCO_AGENT_MODEL={_KIMI_CLI_MODEL}",
                "-e",
                f"ROBOCO_MCP_CONFIG={_MCP_CONFIG_IN_CONTAINER}",
                "-e",
                f"ROBOCO_INITIAL_PROMPT={initial_prompt or ''}",
                "-e",
                f"ROBOCO_KIMI_USAGE_FILE={_KIMI_USAGE_FILE_IN_CONTAINER}",
            ]
        )

    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        await stop_container(instance_id, graceful)

    async def health_check(self, instance_id: str) -> bool:
        return await container_running(instance_id)

    async def remove(self, instance_id: str) -> None:
        await self._host._remove_container(instance_id)
