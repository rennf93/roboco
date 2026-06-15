"""OpenCode subprocess provider.

Spawns agents as local ``opencode run`` processes.  OpenCode is an open-source
AI coding agent that supports multiple model providers (Z.AI, OpenRouter,
Anthropic, etc.).  No Docker required — the agent runs as a direct CLI process.

Configuration:
    OPENCODE_MODEL       — model string (default ``zai-coding-plan/glm-5.2``)
    OPENCODE_LOG_DIR     — log directory (default ``/tmp/opencode-logs``)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path
from typing import Any

import structlog

from roboco.llm.providers.base import AgentProvider, ProviderError, SpawnResult

logger = structlog.get_logger()

# Default model — override via constructor arg or OPENCODE_MODEL env var
_DEFAULT_MODEL = os.environ.get("OPENCODE_MODEL", "zai-coding-plan/glm-5.2")
_DEFAULT_LOG_DIR = os.environ.get("OPENCODE_LOG_DIR", "/tmp/opencode-logs")


class OpenCodeProvider(AgentProvider):
    """Launch agents as local ``opencode run`` subprocesses.

    Each agent spawns ``opencode run --model <model> "<prompt>"``,
    tracked by PID so the orchestrator can stop / health-check it.

    Args:
        default_model: Model string passed to ``--model`` (e.g.
            ``zai-coding-plan/glm-5.2``).
        log_dir: Directory for per-agent PIDs and logs.
    """

    def __init__(
        self,
        default_model: str | None = None,
        log_dir: str | None = None,
    ) -> None:
        self._default_model: str = default_model or _DEFAULT_MODEL
        self._log_dir = Path(log_dir or _DEFAULT_LOG_DIR)
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def spawn(
        self,
        config: Any,
        initial_prompt: str | None = None,
        _agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        """Launch ``opencode run --model <model> "<prompt>"`` as a subprocess."""
        agent_id = config.agent_id
        model = config.model or self._default_model
        prompt = initial_prompt or ""

        agent_log_dir = self._log_dir / agent_id
        agent_log_dir.mkdir(parents=True, exist_ok=True)

        cmd = ["opencode", "run", "--model", model]
        if prompt:
            cmd.append(prompt)
        else:
            cmd.append("Work through your assigned task step by step.")

        logger.info(
            "Spawning OpenCode agent",
            agent_id=agent_id,
            model=model,
            cmd=cmd,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(agent_log_dir),
            )
        except FileNotFoundError:
            raise ProviderError(
                "opencode binary not found — is it installed? "
                "Run: npm install -g opencode-ai",
                agent_id=agent_id,
            ) from None

        self._processes[agent_id] = proc
        pid = str(proc.pid)

        pid_path = agent_log_dir / "agent.pid"
        pid_path.write_text(pid)

        logger.info(
            "OpenCode agent spawned",
            agent_id=agent_id,
            pid=pid,
            model=model,
        )

        return SpawnResult(
            instance_id=pid,
            agent_state="active",
            extra={
                "pid": pid,
                "log_dir": str(agent_log_dir),
                "model": model,
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
            # Force kill.
            sig = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(pid, sig)
        except ProcessLookupError:
            pass  # already dead
        except Exception as exc:
            raise ProviderError(
                f"Failed to stop OpenCode process {pid}",
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
        for agent_id, proc in list(self._processes.items()):
            if str(proc.pid) == pid:
                del self._processes[agent_id]
                pid_path = self._log_dir / agent_id / "agent.pid"
                with contextlib.suppress(OSError):
                    pid_path.unlink(missing_ok=True)
                break
