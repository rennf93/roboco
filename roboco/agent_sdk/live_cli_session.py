"""Provider-generic interactive session over headless CLI invocations.

The interactive Intake/Secretary roles must honor WHATEVER provider the
operator routed (the 2026-09-17 directive: the selected provider powers ALL
agents, not just delivery roles). GROK already has a per-turn CLI session
(:class:`roboco.agent_sdk.grok_cli_session.GrokCliSession`); this module is
the provider-generic twin for every other headless CLI (hummin, codex,
gemini, kimi, and the opencode pair openrouter/nebius).

Same ``IntakeSession`` seam as grok: async context manager, ``send(text)``
yielding :class:`StreamChunk`s ending with ``turn_end``. Differences:

  * **Conversation context** is carried by RE-INJECTION, not CLI session
    resume: the driver holds the prior turns and prefixes each prompt with
    the transcript so far. Every CLI then works uniformly (resume flags
    differ per CLI version). Chats are short; re-injection is the honest
    V1. The system prompt reaches the CLI via its rendered config (the
    delivery render step writes the role blueprint), so it is NOT
    duplicated into the prompt.
  * **Output parsing** is the tolerant multi-dialect accumulator from
    :mod:`roboco.agent_sdk.live_providers` (hummin ``message_end``, codex
    ``agent_message``, gemini ``response``, opencode text parts, kimi
    tolerant shapes; plain stdout as the fallback).
  * **Tools** (the CEO-authority / intake servers) are wired by the
    container, not by this class: MCP-capable CLIs get them through the
    rendered config's mcpServers (the live mcp-config.json mount);
    hummin gets them through the rendered pi extension (registerTool +
    fetch). Both call the backend with the container's HMAC token.

Token usage: V1 does not capture per-turn usage for these CLIs (delivery
finalizers meter one-shot runs; the live chat surfaces provider-side
billing).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import TYPE_CHECKING

import structlog

from roboco.agent_sdk.intake_driver import StreamChunk, _extract_draft
from roboco.agent_sdk.live_providers import LiveReplyAccumulator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

logger = structlog.get_logger()

_DEFAULT_TURN_TIMEOUT_SECONDS = 600.0


def _turn_timeout_seconds() -> float:
    """Per-turn watchdog timeout (ROBOCO_LIVE_TURN_TIMEOUT_SECONDS)."""
    raw = os.environ.get("ROBOCO_LIVE_TURN_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TURN_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_TURN_TIMEOUT_SECONDS


class HeadlessCliTurnSession:
    """``IntakeSession`` over per-turn headless CLI runs (provider-generic).

    ``argv_builder`` comes from :mod:`roboco.agent_sdk.live_providers` and
    receives the FULL per-turn prompt (transcript + new message). Transcript
    state lives in this instance, so hold ONE session open per chat.
    """

    def __init__(
        self,
        *,
        argv_builder: Callable[[str], list[str]],
        cwd: str = "/app",
        usage_file: str | None = None,
    ) -> None:
        self._argv_builder = argv_builder
        self._cwd = cwd
        self._usage_file = usage_file
        # Conversation transcript for re-injection: (role, text) pairs.
        self._history: list[tuple[str, str]] = []
        self._turn_timeout = _turn_timeout_seconds()

    async def __aenter__(self) -> HeadlessCliTurnSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _full_prompt(self, text: str) -> str:
        """Transcript-so-far + the new message (stateless re-injection)."""
        if not self._history:
            return text
        lines = ["Conversation so far (you are the assistant):"]
        for role, content in self._history:
            lines.append(f"{'User' if role == 'user' else 'Assistant'}: {content}")
        lines.append("")
        lines.append(f"User: {text}")
        lines.append("")
        lines.append("Answer the LAST User message, continuing the conversation.")
        return "\n".join(lines)

    def _record(self, role: str, text: str) -> None:
        text = text.strip()
        if text:
            self._history.append((role, text))
            # Bound the re-injection: the last 40 turns are plenty for a
            # live intake/secretary chat and keeps prompts finite.
            self._history = self._history[-40:]

    async def send(self, text: str) -> AsyncIterator[StreamChunk]:
        """Run one turn and yield chunks; always ends with ``turn_end``."""
        argv = self._argv_builder(self._full_prompt(text))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self._cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            logger.error("live CLI turn could not start", error=str(exc))
            yield StreamChunk(
                kind="error", text=f"Could not start the agent CLI: {exc}"
            )
            yield StreamChunk(kind="turn_end", data={})
            return

        assert proc.stdout is not None
        assert proc.stderr is not None
        # Drain stderr concurrently (pipe-buffer deadlock, same lesson as grok).
        stderr_task = asyncio.create_task(proc.stderr.read())

        accumulator = LiveReplyAccumulator()
        timed_out = False
        try:
            async for chunk in self._drain(proc.stdout, accumulator):
                yield chunk
        except TimeoutError:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

        stderr = (await stderr_task).decode("utf-8", "replace")
        await proc.wait()

        reply = accumulator.reply()
        self._record("user", text)
        self._record("assistant", reply)

        for chunk in self._finalize(timed_out, proc.returncode, stderr, reply):
            yield chunk

    def _finalize(
        self,
        timed_out: bool,
        returncode: int | None,
        stderr: str,
        reply: str,
    ) -> list[StreamChunk]:
        """Map a finished turn to its trailing chunks (error/draft/turn_end)."""
        if timed_out:
            return [
                StreamChunk(
                    kind="error",
                    text="The agent turn timed out; please try again.",
                ),
                StreamChunk(kind="turn_end", data={}),
            ]
        if returncode != 0:
            lines_ = stderr.strip().splitlines()
            detail = lines_[-1] if lines_ else f"exit code {returncode}"
            return [
                StreamChunk(
                    kind="error",
                    text=(
                        f"The agent turn ended unexpectedly "
                        f"({detail}). Please try again."
                    ),
                ),
                StreamChunk(kind="turn_end", data={}),
            ]
        if not reply:
            return [
                StreamChunk(
                    kind="error",
                    text="The agent returned an empty reply; please try again.",
                ),
                StreamChunk(kind="turn_end", data={}),
            ]
        chunks: list[StreamChunk] = []
        draft = _extract_draft(reply)
        if draft is not None:
            chunks.append(StreamChunk(kind="draft", data=draft))
        chunks.append(StreamChunk(kind="turn_end", data={}))
        return chunks

    async def _drain(
        self, stdout: asyncio.StreamReader, accumulator: LiveReplyAccumulator
    ) -> AsyncIterator[StreamChunk]:
        """Yield chunks from the CLI's stdout until EOF, honoring the deadline."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._turn_timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            raw = await asyncio.wait_for(stdout.readline(), timeout=remaining)
            if not raw:  # EOF
                return
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if not line.strip():
                continue
            delta = accumulator.feed(line)
            if delta:
                yield StreamChunk(kind="text", text=delta)
