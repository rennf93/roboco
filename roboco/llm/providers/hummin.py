"""Hummin CLI provider — the GLM-native `hummin` CLI (pi-harness fork) headless.

Renzo's `hummin` CLI speaks GLM natively (the zai provider id maps to the GLM
Coding Plan endpoint https://api.z.ai/api/coding/paas/v4 inside hummin), so
HUMMIN is the go-to for the GLM catalog entries (glm-5.3 / glm-5.3-flash /
glm-5.3-highspeed). RoboCo runs hummin agents the same way it runs Kimi
agents: the orchestrator's shared container assembly mounts the agent
identity and the git context; this provider adds the runtime env the
hummin-cli entrypoint reads (ZAI_API_KEY + the rendered tool allowlist), then
launches the ``roboco-agent-hummin`` image — whose entrypoint renders the
hummin runtime config (see :mod:`roboco.llm.providers.hummin_cli_config`),
runs the auth preflight, and runs ``hummin --mode json`` headless.

Auth is key-based (the OpenRouter shape, NOT the kimi/grok/codex subscription
mount): the operator's Z.ai key for the GLM Coding Plan is stored
Fernet-encrypted on the provider row (PUT /providers/hummin-key), the routing
service decrypts it into ``provider_auth_token``, and this provider injects
it as ``ZAI_API_KEY`` at spawn. No ``~/.hummin`` credential mount exists and
no refresh loop is needed (an API key never rotates server-side).

Two structural departures from every other CLI provider (both live-verified
against the hummin source — see the vault gotcha ``hummin-mcp-and-rules-gaps``):

  1. **No MCP.** hummin has no MCP client at all (its README: "No MCP. ...
     build an extension that adds MCP support"), and V1 requires
     ``--no-extensions`` (the bundled colibri extension probes localhost
     ports at startup). So the RoboCo gateway (``roboco-flow`` /
     ``roboco-do``) is UNREACHABLE from a hummin agent in V1: the mounted
     ``mcp-config.json`` rides along inert (the env var is still passed for
     provenance and the future explicit ``-e`` bridge extension). A hummin
     delivery agent works prompt + built-in tools (read/bash/edit/write) +
     git only; task completion rides the orchestrator's still-owned-task
     path on container exit (``_handle_stopped_container``), exactly like a
     CLI agent that never reached a terminal verb.
  2. **Memory isolation.** ``HUMMIN_MEMORY=0`` is force-set in the container
     env: hummin's memory extension must never run inside a RoboCo agent,
     or 26 delivery agents would flood the operator's personal vault (the
     same bug class hummin's own spawned-children rule guards against).

The initial prompt travels as an **env var, not a positional CLI arg** (the
entrypoint folds it into a single quoted ``-p`` argv token) — the same
flag-injection safety property as every other CLI provider. V1 requires the
key BEFORE the run starts: hummin's own auth preflight exits 78 when
``ZAI_API_KEY`` is absent/invalid, and the orchestrator parks the provider.

**V1 scope**: one-shot delivery roles only (developer / qa / documenter /
cell_pm / main_pm / pr_reviewer / board). No interactive intake/secretary
support — there is no ``roboco-agent-hummin-prompter`` / ``-secretary``
image.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from typing import TYPE_CHECKING, Protocol

from roboco.config import settings
from roboco.llm.providers._docker import container_running, stop_container
from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult
from roboco.runtime.compose_labels import compose_label_args

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig

_log = logging.getLogger(__name__)

# The Hummin agent image (own image, like every other agent role).
_DEFAULT_HUMMIN_IMAGE = "roboco-agent-hummin:latest"

# The hummin CLI model id, pinned via Settings (parity with kimi_cli_model /
# gemini_cli_model). Bare zai-catalog id (e.g. "glm-5.3") — the entrypoint
# passes it as `--model zai/<id>`.
_HUMMIN_CLI_MODEL = settings.hummin_cli_model

# In-container paths.
_MCP_CONFIG_IN_CONTAINER = "/app/mcp-config.json"
# Per-agent data dir (the host side is reused from the shared assembly): the
# entrypoint writes the captured token usage here so the orchestrator reads it
# back at finalize, the hummin analogue of the mounted Claude transcript.
_HUMMIN_USAGE_DIR_IN_CONTAINER = "/home/agent/.hummin-usage"
_HUMMIN_USAGE_FILE_IN_CONTAINER = f"{_HUMMIN_USAGE_DIR_IN_CONTAINER}/usage.json"


def _container_name(agent_id: str) -> str:
    return f"roboco-agent-{agent_id}"


class _HumminHost(Protocol):
    """The orchestrator surface HumminCliProvider reuses for container assembly.

    Typed as a Protocol so this module never imports ``AgentOrchestrator`` (no
    import cycle) and is trivially mockable in tests.
    """

    async def _remove_container(
        self, container_name: str, *, stop_reason: str | None = None
    ) -> None: ...

    def _ensure_hummin_usage_dir(self, agent_id: str) -> None: ...

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


class HumminCliProvider(AgentProvider):
    """Spawn a hummin (GLM-native CLI) agent as a gateway-less container.

    The OpenRouter auth shape (static key via env, no ``~/.`` mount, no
    refresh loop) on the kimi spawn flow (dedicated CLI image, rendered
    config, tee'd JSONL run log) — contrast
    :class:`~roboco.llm.providers.kimi.KimiCliProvider` /
    :class:`~roboco.llm.providers.codex.CodexCliProvider` which mount a
    subscription credential.
    """

    def __init__(self, host: _HumminHost, image: str | None = None) -> None:
        self._host = host
        self._image = image or _DEFAULT_HUMMIN_IMAGE

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        if not config.mcp_config_path:
            raise ProviderError(
                "HUMMIN spawn requires an MCP config (gateway access).",
                agent_id=config.agent_id,
            )
        # Key-based auth (the OpenRouter shape) but WARN, not raise: unlike
        # OpenRouter there is a second key source (a ZAI_API_KEY riding the
        # orchestrator env, forwarded below), and hummin's own auth preflight
        # fails the container fast (exit 78) so the orchestrator parks the
        # provider with a clear remediation either way.
        zai_api_key = config.provider_auth_token or os.environ.get("ZAI_API_KEY", "")
        if not zai_api_key:
            _log.warning(
                "HUMMIN spawn for agent %s has no stored provider key AND no "
                "ZAI_API_KEY in the orchestrator env — the container will "
                "start but hummin's auth preflight will refuse to run (exit "
                "78). Set the key via PUT /api/providers/hummin-key (or "
                "export ZAI_API_KEY for the orchestrator) before spawning "
                "hummin agents.",
                config.agent_id,
            )

        container_name = _container_name(config.agent_id)
        await self._host._remove_container(
            container_name, stop_reason="pre_spawn_stale_clear"
        )
        # Pre-create the per-agent data dir (world-writable) before the bind
        # mount so the non-root agent can write the usage file (else EACCES).
        self._host._ensure_hummin_usage_dir(config.agent_id)

        # Reuse the orchestrator's mount/auth/git assembly so the agent gets
        # the full identity + git wiring. Blank the provider routing fields
        # first: otherwise the shared builder would inject the provider
        # endpoint as ANTHROPIC_BASE_URL/AUTH_TOKEN (the wrong runtime) —
        # hummin authenticates from ZAI_API_KEY, which this provider injects
        # itself under the zai env name.
        mount_config = dataclasses.replace(
            config, provider_base_url=None, provider_auth_token=None
        )
        hosts = self._host._resolve_host_paths(config, agent_settings_path)
        cmd = self._host._build_mount_args(container_name, mount_config, hosts)
        self._host._append_agent_auth_env(cmd, config)
        self._host._append_git_context_env(cmd, config)
        # No auth mount — the OpenRouter shape (static key via env).
        self._append_usage_mount(cmd, hosts)
        self._append_hummin_env(cmd, config, initial_prompt, zai_api_key)
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
                f"Failed to start hummin container: {stderr.decode().strip()}",
                agent_id=config.agent_id,
            )
        return SpawnResult(
            instance_id=container_name,
            extra={
                "container_id": stdout.decode().strip(),
                "model": config.model or _HUMMIN_CLI_MODEL,
            },
        )

    @staticmethod
    def _append_usage_mount(cmd: list[str], hosts: dict[str, str | None]) -> None:
        """Mount the per-agent data dir so the orchestrator reads usage back.

        Reuses the shared per-agent host dir (``hosts["hummin_usage"]``); the
        entrypoint writes ``usage.json`` here after the run. Without it a
        hummin agent finalizes at 0 tokens / $0.
        """
        data_host = hosts.get("hummin_usage")
        if data_host:
            cmd.extend(["-v", f"{data_host}:{_HUMMIN_USAGE_DIR_IN_CONTAINER}"])

    def _append_hummin_env(
        self,
        cmd: list[str],
        config: AgentConfig,
        initial_prompt: str | None,
        zai_api_key: str,
    ) -> None:
        """Append the runtime env the hummin-cli entrypoint + renderer read.

        ``ZAI_API_KEY`` is the GLM Coding Plan credential (the provider row's
        Fernet-decrypted key, or an orchestrator-env fallback).
        ``ROBOCO_AGENT_MODEL`` carries the BARE zai-catalog id (the
        entrypoint prefixes it as ``--model zai/<id>``).
        ``ROBOCO_MCP_CONFIG`` is passed for provenance but is INERT in V1 —
        hummin has no MCP client (see the module docstring).
        ``HUMMIN_MEMORY=0`` keeps hummin's memory extension out of the
        operator's personal vault. The prompt travels as an env var (never
        an argv positional).
        """
        cmd.extend(
            [
                "-e",
                f"ROBOCO_AGENT_ID={config.agent_id}",
                "-e",
                f"ROBOCO_AGENT_MODEL={config.model or _HUMMIN_CLI_MODEL}",
                "-e",
                f"ROBOCO_MCP_CONFIG={_MCP_CONFIG_IN_CONTAINER}",
                "-e",
                f"ROBOCO_INITIAL_PROMPT={initial_prompt or ''}",
                "-e",
                f"ROBOCO_HUMMIN_USAGE_FILE={_HUMMIN_USAGE_FILE_IN_CONTAINER}",
                "-e",
                f"ZAI_API_KEY={zai_api_key}",
                # Memory isolation (module docstring, point 2): hummin's
                # memory extension must never write the personal vault from
                # a RoboCo agent container.
                "-e",
                "HUMMIN_MEMORY=0",
            ]
        )

    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        await stop_container(instance_id, graceful)

    async def health_check(self, instance_id: str) -> bool:
        return await container_running(instance_id)

    async def remove(self, instance_id: str) -> None:
        await self._host._remove_container(instance_id)
