"""Claude Code Docker provider.

Wraps the orchestrator's Docker-container lifecycle for Claude Code agents.
This is the legacy/default provider — it implements the full ``docker run``
spawn, ``docker stop/kill``, health checks, and container removal with log
dumping.

Extracted from ``orchestrator.py`` to make the orchestrator provider-agnostic.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from roboco.agents_config import ALL_DOCS, get_agent_role, get_agent_team
from roboco.config import settings
from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult

# ---------------------------------------------------------------------------
# Module-level constants used by Docker spawn
# ---------------------------------------------------------------------------
# These are imported from the orchestrator's _helpers module to keep
# path resolution in one place.
from roboco.runtime._helpers import (
    AGENT_NETWORK,
    CLAUDE_AUTH_HOST_PATH,
    DATA_HOST_PATH,
    PROJECT_HOST_PATH,
    _agent_workspace_path,
    _build_manifest_for_agent,
    _cell_workspace_path,
    _resolve_agent_cli_model,
    _resolve_project_slug_from_git_context,
    get_agent_image,
)

# Role-to-workspace mapping used by _append_workspace_cwd.
# Extracted from the orchestrator so the provider can set the container cwd
# without importing the orchestrator class.
_ROLES_WITH_AGENT_WORKSPACE: frozenset[str] = frozenset(
    {"developer", "product_owner", "head_marketing"}
)
_ROLES_WITH_CELL_WORKSPACE: frozenset[str] = frozenset({"documenter"})

logger = structlog.get_logger()


class ClaudeCodeProvider(AgentProvider):
    """Launch Claude Code agents inside Docker containers.

    Each agent runs in a named ``roboco-agent-{slug}`` container on the
    ``roboco`` Docker network with workspace volumes, MCP config, and
    per-agent settings mounted from the host.

    Args:
        project_root: The orchestrator's project root path (used to resolve
            host paths when ``PROJECT_HOST_PATH`` is not set).
    """

    CONTAINER_PREFIX: str = "roboco-agent-"

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path.cwd()

    # =========================================================================
    # Public API (AgentProvider contract)
    # =========================================================================

    async def spawn(
        self,
        config: Any,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        """Spawn a Claude Code Docker container.

        The full lifecycle:
        1. Stale container removal (with log dump)
        2. Build Docker command (volumes, env, auth, MCP, workspace)
        3. Execute ``docker run -d``
        4. Return the container ID
        """
        container_name = self._container_name(config.agent_id)
        await self._remove_container(container_name)

        if not config.mcp_config_path:
            raise ProviderError(
                "MCP config path not set on AgentConfig",
                agent_id=config.agent_id,
            )

        hosts = self._resolve_host_paths(config, agent_settings_path)
        cmd = self._build_mount_args(container_name, config, hosts)
        self._append_agent_auth_env(cmd, config)
        self._append_git_context_env(cmd, config)
        self._append_image_and_claude_args(cmd, config, initial_prompt)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise ProviderError(
                f"Failed to start container: {stderr.decode().strip()}",
                agent_id=config.agent_id,
            )

        container_id = stdout.decode().strip()
        logger.info(
            "Docker container spawned",
            agent_id=config.agent_id,
            container_id=container_id[:12],
            model=config.model,
        )
        return SpawnResult(
            instance_id=container_id,
            agent_state="active",
            extra={"container_name": container_name},
        )

    async def stop(
        self,
        instance_id: str,
        graceful: bool = True,
    ) -> None:
        """Stop a running agent container."""
        container_name = instance_id
        try:
            if graceful:
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "stop",
                    "-t",
                    "10",
                    container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "kill",
                    container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            await proc.wait()
        except Exception as exc:
            raise ProviderError(
                f"Failed to stop container {container_name}",
                cause=exc,
            ) from exc

    async def health_check(self, instance_id: str) -> bool:
        """Check if the container is still running."""
        container_name = instance_id
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "--format={{.State.Status}}",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            status = stdout.decode().strip()
            return status == "running"
        except Exception:
            return False

    async def remove(self, instance_id: str) -> None:
        """Remove a stopped container (with log dump before removal)."""
        await self._remove_container(instance_id)

    # =========================================================================
    # Internal helpers (extracted from orchestrator.py)
    # =========================================================================

    @staticmethod
    def _container_name(agent_id: str) -> str:
        return f"{ClaudeCodeProvider.CONTAINER_PREFIX}{agent_id}"

    async def _remove_container(self, container_name: str) -> None:
        """Remove a container if it exists, dumping its logs to disk first.

        Docker deletes the container's json-file log when we ``docker rm``, so
        before removal we copy the current log to /data/logs/agents/{slug}/
        with a timestamp.  That gives us persistent history across respawns
        without needing an entrypoint wrapper inside the agent image.
        """
        # Check the container actually exists before trying to dump logs.
        inspect = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "--format={{.Id}}",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        exists = (await inspect.wait()) == 0

        if exists:
            slug = container_name.removeprefix("roboco-agent-")
            log_dir = Path("/data/logs/agents") / slug
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                log_path = log_dir / f"{timestamp}.log"
                with log_path.open("wb") as out:
                    dump_proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "logs",
                        container_name,
                        stdout=out,
                        stderr=out,
                    )
                    await dump_proc.wait()
                if log_path.stat().st_size == 0:
                    log_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(
                    "Could not dump container logs before removal",
                    container=container_name,
                    error=str(e),
                )

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    def _resolve_host_paths(
        self, config: Any, agent_settings_path: Path | None
    ) -> dict[str, str | None]:
        """Compute host mount paths for the Docker container."""
        mcp_name = config.mcp_config_path.name if config.mcp_config_path else ""
        if PROJECT_HOST_PATH:
            return {
                "docs": f"{PROJECT_HOST_PATH}/docs",
                "workspaces": f"{DATA_HOST_PATH}/workspaces",
                "claude": CLAUDE_AUTH_HOST_PATH,
                "mcp_config": f"{DATA_HOST_PATH}/mcp-configs/{mcp_name}",
                "prompt": (
                    f"{DATA_HOST_PATH}/prompts-generated/{config.agent_id}-prompt.md"
                ),
                "settings": (
                    f"{DATA_HOST_PATH}/agent-settings/{config.agent_id}-settings.json"
                    if agent_settings_path
                    else None
                ),
                "briefing": (
                    f"{DATA_HOST_PATH}/briefings/{config.agent_id}.md"
                    if config.briefing_path
                    else None
                ),
            }
        return {
            "docs": str((self._project_root / "docs").absolute()),
            "workspaces": str(Path(settings.workspaces_root)),
            "claude": CLAUDE_AUTH_HOST_PATH,
            "mcp_config": str(config.mcp_config_path),
            "prompt": str(
                Path(tempfile.gettempdir())
                / "roboco-prompts"
                / f"{config.agent_id}-prompt.md"
            ),
            "settings": str(agent_settings_path) if agent_settings_path else None,
            "briefing": (str(config.briefing_path) if config.briefing_path else None),
        }

    @staticmethod
    def _build_mount_args(
        container_name: str, config: Any, hosts: dict[str, str | None]
    ) -> list[str]:
        """Compose ``docker run -v/-e`` mount + env args for the agent."""
        cmd: list[str] = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--network",
            AGENT_NETWORK,
            "-v",
            f"{hosts['claude']}:/home/agent/.claude",
        ]
        ClaudeCodeProvider._append_claude_json_mount(cmd, hosts)
        ClaudeCodeProvider._append_optional_host_mounts(cmd, hosts)
        role = get_agent_role(config.agent_id) or "developer"
        cmd.extend(ClaudeCodeProvider._core_volume_and_env_args(config, hosts, role))
        ClaudeCodeProvider._append_provider_env(cmd, config)
        subagent_model = _resolve_agent_cli_model(config.provider_type, config.model)
        cmd.extend(["-e", f"CLAUDE_CODE_SUBAGENT_MODEL={subagent_model}"])
        ClaudeCodeProvider._append_manifest_args(cmd, config, subagent_model)
        ClaudeCodeProvider._append_workspace_cwd(cmd, config)
        return cmd

    @staticmethod
    def _append_claude_json_mount(cmd: list[str], hosts: dict[str, str | None]) -> None:
        """Mount host's ``~/.claude.json`` sibling FILE if present."""
        claude_dir = hosts["claude"]
        if not claude_dir:
            return
        claude_json_host = f"{claude_dir.rstrip('/')}.json"
        if Path(claude_json_host).exists():
            cmd.extend(["-v", f"{claude_json_host}:/home/agent/.claude.json"])

    @staticmethod
    def _append_optional_host_mounts(
        cmd: list[str], hosts: dict[str, str | None]
    ) -> None:
        """Mount agent settings.json and briefing.md when their hosts exist."""
        settings_host = hosts.get("settings")
        if settings_host:
            cmd.extend(["-v", f"{settings_host}:/home/agent/.claude/settings.json:ro"])
        briefing_host = hosts.get("briefing")
        if briefing_host:
            cmd.extend(["-v", f"{briefing_host}:/app/briefing.md:ro"])

    @staticmethod
    def _core_volume_and_env_args(
        config: Any, hosts: dict[str, str | None], role: str
    ) -> list[str]:
        """The always-on -v/-e block (prompt, docs, workspaces, env)."""
        docs_ro = "" if config.agent_id in ALL_DOCS else ":ro"
        return [
            "-v",
            f"{hosts['prompt']}:/app/system-prompt.md:ro",
            "-v",
            f"{hosts['docs']}:/app/docs{docs_ro}",
            "-v",
            f"{hosts['workspaces']}:/data/workspaces",
            "-v",
            f"{hosts['mcp_config']}:/app/mcp-config.json:ro",
            "-e",
            f"ROBOCO_AGENT_ID={config.agent_id}",
            "-e",
            f"ROBOCO_AGENT_ROLE={role}",
            "-e",
            "ROBOCO_API_URL=http://roboco-orchestrator:8000",
            "-e",
            "ROBOCO_SDK_PORT=9000",
            "-e",
            "ROBOCO_SDK_URL=http://localhost:9000",
            "-e",
            f"ROBOCO_AGENT_TOOL_CALL_WARN={settings.agent_tool_call_warn}",
            "-e",
            f"ROBOCO_AGENT_TOOL_CALL_HALT={settings.agent_tool_call_halt}",
            "-e",
            f"ROBOCO_AGENT_LOOP_THRESHOLD={settings.agent_loop_threshold}",
            "-e",
            f"ROBOCO_AGENT_LOOP_WINDOW={settings.agent_loop_window}",
            "-e",
            f"ROBOCO_AGENT_STOP_ATTEMPT_ALLOWANCE={settings.agent_stop_attempt_allowance}",
        ]

    @staticmethod
    def _append_provider_env(cmd: list[str], config: Any) -> None:
        """Inject ``ANTHROPIC_*`` env only on non-Anthropic providers."""
        if config.provider_base_url:
            cmd.extend(["-e", f"ANTHROPIC_BASE_URL={config.provider_base_url}"])
        if config.provider_auth_token:
            cmd.extend(["-e", f"ANTHROPIC_AUTH_TOKEN={config.provider_auth_token}"])

    @staticmethod
    def _append_manifest_args(cmd: list[str], config: Any, subagent_model: str) -> None:
        """Write the spawn manifest and flip the gateway flag."""
        manifest_host_path = _build_manifest_for_agent(config.agent_id, subagent_model)
        if manifest_host_path:
            cmd.extend(
                [
                    "-v",
                    f"{manifest_host_path}:/app/tool-manifest.json:ro",
                    "-e",
                    "ROBOCO_GATEWAY_ENABLED=true",
                    "-e",
                    "ROBOCO_TOOL_MANIFEST_PATH=/app/tool-manifest.json",
                ]
            )
        else:
            cmd.extend(["-e", "ROBOCO_GATEWAY_ENABLED=false"])

    @staticmethod
    def _append_workspace_cwd(cmd: list[str], config: Any) -> None:
        """Set the container ``-w`` to the agent or cell workspace by role."""
        role = get_agent_role(config.agent_id) or "developer"
        team = get_agent_team(config.agent_id) or ""
        project = _resolve_project_slug_from_git_context(config.git_context)
        if role in _ROLES_WITH_AGENT_WORKSPACE:
            cmd.extend(["-w", _agent_workspace_path(project, team, config.agent_id)])
        elif role in _ROLES_WITH_CELL_WORKSPACE:
            cmd.extend(["-w", _cell_workspace_path(project, team)])

    @staticmethod
    def _append_agent_auth_env(cmd: list[str], config: Any) -> None:
        """Append agent HMAC token env var to the docker run cmd."""
        from roboco.agents_config import (
            get_agent_role as _get_role,
        )
        from roboco.agents_config import (
            get_agent_team as _get_team,
        )
        from roboco.agents_config import (
            issue_agent_token,
        )

        _role = _get_role(config.agent_id)
        _team = _get_team(config.agent_id) or ""
        _token = issue_agent_token(config.agent_id, _role, _team)
        cmd.extend(["-e", f"ROBOCO_AGENT_TOKEN={_token}"])

    @staticmethod
    def _append_git_context_env(cmd: list[str], config: Any) -> None:
        """Append git-context env vars to the docker run cmd."""
        if not config.git_context:
            return
        if config.git_context.project_slug:
            cmd.extend(["-e", f"ROBOCO_PROJECT_SLUG={config.git_context.project_slug}"])
        if config.git_context.branch_name:
            cmd.extend(["-e", f"ROBOCO_BRANCH={config.git_context.branch_name}"])

    @staticmethod
    def _default_spawn_prompt() -> str:
        """Fallback prompt when the caller provided none."""
        return (
            "You may have been spawned without a specific task assignment. "
            "Follow your standard workflow:\n\n"
            "1. Call `give_me_work()` to find work for your role\n"
            "2. Begin the assigned task (its details arrive in the "
            "response): UNDERSTAND -> PLAN -> EXECUTE -> VERIFY -> HANDOFF\n"
            "3. If no tasks available, call `i_am_idle()` "
            "to shutdown gracefully\n\n"
            "Start now by scanning for work."
        )

    @staticmethod
    def _append_image_and_claude_args(
        cmd: list[str], config: Any, initial_prompt: str | None
    ) -> None:
        """Append the image + Claude Code CLI args to the docker run cmd."""
        claude_args = [
            get_agent_image(config.agent_id),
            "--model",
            _resolve_agent_cli_model(config.provider_type, config.model),
            "--system-prompt-file",
            "/app/system-prompt.md",
            "--mcp-config",
            "/app/mcp-config.json",
            "--strict-mcp-config",
            "--tools",
            "Read,Write,Edit,Bash,Grep,Glob,TodoWrite",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if config.claude_session_id:
            claude_args += ["--session-id", config.claude_session_id]
        claude_args += [
            "-p",
            initial_prompt or ClaudeCodeProvider._default_spawn_prompt(),
        ]
        cmd.extend(claude_args)
