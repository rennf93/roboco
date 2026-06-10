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


def _coerce_draft(data: Any) -> dict[str, Any] | None:
    """Return ``data`` as a draft dict (with a string ``title``), else ``None``.

    Accepts a dict, or a JSON string the agent may have passed.
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return None
    if isinstance(data, dict) and isinstance(data.get("title"), str):
        return data
    return None


def _extract_draft(text: str) -> dict[str, Any] | None:
    """Parse a fenced ``roboco-draft`` JSON block out of the agent's reply.

    A fallback to the ``propose_draft`` tool: returns the parsed object (a dict
    with a string ``title``) or ``None`` when no well-formed block is present.
    """
    match = _DRAFT_FENCE.search(text)
    if match is None:
        return None
    return _coerce_draft(match.group(1))


def _draft_from_tool_input(tool_input: Any) -> dict[str, Any] | None:
    """Pull the draft out of a ``propose_draft`` tool call's input.

    Tolerant of both shapes the agent might use: the draft nested under a
    ``draft`` key, or the draft fields passed flat as the input itself.
    """
    if not isinstance(tool_input, dict):
        return None
    return _coerce_draft(tool_input.get("draft", tool_input))


def _is_propose_draft(name: str) -> bool:
    """True for the intake ``propose_draft`` tool, however the SDK namespaces it."""
    return name == "propose_draft" or name.endswith("__propose_draft")


def _block_to_chunk(
    block: Any,
) -> tuple[StreamChunk | None, str | None, dict[str, Any] | None]:
    """Classify one assistant content block → (chunk, text_part, draft).

    A ``propose_draft`` tool call yields a draft; thinking / other tool_use
    yield a chunk; a TextBlock yields a text_part (already streamed live, mined
    for a fenced draft by the caller). Unknown blocks yield nothing.
    """
    if hasattr(block, "thinking"):  # ThinkingBlock
        return StreamChunk(kind="thinking", text=str(block.thinking)), None, None
    if hasattr(block, "name") and hasattr(block, "input"):  # ToolUseBlock
        name = str(block.name)
        tool_input = getattr(block, "input", {})
        if _is_propose_draft(name):
            return None, None, _draft_from_tool_input(tool_input)
        return (
            StreamChunk(kind="tool_use", tool=name, data={"input": tool_input}),
            None,
            None,
        )
    if hasattr(block, "text"):  # TextBlock — already streamed; mine for a draft
        return None, str(block.text), None
    return None, None, None


def _blocks_to_chunks(content: list[Any]) -> list[StreamChunk]:
    """Map an assistant message's content blocks to chunks (duck-typed).

    Text is deliberately NOT re-emitted here: with ``include_partial_messages``
    the live token deltas (``StreamEvent``) already streamed it, so re-emitting
    the complete ``TextBlock`` would render every reply twice on the panel.

    The canonical draft signal is the agent calling the **``propose_draft``**
    tool — that ToolUseBlock becomes a single ``draft`` chunk. As a fallback (if
    the agent types the spec instead of calling the tool) the complete text is
    also mined for a fenced ``roboco-draft`` block. thinking + other tool_use
    (which do NOT arrive as deltas) are emitted as before.
    """
    chunks: list[StreamChunk] = []
    text_parts: list[str] = []
    draft: dict[str, Any] | None = None
    for block in content or []:
        chunk, text_part, block_draft = _block_to_chunk(block)
        if chunk is not None:
            chunks.append(chunk)
        if text_part is not None:
            text_parts.append(text_part)
        draft = draft or block_draft
    draft = draft or _extract_draft("".join(text_parts))
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
                self.log.info("Intake turn received", turn=turns, chars=len(text))
                await self._run_turn(session, text)

    async def _run_turn(self, session: IntakeSession, text: str) -> None:
        """Stream one turn's chunks to the sink, logging each tool call.

        The conversation streams to the relay (panel), not stdout — so without
        this, ``docker logs`` on the intake container is a black box between turn
        start and end even while the agent reads the codebase and spawns subagents.
        Logging each ``tool_use`` (and the draft) shows the turn's real shape;
        text deltas are intentionally NOT logged (they'd spam). A failure ends as
        an error chunk.
        """
        chunks = 0
        tools = 0
        drafted = False
        try:
            async for chunk in session.send(text):
                chunks += 1
                if chunk.kind == "tool_use":
                    tools += 1
                    self.log.info("Intake tool use", tool=chunk.tool)
                elif chunk.kind == "draft":
                    drafted = True
                    self.log.info("Intake draft emitted")
                await self._emit(chunk)
        except Exception as exc:
            self.log.error("Intake turn failed", error=str(exc), chunks=chunks)
            await self._emit(StreamChunk(kind="error", text=str(exc)))
        else:
            self.log.info(
                "Intake turn streamed", chunks=chunks, tools=tools, drafted=drafted
            )


# ---------------------------------------------------------------------------
# SDK adapter — the only SDK-coupled code (lazy import). Verified against
# claude-agent-sdk; not exercised in the gate (needs the live claude binary).
# ---------------------------------------------------------------------------


# The intake agent's hard tool allowlist: read-only built-ins + the draft tool.
_INTAKE_BASE_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob", "Task")


def build_intake_options(
    *,
    system_prompt: str,
    cwd: str,
    model: str | None = None,
) -> Any:  # pragma: no cover - thin SDK construction
    """Build locked-down ``ClaudeAgentOptions`` for the intake session.

    Isolation/security: the intake agent must NOT inherit the host's personal
    Claude Code env (Gmail/Notion MCP, Write/Edit/Bash). So:

    - ``strict_mcp_config=True`` + ``setting_sources=[]`` → ignore the host's
      ``~/.claude.json`` / ``settings.json``; use ONLY the MCP server below.
    - ``permission_mode="dontAsk"`` (NOT ``bypassPermissions``) + a ``can_use_tool``
      gate → a hard allowlist (Read/Grep/Glob/Task + ``propose_draft``), no prompts.

    Draft emission: the agent calls the ``propose_draft`` MCP tool, which the
    driver turns into a ``draft`` event — deterministic, not a fragile text fence.

    NOTE: ``setting_sources=[]`` must be validated against the mounted-``~/.claude``
    auth on the next smoke; if auth breaks, narrow it instead of removing it.
    """
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        PermissionResultAllow,
        PermissionResultDeny,
        create_sdk_mcp_server,
        tool,
    )

    @tool(
        "propose_draft",
        "Submit the finished task draft for the human to review and confirm. Call "
        "this once the spec is complete. Pass a JSON object: title, objective, "
        "what_this_builds[], the_work[] ({team, summary, items}), notes[], "
        "acceptance_criteria[], team, scale, task_type, nature, "
        "estimated_complexity, priority.",
        {"draft": dict},
    )
    async def _propose_draft(_args: dict[str, Any]) -> dict[str, Any]:
        # The driver intercepts this tool call (ToolUseBlock) and emits the draft
        # event; the handler only acknowledges so the agent knows it landed.
        return {
            "content": [
                {"type": "text", "text": "Draft submitted — the human can review it."}
            ]
        }

    server = create_sdk_mcp_server(
        name="intake", version="1.0.0", tools=[_propose_draft]
    )

    async def _gate(tool_name: str, _input: dict[str, Any], _ctx: Any) -> Any:
        if tool_name in _INTAKE_BASE_TOOLS or _is_propose_draft(tool_name):
            return PermissionResultAllow()
        # The intake's job is to ask questions, so it reaches for AskUserQuestion
        # by reflex. It isn't wired to the live chat panel (and isn't allowed), so
        # nudge it to just ask inline rather than leave it to stumble on a bare deny.
        if tool_name == "AskUserQuestion" or tool_name.endswith("AskUserQuestion"):
            return PermissionResultDeny(
                message=(
                    "AskUserQuestion isn't available here — just write your "
                    "questions as a normal chat message; the human reads every "
                    "reply live."
                )
            )
        # Plan mode is a Claude Code workflow the intake keeps slipping into; its
        # "plan" is the propose_draft draft, so steer it straight there.
        if tool_name == "ExitPlanMode" or tool_name.endswith("ExitPlanMode"):
            return PermissionResultDeny(
                message=(
                    "You don't use plan mode. When your spec is ready, call "
                    "propose_draft to produce the reviewable draft card — don't "
                    "announce a plan and wait."
                )
            )
        # Generic deny, but guiding: the agent reflexively probes Claude Code
        # built-ins (Write, ToolSearch, …). Tell it what it actually has.
        return PermissionResultDeny(
            message=(
                f"{tool_name} is not available to the intake agent. Your only tools "
                "are Read, Grep, Glob, Task, and propose_draft. Ask the human inline; "
                "when the spec is ready, call propose_draft."
            )
        )

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=cwd,
        mcp_servers={"intake": server},
        allowed_tools=[*_INTAKE_BASE_TOOLS, "mcp__intake__propose_draft"],
        model=model,
        include_partial_messages=True,  # live token streaming
        permission_mode="dontAsk",
        strict_mcp_config=True,
        setting_sources=[],
        can_use_tool=_gate,
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
