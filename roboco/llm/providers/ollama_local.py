"""Ollama local subprocess provider.

Spawns agents as local subprocesses talking to an Ollama server on the same
machine.  No Docker required — the agent runs as a direct ``uv run`` process
with Ollama's OpenAI-compatible API endpoint.

This provider is ideal for:
- Development / testing without Docker overhead
- Resource-constrained hosts that can't run 20 Docker containers
- Offline / air-gapped setups with local models
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

import httpx
import structlog

from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult

logger = structlog.get_logger()


class OllamaLocalProvider(AgentProvider):
    """Launch agents as local ``claude`` processes using an Ollama endpoint.

    Each agent writes its transcript to ``{OLLAMA_LOCAL_LOG_DIR}/{agent_id}/``
    and is tracked by PID.  The process is managed via asyncio subprocess.

    Configuration (via env / settings):
        OLLAMA_BASE_URL       — default ``http://localhost:11434``
        OLLAMA_LOCAL_LOG_DIR  — default ``/data/logs/ollama-agents``
        AGENT_MODEL           — default ``kimi-k2.6:cloud``
    """

    def __init__(
        self,
        ollama_base_url: str | None = None,
        log_dir: str | None = None,
        default_model: str = "kimi-k2.6:cloud",
    ) -> None:
        self._ollama_base_url = (
            ollama_base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")
        self._log_dir = Path(
            log_dir or os.environ.get("OLLAMA_LOCAL_LOG_DIR", "/data/logs/ollama-agents")
        )
        self._default_model = default_model
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    @property
    def ollama_base_url(self) -> str:
        return self._ollama_base_url

    async def _verify_ollama_reachable(self) -> None:
        """Raise ``ProviderError`` if the Ollama server is down."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._ollama_base_url}/api/tags")
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Ollama server at {self._ollama_base_url} is unreachable: {exc}",
                cause=exc,
            ) from exc

    async def spawn(
        self,
        config: Any,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        """Launch a local subprocess agent.

        The agent runs as::

            claude --model {model} --allowed-tools ... --session-id {uuid}

        with ``ANTHROPIC_BASE_URL`` and ``ANTHROPIC_AUTH_TOKEN`` set to
        the Ollama endpoint (using an empty/noop auth token since Ollama
        doesn't require one).
        """
        agent_id = config.agent_id
        model = config.model or self._default_model

        # Verify Ollama is responding before trying to spawn.
        await self._verify_ollama_reachable()

        # Prepare log directory.
        agent_log_dir = self._log_dir / agent_id
        agent_log_dir.mkdir(parents=True, exist_ok=True)

        # Build the process command.
        cmd = await self._build_claude_cmd(config, model, initial_prompt)

        # Set environment for Ollama routing.
        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = f"{self._ollama_base_url}/v1"
        env["ANTHROPIC_AUTH_TOKEN"] = ""  # Ollama doesn't use auth tokens
        env["ANTHROPIC_MODEL"] = model

        logger.info(
            "Spawning Ollama-local agent",
            agent_id=agent_id,
            model=model,
            cmd=cmd,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(agent_log_dir),
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                f"Claude binary not found — is it installed? ({exc})",
                agent_id=agent_id,
                cause=exc,
            ) from exc

        self._processes[agent_id] = proc
        pid = str(proc.pid)

        # Write PID file for external monitoring.
        pid_path = agent_log_dir / "agent.pid"
        pid_path.write_text(pid)

        logger.info(
            "Ollama-local agent spawned",
            agent_id=agent_id,
            pid=pid,
            log_dir=str(agent_log_dir),
        )

        return SpawnResult(
            instance_id=pid,
            agent_state="active",
            extra={
                "pid": pid,
                "log_dir": str(agent_log_dir),
                "model": model,
                "ollama_base_url": self._ollama_base_url,
                "cmd": cmd,
            },
        )

    async def stop(
        self,
        instance_id: str,
        graceful: bool = True,
    ) -> None:
        """Terminate the subprocess by PID."""
        pid = int(instance_id)
        try:
            if graceful:
                os.kill(pid, signal.SIGTERM)
                # Give the process 10 seconds to exit gracefully.
                for _ in range(50):
                    try:
                        os.kill(pid, 0)  # still alive?
                        await asyncio.sleep(0.2)
                    except OSError:
                        return  # clean exit
                # Force kill after grace period.
            # Force kill: use SIGKILL (POSIX) or SIGTERM (Windows).
            sig = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(pid, sig)
        except ProcessLookupError:
            pass  # already dead
        except Exception as exc:
            raise ProviderError(
                f"Failed to stop process {pid}",
                cause=exc,
            ) from exc

    async def health_check(self, instance_id: str) -> bool:
        """Check if the process is still alive."""
        try:
            pid = int(instance_id)
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    async def remove(self, instance_id: str) -> None:
        """Clean up PID file and process tracking."""
        pid = instance_id
        # Find and clean up the log dir.
        for agent_id, proc in list(self._processes.items()):
            if str(proc.pid) == pid:
                del self._processes[agent_id]
                pid_path = self._log_dir / agent_id / "agent.pid"
                try:
                    pid_path.unlink(missing_ok=True)
                except OSError:
                    pass
                break

    async def _build_claude_cmd(
        self,
        config: Any,
        model: str,
        initial_prompt: str | None,
    ) -> list[str]:
        """Build the Claude CLI command line.

        This mirrors the Claude CLI invocation used in the Docker-based
        provider but runs it directly on the host.
        """
        cmd = ["claude"]

        if config.claude_session_id:
            cmd.extend(["--session-id", config.claude_session_id])

        cmd.extend(["--model", model])
        cmd.extend(["--allowed-tools", "Read,Write,Edit,Bash,Grep,Glob,TodoWrite"])

        if initial_prompt:
            cmd.extend(["--prompt", initial_prompt])

        return cmd
