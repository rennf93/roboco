"""Intake agent driver — a long-lived Claude Code session the human chats with.

The intake (``prompter``) agent is not a one-shot ``claude -p`` like every other
RoboCo agent; it is an interactive session. This driver is the container's
entrypoint: it opens ONE ``claude-agent-sdk`` ``ClaudeSDKClient`` (Claude Code
held open), then loops — pull the human's next message, stream the agent's reply
(token deltas, tool calls), wait for the next message — keeping conversation
context in-process. The container stays alive for the whole chat and is reaped
when the draft becomes a task.

The SDK call surface is isolated in ``SdkIntakeSession`` (lazy import, so this
module imports without ``claude-agent-sdk`` installed). The loop
(``IntakeDriver``) and event normalization are SDK-free and unit-tested with
fakes.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

logger = structlog.get_logger()

# The intake agent emits the finished structured task draft as a fenced block
# (see the prompter system prompt). The driver mines it from the complete reply
# and surfaces it as one ``draft`` chunk for the panel's draft card.
_DRAFT_FENCE = re.compile(r"```roboco-draft\s*\n(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# Normalized stream chunk — what the panel SSE consumes. SDK-free.
# ---------------------------------------------------------------------------


@dataclass
class StreamChunk:
    """One normalized event in the agent's live reply.

    ``kind`` is the panel-facing event type; the rest is payload. Decoupled
    from the SDK's message classes so the relay/panel never import the SDK.
    """

    kind: str  # text|thinking|tool_use|tool_result|turn_end|system|draft|error
    text: str = ""
    tool: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def _extract_draft(text: str) -> dict[str, Any] | None:
    """Parse a fenced ``roboco-draft`` JSON block out of the agent's reply.

    Returns the parsed object (a dict with a string ``title``) or ``None`` when
    no well-formed draft block is present.
    """
    match = _DRAFT_FENCE.search(text)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("title"), str):
        return data
    return None


def _blocks_to_chunks(content: list[Any]) -> list[StreamChunk]:
    """Map an assistant message's content blocks to chunks (duck-typed).

    Text is deliberately NOT re-emitted here: with ``include_partial_messages``
    the live token deltas (``StreamEvent``) already streamed it, so re-emitting
    the complete ``TextBlock`` would render every reply twice on the panel.
    Instead the complete text is mined for a fenced ``roboco-draft`` block and
    surfaced as a single ``draft`` chunk; thinking + tool_use (which do NOT
    arrive as deltas) are emitted as before.
    """
    chunks: list[StreamChunk] = []
    text_parts: list[str] = []
    for block in content or []:
        if hasattr(block, "thinking"):  # ThinkingBlock
            chunks.append(StreamChunk(kind="thinking", text=str(block.thinking)))
        elif hasattr(block, "name") and hasattr(block, "input"):  # ToolUseBlock
            chunks.append(
                StreamChunk(
                    kind="tool_use",
                    tool=str(block.name),
                    data={"input": getattr(block, "input", {})},
                )
            )
        elif hasattr(block, "text"):  # TextBlock — already streamed; mine for a draft
            text_parts.append(str(block.text))
    draft = _extract_draft("".join(text_parts))
    if draft is not None:
        chunks.append(StreamChunk(kind="draft", data=draft))
    return chunks


def _stream_event_to_chunks(msg: Any) -> list[StreamChunk]:
    """Extract a live text delta from a partial StreamEvent (token streaming)."""
    event = getattr(msg, "event", None) or {}
    delta = event.get("delta") if isinstance(event, dict) else None
    if isinstance(delta, dict) and delta.get("type") == "text_delta":
        text = str(delta.get("text", ""))
        if text:
            return [StreamChunk(kind="text", text=text)]
    return []


def normalize(msg: Any) -> list[StreamChunk]:
    """Map a single ``claude-agent-sdk`` message to panel-facing chunks.

    Duck-typed on type name + attributes so it works on real SDK messages and
    on test fakes alike (no SDK import required).
    """
    name = type(msg).__name__
    if name == "StreamEvent":
        return _stream_event_to_chunks(msg)
    if name == "AssistantMessage":
        return _blocks_to_chunks(getattr(msg, "content", []))
    if name == "ResultMessage":
        return [
            StreamChunk(
                kind="turn_end",
                data={
                    "session_id": getattr(msg, "session_id", None),
                    "cost_usd": getattr(msg, "total_cost_usd", None),
                },
            )
        ]
    if name == "SystemMessage":
        return [
            StreamChunk(kind="system", data={"subtype": getattr(msg, "subtype", "")})
        ]
    return []


# ---------------------------------------------------------------------------
# Session seam — one conversational turn -> a stream of chunks.
# ---------------------------------------------------------------------------


class IntakeSession(Protocol):
    """A live agent session. ``send`` runs one turn and streams its chunks."""

    def send(self, text: str) -> AsyncIterator[StreamChunk]: ...


# A factory that yields an async-context-managed IntakeSession (opens/closes
# the underlying client). Injected so the driver loop is testable with a fake.
SessionFactory = Callable[[], "AbstractAsyncContextManager[IntakeSession]"]
# Source of the human's messages (e.g. the in-container inbox). Returns None to
# signal shutdown (container being reaped).
MessageSource = Callable[[], Awaitable[str | None]]
# Where normalized chunks go (the relay -> panel SSE).
EventSink = Callable[[StreamChunk], Awaitable[None]]


# ---------------------------------------------------------------------------
# The driver loop — SDK-free, unit-tested with fakes.
# ---------------------------------------------------------------------------


class IntakeDriver:
    """Owns the chat loop for the lifetime of one intake session."""

    def __init__(
        self,
        session_factory: SessionFactory,
        next_message: MessageSource,
        emit: EventSink,
    ) -> None:
        self._session_factory = session_factory
        self._next_message = next_message
        self._emit = emit
        self.log = logger.bind(component="intake_driver")

    async def run(self) -> None:
        """Open the session and process human turns until shutdown.

        One ``ClaudeSDKClient`` is held open across all turns (context persists
        in-process). The loop ends when ``next_message`` returns ``None``.
        """
        async with self._session_factory() as session:
            self.log.info("Intake session opened")
            turns = 0
            while True:
                text = await self._next_message()
                if text is None:
                    self.log.info("Intake session closing", turns=turns)
                    return
                turns += 1
                await self._run_turn(session, text)

    async def _run_turn(self, session: IntakeSession, text: str) -> None:
        """Stream one turn's chunks to the sink; a failure ends as an error chunk."""
        try:
            async for chunk in session.send(text):
                await self._emit(chunk)
        except Exception as exc:
            self.log.error("Intake turn failed", error=str(exc))
            await self._emit(StreamChunk(kind="error", text=str(exc)))


# ---------------------------------------------------------------------------
# SDK adapter — the only SDK-coupled code (lazy import). Verified against
# claude-agent-sdk; not exercised in the gate (needs the live claude binary).
# ---------------------------------------------------------------------------


def build_intake_options(
    *,
    system_prompt: str,
    cwd: str,
    allowed_tools: list[str],
    mcp_servers: dict[str, Any] | None = None,
    model: str | None = None,
) -> Any:  # pragma: no cover - thin SDK construction
    """Build ``ClaudeAgentOptions`` for the intake session (lazy SDK import)."""
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=cwd,
        allowed_tools=allowed_tools,
        mcp_servers=mcp_servers or {},
        model=model,
        include_partial_messages=True,  # live token streaming
        permission_mode="bypassPermissions",
    )


class SdkIntakeSession:  # pragma: no cover - requires the live claude binary
    """``IntakeSession`` backed by a real ``ClaudeSDKClient``.

    Async context manager: connects the client on enter, disconnects on exit.
    ``send`` runs one turn (query + receive_response) and yields normalized
    chunks. The conversation context lives in the client across turns.
    """

    def __init__(self, options: Any) -> None:
        self._options = options
        self._client: Any = None

    async def __aenter__(self) -> SdkIntakeSession:
        from claude_agent_sdk import ClaudeSDKClient

        self._client = ClaudeSDKClient(options=self._options)
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.disconnect()

    async def send(self, text: str) -> AsyncIterator[StreamChunk]:
        await self._client.query(text)
        async for msg in self._client.receive_response():
            for chunk in normalize(msg):
                yield chunk
