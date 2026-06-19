"""Grok interactive session over the official ``grok`` CLI — the IntakeSession seam.

The Claude interactive roles (intake/secretary) run a held-open ``ClaudeSDKClient``.
Grok runs the same conversation on xAI's official ``grok`` CLI: there is no
long-lived server, so each human turn is one headless ``grok -p`` invocation.
Conversation context persists by **resuming the same grok session id** — turn 1
lets grok generate an id (read back from the terminal ``end`` event), and every
later turn passes ``-r <id>`` so grok reloads the prior transcript. The CLI's
``--output-format streaming-json`` events (``{type: thought|text|end}``) map to
the same :class:`StreamChunk` kinds the panel already renders, so the existing
``IntakeDriver`` loop / ``MessageSource`` / ``EventSink`` / relay are reused
unchanged — only the ``SessionFactory`` differs.

Verified live on the maintainer's machine (grok 0.2.56):
  * ``grok -p "<text>" --output-format json`` returns ``{text, sessionId, ...}``;
  * ``grok -p "<text>" -r <sessionId>`` reloads the conversation (a fact set on
    turn 1 is recalled on turn 2 under the same session id);
  * streaming-json emits ``{type:thought,data}`` / ``{type:text,data}`` deltas and
    a final ``{type:end, sessionId, stopReason}``; tool calls do NOT surface as
    stream events (so the intake draft is delivered by the propose_draft MCP tool
    POSTing to the relay, not intercepted here).

Token usage is captured after every turn: the chat reuses one session id, so the
session store's cumulative total is the running whole-chat usage and the last
write to ``usage.json`` wins (the orchestrator reads it back at reap).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from roboco.agent_sdk.intake_driver import StreamChunk, _extract_draft
from roboco.agents_config import get_agent_role
from roboco.llm.providers.grok_cli_config import grok_cli_args_for_role
from roboco.llm.providers.grok_cli_usage import capture_session_usage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

_DEFAULT_MODEL = os.environ.get("ROBOCO_AGENT_MODEL", "grok-build")
# A rate-limit / quota end to a turn leaves no terminal verb on the one-shot path
# and must read clearly on the interactive one; detected from the run's stderr.
_RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "too many requests",
    "quota",
    "insufficient_quota",
)


def _parse_event(line: str) -> dict[str, Any] | None:
    """Parse one streaming-json NDJSON line into an event dict, tolerantly."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


class _StreamAssembler:
    """Maps grok streaming-json events to ``StreamChunk``s, holding turn state.

    Pure and synchronous so it is unit-tested without the live binary: feed it
    parsed event dicts, collect the chunks it returns, then read ``session_id``
    (for the next turn's ``-r``) and ``saw_end`` (to detect an abnormal turn).

    Reasoning (``thought``) deltas are coalesced into one ``thinking`` chunk
    (flushed when the answer starts or at turn end) — the panel renders reasoning
    as a block, like the Claude path. Answer (``text``) deltas stream live, one
    chunk each, for the live-typing effect.
    """

    def __init__(self) -> None:
        self._thinking: list[str] = []
        self._text: list[str] = []
        self.session_id: str | None = None
        self.stop_reason: str | None = None
        self.saw_end: bool = False

    def _flush_thinking(self) -> list[StreamChunk]:
        if not self._thinking:
            return []
        text = "".join(self._thinking)
        self._thinking = []
        return [StreamChunk(kind="thinking", text=text)] if text else []

    def feed(self, event: dict[str, Any]) -> list[StreamChunk]:
        """Return the chunks to emit for one event (may be empty)."""
        etype = str(event.get("type", ""))
        if etype == "thought":
            self._thinking.append(str(event.get("data", "")))
            return []
        if etype == "text":
            out = self._flush_thinking()
            piece = str(event.get("data", ""))
            if piece:
                self._text.append(piece)
                out.append(StreamChunk(kind="text", text=piece))
            return out
        if etype == "end":
            return self._finish(event)
        return []  # unknown event types ignored (tolerant)

    def _finish(self, event: dict[str, Any]) -> list[StreamChunk]:
        out = self._flush_thinking()
        sid = event.get("sessionId") or event.get("session_id")
        if isinstance(sid, str) and sid:
            self.session_id = sid
        self.stop_reason = str(event.get("stopReason") or "") or None
        self.saw_end = True
        # Draft fallback only: the canonical draft path is the propose_draft MCP
        # tool POSTing straight to the relay (tool calls do not surface as stream
        # events), but if the agent typed a fenced ```roboco-draft``` block we
        # still surface it.
        draft = _extract_draft("".join(self._text))
        if draft is not None:
            out.append(StreamChunk(kind="draft", data=draft))
        out.append(
            StreamChunk(
                kind="turn_end",
                data={"session_id": self.session_id, "stop_reason": self.stop_reason},
            )
        )
        return out


def _classify_failure(returncode: int | None, stderr: str) -> str:
    """A human-readable error for a turn that ended without an ``end`` event."""
    blob = stderr.strip()
    if any(marker in blob.lower() for marker in _RATE_LIMIT_MARKERS):
        return (
            "Grok is rate-limited right now (the SuperGrok quota is exhausted); "
            "please wait a moment and send your message again."
        )
    detail = blob.splitlines()[-1] if blob else f"exit code {returncode}"
    return f"The Grok turn ended unexpectedly ({detail}). Please try again."


class GrokCliSession:  # pragma: no cover - needs the live grok binary
    """``IntakeSession`` backed by per-turn headless ``grok -p`` invocations.

    Async context manager with no held-open process: ``__aenter__`` returns self,
    ``__aexit__`` is a no-op (each turn owns its own subprocess). ``send`` runs one
    turn, resuming the captured grok session id so conversation context persists.
    """

    def __init__(
        self,
        *,
        cwd: str,
        agent_id: str,
        model: str = _DEFAULT_MODEL,
        usage_file: str | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        self._cwd = cwd
        self._agent_id = agent_id
        self._model = model
        self._usage_file = usage_file or os.environ.get("ROBOCO_GROK_USAGE_FILE")
        # The secretary's ROBOCO_AGENT_ID is its UUID (not a slug), so resolve the
        # role from the id when possible, else the container's ROBOCO_AGENT_ROLE.
        role = get_agent_role(agent_id) or os.environ.get("ROBOCO_AGENT_ROLE", "")
        self._role_args = grok_cli_args_for_role(role)
        self._extra_args = list(extra_args or [])
        self._session_id: str | None = None

    async def __aenter__(self) -> GrokCliSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _build_argv(self, text: str) -> list[str]:
        argv = [
            "grok",
            "-p",
            text,
            "-m",
            self._model,
            "--cwd",
            self._cwd,
            "--output-format",
            "streaming-json",
            *self._role_args,
            *self._extra_args,
        ]
        if self._session_id:
            argv += ["-r", self._session_id]
        return argv

    async def send(self, text: str) -> AsyncIterator[StreamChunk]:
        """Run one turn (one ``grok -p`` invocation) and yield its chunks.

        A turn always ends with a ``turn_end`` chunk; a failure (spawn error,
        non-zero exit, or no ``end`` event) yields an ``error`` chunk first so the
        panel never renders a blank turn.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._build_argv(text),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            logger.error("grok turn could not start", error=str(exc))
            yield StreamChunk(kind="error", text=f"Could not start Grok: {exc}")
            yield StreamChunk(kind="turn_end", data={})
            return

        assembler = _StreamAssembler()
        assert proc.stdout is not None
        async for raw in proc.stdout:
            event = _parse_event(raw.decode("utf-8", "replace").strip())
            if event is None:
                continue
            for chunk in assembler.feed(event):
                yield chunk

        stderr_bytes = await proc.stderr.read() if proc.stderr else b""
        stderr = stderr_bytes.decode("utf-8", "replace")
        await proc.wait()

        if assembler.session_id:
            self._session_id = assembler.session_id
        self._capture_usage()

        if not assembler.saw_end:
            logger.error(
                "grok turn ended without a result",
                returncode=proc.returncode,
                stderr=stderr.strip()[:500],
            )
            yield StreamChunk(
                kind="error", text=_classify_failure(proc.returncode, stderr)
            )
            yield StreamChunk(kind="turn_end", data={})

    def _capture_usage(self) -> None:
        """Best-effort: rewrite usage.json with the chat's cumulative total."""
        if not (self._usage_file and self._session_id):
            return
        capture_session_usage(
            cwd=self._cwd,
            session_id=self._session_id,
            model=self._model,
            out_path=Path(self._usage_file),
        )
