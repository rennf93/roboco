"""Grok interactive session over ``opencode serve`` — the IntakeSession seam.

The Claude interactive roles (intake/secretary) run a held-open ``ClaudeSDKClient``.
Grok has no Claude binary, so its interactive runtime is ``opencode serve``: a
long-lived local opencode HTTP server (the rendered ``opencode.json`` wires the
xAI provider + the RoboCo MCP gateway + the system prompt as instructions). Each
human turn is one **synchronous** ``POST /session/:id/message`` ("send and wait"),
whose ``parts`` are mapped to the same :class:`StreamChunk` kinds the panel
already renders. Conversation context persists because the opencode session is
reused across turns. ``OpencodeServeSession`` satisfies the same ``IntakeSession``
protocol the Claude ``SdkIntakeSession`` does, so the existing ``IntakeDriver``
loop / ``MessageSource`` / ``EventSink`` / relay are reused unchanged.

Confirmed against opencode's server docs (https://opencode.ai/docs/server):
``opencode serve`` on 127.0.0.1:<port>, ``POST /session`` -> Session, and the
synchronous ``POST /session/:id/message`` -> ``{info, parts}``. Using the
synchronous endpoint avoids the async ``/event`` SSE-bus correlation; the
trade-off is that a turn's reply renders when it completes rather than as live
token deltas (a later enhancement, once verifiable against grok-build-0.1).

UNVERIFIED-LIVE: opencode's exact ``Part`` schema is read defensively here — the
mapping tolerates unknown shapes — and must be confirmed against a live
``opencode serve`` + grok-build-0.1 run on the NAS.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from roboco.agent_sdk.intake_driver import (
    StreamChunk,
    _draft_from_tool_input,
    _extract_draft,
    _is_propose_draft,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

_DEFAULT_PORT = 4096
_READY_TIMEOUT_S = 30.0
_READY_INTERVAL_S = 0.5


def _part_to_chunk(
    part: dict[str, Any],
) -> tuple[StreamChunk | None, str | None, dict[str, Any] | None]:
    """Classify one opencode message part -> (chunk, text_part, draft).

    Mirrors ``intake_driver._block_to_chunk`` for the opencode part shape and is
    tolerant of unknown shapes (skipped) since the Part schema may evolve.
    """
    ptype = str(part.get("type", ""))
    if ptype in ("reasoning", "thinking"):
        return StreamChunk(kind="thinking", text=str(part.get("text", ""))), None, None
    if ptype in ("tool", "tool-invocation", "tool_use"):
        name = str(part.get("tool") or part.get("name") or "")
        tool_input = part.get("input") or part.get("args") or {}
        if _is_propose_draft(name):
            return None, None, _draft_from_tool_input(tool_input)
        return (
            StreamChunk(kind="tool_use", tool=name, data={"input": tool_input}),
            None,
            None,
        )
    if ptype == "text":
        return None, str(part.get("text", "")), None
    return None, None, None


def _message_error(message: dict[str, Any]) -> str | None:
    """Human-readable text of a turn-level error (``info.error``), else ``None``.

    A model/turn failure (bad key, rate limit, model error) is reported by
    opencode in ``info.error`` with an EMPTY ``parts`` list — not as a part — so
    it must be surfaced explicitly or the turn renders blank (the original Claude
    intake bug). Confirmed live: a bad xAI key returns
    ``info.error={"name":"APIError","data":{"message":"Incorrect API key ..."}}``.
    """
    info = message.get("info")
    if not isinstance(info, dict):
        return None
    err = info.get("error")
    if not err:
        return None
    if isinstance(err, dict):
        data = err.get("data")
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])
        if err.get("name"):
            return str(err["name"])
    return str(err)


def normalize_opencode_message(message: dict[str, Any]) -> list[StreamChunk]:
    """Map an opencode message reply (``{info, parts}``) to panel chunks.

    Unlike the Claude path (which streams text deltas live and so drops the final
    TextBlock to avoid double-render), the synchronous opencode reply carries the
    text only here, so text parts ARE emitted. A ``propose_draft`` tool part — or
    a fenced ```roboco-draft``` block in the assembled text — becomes a ``draft``
    chunk, matching the Claude intake's two draft paths. A turn-level
    ``info.error`` is surfaced as an ``error`` chunk so a failed turn is never
    silently blank.
    """
    parts = message.get("parts") or []
    chunks: list[StreamChunk] = []
    text_parts: list[str] = []
    draft: dict[str, Any] | None = None
    for part in parts:
        chunk, text_part, block_draft = _part_to_chunk(part)
        if chunk is not None:
            chunks.append(chunk)
        if text_part is not None:
            text_parts.append(text_part)
            chunks.append(StreamChunk(kind="text", text=text_part))
        draft = draft or block_draft
    draft = draft or _extract_draft("".join(text_parts))
    if draft is not None:
        chunks.append(StreamChunk(kind="draft", data=draft))
    error = _message_error(message)
    if error:
        chunks.append(StreamChunk(kind="error", text=error))
    chunks.append(StreamChunk(kind="turn_end", data={}))
    return chunks


class OpencodeServeSession:
    """``IntakeSession`` backed by a long-lived ``opencode serve`` process.

    Async context manager: ``__aenter__`` launches ``opencode serve`` and opens
    one session; ``__aexit__`` tears the server down. ``send`` runs one turn via
    the synchronous message endpoint and yields normalized chunks. The opencode
    session id is reused across turns so conversation context persists in the
    server (the held-open analogue of the Claude SDK client).
    """

    def __init__(
        self,
        *,
        port: int = _DEFAULT_PORT,
        cwd: str | None = None,
    ) -> None:
        self._port = port
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None

    @property
    def _base(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def __aenter__(self) -> OpencodeServeSession:
        self._proc = await asyncio.create_subprocess_exec(
            "opencode",
            "serve",
            "--port",
            str(self._port),
            "--hostname",
            "127.0.0.1",
            cwd=self._cwd,
        )
        # A generous per-turn timeout (grok-build-0.1 reasons before replying);
        # a short connect timeout so readiness polling fails fast and retries.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0))
        self._session_id = await self._open_session()
        logger.info("opencode serve session opened", session=self._session_id)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._proc.wait(), timeout=10.0)

    async def _open_session(self) -> str:
        """Create an opencode session, retrying until the server is ready."""
        assert self._client is not None
        last_error: str = "no response"
        attempts = max(1, int(_READY_TIMEOUT_S / _READY_INTERVAL_S))
        for _ in range(attempts):
            try:
                resp = await self._client.post(f"{self._base}/session", json={})
                if resp.is_success:
                    sid = _extract_session_id(resp.json())
                    if sid:
                        return sid
                last_error = f"HTTP {resp.status_code}"
            except Exception as exc:  # server not up yet / transient
                last_error = str(exc)
            await asyncio.sleep(_READY_INTERVAL_S)
        raise RuntimeError(f"opencode serve never became ready: {last_error}")

    async def send(self, text: str) -> AsyncIterator[StreamChunk]:
        """Run one turn (synchronous message) and yield its normalized chunks."""
        if self._client is None or self._session_id is None:
            raise RuntimeError("OpencodeServeSession used outside its context")
        body: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        # Per-role reasoning effort: the orchestrator sets ROBOCO_GROK_VARIANT on
        # the container; the serve message endpoint accepts a `variant` field
        # (confirmed against the live opencode OpenAPI), the same lever the
        # one-shot path drives via `opencode run --variant`.
        variant = _variant()
        if variant:
            body["variant"] = variant
        try:
            resp = await self._client.post(
                f"{self._base}/session/{self._session_id}/message",
                json=body,
            )
            resp.raise_for_status()
            message = resp.json()
        except Exception as exc:
            logger.error("opencode message turn failed", error=str(exc))
            yield StreamChunk(kind="error", text=str(exc))
            return
        for chunk in normalize_opencode_message(message):
            yield chunk


def _extract_session_id(payload: Any) -> str | None:
    """Pull the session id out of opencode's POST /session response, tolerantly."""
    if not isinstance(payload, dict):
        return None
    for key in ("id", "sessionID", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    info = payload.get("info")
    if isinstance(info, dict):
        ident = info.get("id")
        if isinstance(ident, str) and ident:
            return ident
    return None


def serve_port() -> int:
    """The opencode serve port (override with ROBOCO_OPENCODE_SERVE_PORT)."""
    raw = os.environ.get("ROBOCO_OPENCODE_SERVE_PORT", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_PORT


def _variant() -> str | None:
    """The opencode reasoning variant to apply per turn (ROBOCO_GROK_VARIANT).

    Set by the orchestrator from the per-role reasoning-effort policy (the same
    value the one-shot path passes to ``opencode run --variant``); unset = the
    model's default (full) reasoning.
    """
    raw = os.environ.get("ROBOCO_GROK_VARIANT", "").strip()
    return raw or None
