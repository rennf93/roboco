"""Grok provider — xAI ``grok-build-0.1`` as a native OpenAI-protocol agent.

xAI's API is OpenAI-compatible *only* (``https://api.x.ai/v1``) — there is no
native Anthropic-Messages endpoint — so a Grok agent cannot run through the
Claude Code path (which speaks the Anthropic Messages API via
``ANTHROPIC_BASE_URL`` injection). Instead it runs an OpenAI-protocol agent CLI
(the OpenCode pattern), pointed at ``api.x.ai/v1`` with the operator's xAI key.

Design — this provider deliberately **reuses the orchestrator's proven container
assembly** (``_build_mount_args`` / ``_append_agent_auth_env`` /
``_append_git_context_env``). That means a Grok agent gets the *same* RoboCo MCP
gateway wiring as every other agent **by construction**:

  * ``/app/mcp-config.json`` (the ``roboco-flow`` / ``roboco-do`` gateway) and
    ``/app/system-prompt.md`` are mounted by ``_build_mount_args``;
  * the spawn manifest + ``ROBOCO_GATEWAY_ENABLED`` are set there too;
  * the agent HMAC identity is injected by ``_append_agent_auth_env``.

An OpenAI-protocol provider that built its own bespoke spawn would have to
re-wire the MCP config itself, and skipping it leaves agents with zero gateway
verbs. Reusing the shared assembly makes the gateway wiring non-optional here.

Only two things differ from the Claude Code spawn:
  1. **LLM env** — ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` (xAI) instead of the
     ``ANTHROPIC_*`` injection. The provider routing fields are blanked before
     the shared mount step so the xAI endpoint is never mislabelled as Anthropic.
  2. **Runtime** — the ``roboco-agent-grok`` image, whose entrypoint launches the
     OpenAI-protocol CLI from the env below (model, MCP config, system prompt,
     prompt). The image + the exact CLI invocation are the one remaining piece to
     finalise together with xAI ("review + help implement + test").

The initial prompt is passed via an **env var, not a positional CLI arg**, which
structurally avoids a flag-injection vector: a prompt starting with ``--`` passed
as a positional argument could otherwise be parsed as CLI options.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from typing import TYPE_CHECKING, Protocol

from roboco.agents_config import get_agent_role
from roboco.llm.providers._docker import container_running, stop_container
from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult
from roboco.services.gateway.role_config import get_role_config

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig

# The Grok agent image (own image, like every other agent role). Built as the
# infra follow-up — bundles the OpenAI-protocol agent CLI + an entrypoint that
# reads the env contract below. Overridable for tests / staged rollout.
_DEFAULT_GROK_IMAGE = os.environ.get(
    "ROBOCO_GROK_AGENT_IMAGE", "roboco-agent-grok:latest"
)

# Default xAI endpoint when the seeded provider row carries no base_url.
_DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"

# In-container paths mounted by the orchestrator's `_build_mount_args`.
_MCP_CONFIG_IN_CONTAINER = "/app/mcp-config.json"
_SYSTEM_PROMPT_IN_CONTAINER = "/app/system-prompt.md"
# opencode's data dir inside the agent (HOME=/home/agent); opencode.db lands
# here. Mounted to a per-agent host dir so the orchestrator can read usage back
# (mirror of roboco.llm.providers.opencode_usage.DEFAULT_DB_PATH's parent).
_OPENCODE_DATA_DIR_IN_CONTAINER = "/home/agent/.local/share/opencode"

# Reasoning effort by role. grok-build-0.1 reasons heavily by default, and
# reasoning bills at the output rate, so it dominates cost. Code-quality roles
# (developer, qa, pr_reviewer) keep full reasoning; coordination / docs / board
# roles request "minimal". opencode receives this via its `--variant` flag (and
# the serve message `variant` field). NOTE: whether opencode actually applies a
# named reasoning variant to grok-build-0.1 without a provider-defined `variants`
# block is UNVERIFIED — passing the flag does not error, but the reasoning-cost
# reduction must be measured on the NAS; treat the saving as best-effort, not
# guaranteed. Operators can force one effort for ALL grok agents with the
# ROBOCO_GROK_REASONING_EFFORT env on the orchestrator (value "minimal" | "high"
# | "max", or "default"/"full" to use full reasoning).
_MINIMAL_REASONING_ROLES = frozenset(
    {
        "cell_pm",
        "main_pm",
        "documenter",
        "product_owner",
        "head_marketing",
        "auditor",
        "prompter",
        "secretary",
    }
)
_FULL_REASONING_OVERRIDES = frozenset({"default", "full", "none", ""})

# Per-role opencode permission policy (Claude-parity with
# orchestrator._get_role_permissions). Claude denies Write/Edit for the read-only
# roles and Bash(git commit/push) for PMs; opencode's permission is coarser
# (allow/deny per tool class), so:
#   * edit  — allow only roles that write code (role_config.allows_write:
#     developer / documenter). Everyone else edit=deny.
#   * bash  — allow only roles that legitimately run a shell; the read-only
#     reviewers (qa / pr_reviewer / auditor) and the board never do. secret-scrub
#     still guards bash (git-mutate / cred files) for the roles that keep it.
#   * external_directory — only the pr_reviewer reads scratch outside its cwd
#     (a diff it writes to /tmp). Delivery roles work inside their workspace, so
#     external_directory=deny (the headless-ask auto-deny that blocked the
#     pr-reviewer is moot once that one role is explicitly allowed).
_BASH_ROLES = frozenset({"developer", "documenter", "cell_pm", "main_pm"})
_EXTERNAL_DIR_ROLES = frozenset({"pr_reviewer"})


def _edit_permission_for(agent_id: str) -> str:
    """opencode ``edit`` permission for an agent's role (allow iff it writes code)."""
    role = get_agent_role(agent_id) or ""
    try:
        return "allow" if get_role_config(role).allows_write else "deny"
    except KeyError:
        return "deny"  # unknown role → safest


def _bash_permission_for(agent_id: str) -> str:
    """opencode ``bash`` permission for an agent's role."""
    return "allow" if (get_agent_role(agent_id) or "") in _BASH_ROLES else "deny"


def _external_dir_permission_for(agent_id: str) -> str:
    """opencode ``external_directory`` permission for an agent's role."""
    role = get_agent_role(agent_id) or ""
    return "allow" if role in _EXTERNAL_DIR_ROLES else "deny"


def _reasoning_effort_for(agent_id: str) -> str | None:
    """Resolve the opencode --variant reasoning effort for an agent.

    Returns ``None`` to use opencode's default (full) reasoning. A global
    override env wins over the per-role default.
    """
    override = os.environ.get("ROBOCO_GROK_REASONING_EFFORT", "").strip()
    if override:
        return None if override.lower() in _FULL_REASONING_OVERRIDES else override
    role = get_agent_role(agent_id) or ""
    return "minimal" if role in _MINIMAL_REASONING_ROLES else None


def _container_name(agent_id: str) -> str:
    return f"roboco-agent-{agent_id}"


class _GrokHost(Protocol):
    """The orchestrator surface GrokProvider reuses for container assembly.

    Typed as a Protocol so this module never imports ``AgentOrchestrator``
    (no import cycle) and is trivially mockable in tests.
    """

    async def _remove_container(self, container_name: str) -> None: ...

    def _ensure_opencode_data_dir(self, agent_id: str) -> None: ...

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


class GrokProvider(AgentProvider):
    """Spawn a Grok (xAI, OpenAI-protocol) agent as a gateway-wired container."""

    def __init__(self, host: _GrokHost, image: str | None = None) -> None:
        self._host = host
        self._image = image or _DEFAULT_GROK_IMAGE

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        if not config.provider_auth_token:
            raise ProviderError(
                "GROK spawn requires an xAI API key — set the Grok provider key "
                "in Settings (PUT /api/providers/grok/key).",
                agent_id=config.agent_id,
            )
        if not config.mcp_config_path:
            raise ProviderError(
                "GROK spawn requires an MCP config (gateway access).",
                agent_id=config.agent_id,
            )

        container_name = _container_name(config.agent_id)
        await self._host._remove_container(container_name)
        # Pre-create the opencode store dir (world-writable) before the bind mount
        # so the non-root agent user can write opencode.db / repos (else EACCES).
        self._host._ensure_opencode_data_dir(config.agent_id)

        # Reuse the orchestrator's mount/auth/git assembly so the agent gets the
        # full MCP gateway + identity wiring. Blank the provider routing fields
        # first: otherwise the shared builder would inject the xAI endpoint as
        # ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN (wrong protocol).
        mount_config = dataclasses.replace(
            config, provider_base_url=None, provider_auth_token=None
        )
        hosts = self._host._resolve_host_paths(config, agent_settings_path)
        cmd = self._host._build_mount_args(container_name, mount_config, hosts)
        self._host._append_agent_auth_env(cmd, config)
        self._host._append_git_context_env(cmd, config)
        self._append_opencode_data_mount(cmd, hosts)
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
            extra={"container_id": stdout.decode().strip(), "model": config.model},
        )

    def _append_opencode_data_mount(
        self, cmd: list[str], hosts: dict[str, str | None]
    ) -> None:
        """Mount the per-agent opencode data dir so the orchestrator can read it.

        opencode persists token usage to ``opencode.db`` under its data dir
        (``$HOME/.local/share/opencode``). Binding a per-agent host dir there
        lets the finalizer read the store back over the shared data volume —
        the opencode analogue of the mounted Claude transcript. Without this a
        Grok agent finalizes at 0 tokens / $0.
        """
        opencode_host = hosts.get("opencode")
        if opencode_host:
            cmd.extend(["-v", f"{opencode_host}:{_OPENCODE_DATA_DIR_IN_CONTAINER}"])

    def _append_grok_env(
        self, cmd: list[str], config: AgentConfig, initial_prompt: str | None
    ) -> None:
        """Append the OpenAI-protocol (xAI) env contract the grok image consumes.

        The prompt travels as an env var, never an argv positional, so a prompt
        beginning with ``--`` cannot be parsed as a CLI flag.
        """
        base_url = config.provider_base_url or _DEFAULT_XAI_BASE_URL
        cmd.extend(
            [
                # opencode's BUILT-IN xai provider authenticates from XAI_API_KEY
                # and reads XAI_BASE_URL for the endpoint — opencode_config emits
                # no provider block, so these envs are the only LLM wiring needed.
                "-e",
                f"XAI_API_KEY={config.provider_auth_token}",
                "-e",
                f"XAI_BASE_URL={base_url}",
                # Operational inputs for the grok image entrypoint.
                "-e",
                f"ROBOCO_AGENT_MODEL={config.model}",
                "-e",
                f"ROBOCO_MCP_CONFIG={_MCP_CONFIG_IN_CONTAINER}",
                "-e",
                f"ROBOCO_SYSTEM_PROMPT={_SYSTEM_PROMPT_IN_CONTAINER}",
                "-e",
                f"ROBOCO_INITIAL_PROMPT={initial_prompt or ''}",
            ]
        )
        if config.claude_session_id:
            # Reused as the generic agent session id so the transcript stays
            # locatable at finalize, exactly as on the Claude Code path.
            cmd.extend(["-e", f"ROBOCO_AGENT_SESSION_ID={config.claude_session_id}"])
        # Per-role opencode permissions (Claude-parity): read-only roles get
        # edit=deny, only delivery roles get bash, only the pr-reviewer gets
        # external-directory reads. opencode_config.main() reads these.
        cmd.extend(
            [
                "-e",
                f"ROBOCO_GROK_EDIT_PERMISSION={_edit_permission_for(config.agent_id)}",
                "-e",
                f"ROBOCO_GROK_BASH_PERMISSION={_bash_permission_for(config.agent_id)}",
                "-e",
                "ROBOCO_GROK_EXTERNAL_DIR_PERMISSION="
                f"{_external_dir_permission_for(config.agent_id)}",
            ]
        )
        # Reasoning effort (opencode --variant) by role; omitted = full reasoning.
        variant = _reasoning_effort_for(config.agent_id)
        if variant:
            cmd.extend(["-e", f"ROBOCO_GROK_VARIANT={variant}"])

    async def stop(self, instance_id: str, graceful: bool = True) -> None:
        await stop_container(instance_id, graceful)

    async def health_check(self, instance_id: str) -> bool:
        return await container_running(instance_id)

    async def remove(self, instance_id: str) -> None:
        await self._host._remove_container(instance_id)
