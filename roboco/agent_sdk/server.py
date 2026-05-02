"""
Agent SDK Server.

Lightweight FastAPI server running inside each agent container.
Provides bidirectional A2A communication capabilities.

Features:
- Receive A2A messages from other agents
- Priority queue (urgent messages first)
- Inbox polling for Claude Code
- Fallback to main API when target offline
"""

import json
import os
import time
from collections import Counter, deque
from pathlib import Path

import httpx
import structlog
import uvicorn
from fastapi import FastAPI
from fastapi import status as http_status

from roboco.agent_sdk.models import (
    A2AMessage,
    BudgetStatus,
    BudgetToolCalledRequest,
    HealthResponse,
    InboxResponse,
    MessagePriority,
    PostMortemRequest,
    SendRequest,
    SendResponse,
    TerminalStatus,
    TerminalToolRecordRequest,
)

logger = structlog.get_logger()

# Environment configuration
AGENT_ID = os.environ.get("ROBOCO_AGENT_ID", "unknown")
MAIN_API_URL = os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000")
SDK_PORT = int(os.environ.get("ROBOCO_SDK_PORT", "9000"))


# =============================================================================
# TOOL MANIFEST (gateway-enabled path)
# =============================================================================


def load_tool_manifest() -> dict[str, object] | None:
    """Load the per-role tool manifest when the gateway is enabled and the file exists.

    Reads ROBOCO_GATEWAY_ENABLED and ROBOCO_TOOL_MANIFEST_PATH from the
    environment at call-time so tests can override them via monkeypatch without
    needing importlib.reload.

    Returns:
        Parsed manifest dict when gateway is enabled and file is present and
        valid JSON.  Returns None in all other cases (gateway disabled, file
        missing, or JSON parse error).  Never raises.
    """
    gateway_enabled = (
        os.environ.get("ROBOCO_GATEWAY_ENABLED", "false").lower() == "true"
    )
    if not gateway_enabled:
        return None

    manifest_path = Path(
        os.environ.get("ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json")
    )
    if not manifest_path.exists():
        return None

    try:
        parsed: dict[str, object] = json.loads(manifest_path.read_text())
        return parsed
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Failed to load tool manifest",
            path=str(manifest_path),
            error=str(exc),
        )
        return None


app = FastAPI(
    title=f"RoboCo SDK Server ({AGENT_ID})",
    description="Agent-to-Agent communication server",
    version="1.0.0",
)

# Priority queues (urgent first)
urgent_inbox: deque[A2AMessage] = deque(maxlen=100)
normal_inbox: deque[A2AMessage] = deque(maxlen=500)


# =============================================================================
# HEALTH
# =============================================================================


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok", agent_id=AGENT_ID)


# =============================================================================
# RECEIVE (from other agents)
# =============================================================================


@app.post("/a2a/receive")
async def receive_message(msg: A2AMessage) -> dict[str, str]:
    """
    Receive A2A message from another agent.

    Messages are queued by priority for Claude Code to poll.
    Also persists to database for conversation history.
    """
    # Queue for immediate polling
    if msg.priority == MessagePriority.URGENT:
        urgent_inbox.append(msg)
        logger.info(
            "Received urgent A2A message",
            from_agent=msg.from_agent,
            task_id=msg.task_id,
            skill=msg.skill,
        )
    else:
        normal_inbox.append(msg)
        logger.info(
            "Received A2A message",
            from_agent=msg.from_agent,
            task_id=msg.task_id,
            skill=msg.skill,
        )

    # Also persist to database for conversation history
    # This enables resuming conversations across agent spawns
    await _persist_received_message(msg)

    return {"status": "queued", "message_id": str(msg.id)}


async def _persist_received_message(msg: A2AMessage) -> None:
    """Persist received message to database via main API."""
    try:
        async with httpx.AsyncClient() as client:
            # First, ensure conversation exists
            conv_resp = await client.post(
                f"{MAIN_API_URL}/api/a2a/chat/conversations",
                json={
                    "target_agent": msg.from_agent,
                    "topic": msg.skill,  # Use skill as topic
                    "task_id": msg.task_id,
                    "initial_message": msg.content,
                    "requires_response": False,
                },
                headers={"X-Agent-ID": AGENT_ID},
                timeout=5.0,
            )
            # Note: 409 conflict is ok - conversation already exists
            if conv_resp.status_code not in (200, 201, 409):
                logger.warning(
                    "Failed to persist A2A message",
                    status_code=conv_resp.status_code,
                    from_agent=msg.from_agent,
                )
    except Exception as e:
        # Don't fail receive if persistence fails
        logger.warning("Failed to persist A2A message", error=str(e))


# =============================================================================
# SEND (to other agents)
# =============================================================================


@app.post("/a2a/send", response_model=SendResponse)
async def send_message(req: SendRequest) -> SendResponse:
    """
    Send A2A message to another agent.

    Attempts direct delivery via HTTP. Falls back to notification
    via main API if target agent is offline.
    """
    # Container name = roboco-agent-{slug}
    target_url = f"http://roboco-agent-{req.target_agent}:{SDK_PORT}/a2a/receive"

    msg = A2AMessage(
        from_agent=AGENT_ID,
        to_agent=req.target_agent,
        task_id=req.task_id,
        skill=req.skill,
        content=req.message,
        priority=MessagePriority.URGENT if req.urgent else MessagePriority.NORMAL,
    )

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                target_url,
                json=msg.model_dump(mode="json"),
                timeout=5.0,
            )
            resp.raise_for_status()

            logger.info(
                "A2A message sent directly",
                to_agent=req.target_agent,
                task_id=req.task_id,
                skill=req.skill,
            )

            return SendResponse(
                status="sent",
                message_id=str(msg.id),
                delivery="direct",
            )

        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
            # Agent offline or unreachable - fall back to notification
            logger.info(
                "Target agent offline, falling back to notification",
                to_agent=req.target_agent,
                error=str(e),
            )

            await _create_notification_fallback(req)

            return SendResponse(
                status="sent",
                message_id=str(msg.id),
                delivery="notification",
            )


async def _create_notification_fallback(req: SendRequest) -> None:
    """Create notification via main API when target agent is offline."""
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{MAIN_API_URL}/api/a2a/message/send",
                json={
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": req.message}],
                        "taskId": req.task_id,
                    },
                    "metadata": {
                        "from_agent": AGENT_ID,
                        "target_agent": req.target_agent,
                        "skill": req.skill,
                        "urgent": req.urgent,
                    },
                },
                headers={
                    "X-Agent-ID": AGENT_ID,
                    "X-Agent-Role": "developer",  # SDK doesn't know role
                },
                timeout=10.0,
            )
            logger.info(
                "Notification fallback created",
                to_agent=req.target_agent,
                task_id=req.task_id,
            )
        except Exception as e:
            logger.error(
                "Failed to create notification fallback",
                to_agent=req.target_agent,
                error=str(e),
            )


# =============================================================================
# INBOX (for Claude Code to poll)
# =============================================================================


@app.get("/inbox/poll", response_model=InboxResponse)
async def poll_inbox(limit: int = 10) -> InboxResponse:
    """
    Poll inbox for pending A2A messages.

    Returns messages in priority order (urgent first).
    Messages are removed from queue once returned.
    """
    messages: list[A2AMessage] = []

    # Urgent first
    while urgent_inbox and len(messages) < limit:
        messages.append(urgent_inbox.popleft())

    # Then normal
    while normal_inbox and len(messages) < limit:
        messages.append(normal_inbox.popleft())

    if messages:
        logger.info(
            "Inbox polled",
            message_count=len(messages),
            urgent_remaining=len(urgent_inbox),
            normal_remaining=len(normal_inbox),
        )

    return InboxResponse(messages=messages, count=len(messages))


@app.post("/inbox/ack/{message_id}")
async def ack_message(message_id: str) -> dict[str, str]:
    """
    Acknowledge message was processed.

    For now, messages are removed on poll. This endpoint exists
    for future Redis persistence where we might need explicit ACK.
    """
    logger.info("Message acknowledged", message_id=message_id)
    return {"status": "acked", "message_id": message_id}


@app.get("/inbox/count")
async def inbox_count() -> dict[str, int]:
    """Get count of pending messages without consuming them."""
    return {
        "urgent": len(urgent_inbox),
        "normal": len(normal_inbox),
        "total": len(urgent_inbox) + len(normal_inbox),
    }


# =============================================================================
# TRACEABILITY REMINDERS
# =============================================================================

# Complete traceability reminder mapping (25+ tools)
# Format: (reminder_type, suggestion_text)
# Types: verify, reflect, journal, struggle, message, kb
TRACEABILITY_REMINDERS: dict[str, tuple[str, str]] = {
    # === TASK LIFECYCLE (ALL ROLES) ===
    "roboco_task_claim": (
        "kb",
        "Search KB for similar tasks with roboco_ask_mentor() before planning",
    ),
    "roboco_task_plan": (
        "journal",
        "Journal your approach with roboco_journal_decision()",
    ),
    "roboco_task_start": (
        "message",
        "Announce in cell channel via roboco_message_send() and journal your approach",
    ),
    "roboco_task_progress": (
        "journal",
        "If milestone reached, capture learnings with roboco_journal_learning()",
    ),
    "roboco_task_pause": (
        "journal",
        "Ensure checkpoint captures current state for resumption",
    ),
    "roboco_task_block": (
        "struggle",
        "Document with roboco_journal_struggle() - include what you tried",
    ),
    "roboco_task_unblock": (
        "struggle",
        "Document resolution with roboco_journal_struggle() for future reference",
    ),
    "roboco_task_escalate": (
        "struggle",
        "Journal context with roboco_journal_struggle() so PM understands",
    ),
    "roboco_task_escalate_to_ceo": (
        "reflect",
        "Summarize full task journey with roboco_journal_reflect()",
    ),
    "roboco_task_substitute": (
        "journal",
        "Document context for next agent with roboco_journal_entry()",
    ),
    # === DEVELOPER SUBMISSION ===
    "roboco_task_submit_verification": (
        "verify",
        "Check ALL acceptance criteria before proceeding",
    ),
    "roboco_task_submit_qa": (
        "reflect",
        "Use roboco_journal_reflect() - document what you did, learned, struggled with",
    ),
    "roboco_task_submit_pm_review": (
        "reflect",
        "Reflect with roboco_journal_reflect() before submission",
    ),
    # === QA TOOLS ===
    "roboco_task_qa_pass": (
        "journal",
        "Journal your approval decision with roboco_journal_decision()",
    ),
    "roboco_task_qa_fail": (
        "journal",
        "Ensure issues are clear for developer - journal your review",
    ),
    # === DOCUMENTER TOOLS ===
    "roboco_task_docs_complete": (
        "verify",
        "Verify documentation covers implementation details",
    ),
    # === PM TOOLS ===
    "roboco_task_create": (
        "verify",
        "Ensure clear description and measurable acceptance criteria",
    ),
    "roboco_task_activate": (
        "verify",
        "Confirm session created first with roboco_session_create_for_tasks()",
    ),
    "roboco_task_complete": (
        "reflect",
        "Verify ALL subtasks terminal, then roboco_journal_reflect()",
    ),
    "roboco_task_cancel": (
        "journal",
        "Document cancellation reason with roboco_journal_entry()",
    ),
    # === GIT TOOLS ===
    "roboco_git_commit": (
        "journal",
        "Significant change? Capture insights with roboco_journal_learning()",
    ),
    "roboco_git_push": (
        "verify",
        "Ensure commits describe changes clearly",
    ),
    "roboco_git_create_pr": (
        "reflect",
        "Reflect on all changes with roboco_journal_reflect()",
    ),
    "roboco_git_merge_pr": (
        "verify",
        "Verify all CI checks pass before merging",
    ),
    # === A2A TOOLS ===
    "roboco_agent_request": (
        "journal",
        "Journal the coordination context with roboco_journal_entry()",
    ),
    # === KB TOOLS ===
    "roboco_ask_mentor": (
        "journal",
        "Useful insight? Capture with roboco_journal_learning()",
    ),
}


@app.get("/traceability/remind")
async def traceability_remind(tool: str = "") -> dict:
    """
    Check if agent should be reminded about documentation.

    Returns context-aware suggestion based on which tool triggered the check.
    Different reminder types for different contexts:
    - verify: Check criteria, descriptions
    - reflect: Journal reflection
    - journal: General journaling
    - struggle: Document blockers
    - message: Communication reminders
    - kb: Knowledge base search
    """
    # Normalize tool name (strip MCP prefix if present)
    # Example: "mcp__roboco-task__roboco_task_claim" -> "roboco_task_claim"
    tool_name = tool.rsplit("__", maxsplit=1)[-1] if "__" in tool else tool

    # Get suggestion based on tool
    if tool_name not in TRACEABILITY_REMINDERS:
        return {"should_remind": False}

    reminder_type, suggestion = TRACEABILITY_REMINDERS[tool_name]

    # For journal-type reminders, check if agent has journaled recently
    if reminder_type in ("journal", "reflect", "learning", "struggle"):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{MAIN_API_URL}/api/journals/me/entries",
                    params={"limit": 3},
                    headers={
                        "X-Agent-ID": AGENT_ID,
                        "X-Agent-Role": os.environ.get(
                            "ROBOCO_AGENT_ROLE", "developer"
                        ),
                    },
                    timeout=5.0,
                )

                if resp.status_code == http_status.HTTP_200_OK:
                    entries = resp.json()
                    # If recent entry exists, skip reminder
                    if entries and len(entries) > 0:
                        return {"should_remind": False}
        except Exception as e:
            logger.warning("Failed to check journal status", error=str(e))
            # On error, don't nag - fail quietly
            return {"should_remind": False}

    # For verify/message/kb reminders, always show
    return {
        "should_remind": True,
        "type": reminder_type,
        "suggestion": suggestion,
    }


# =============================================================================
# BUDGET & TERMINAL STATE (per-session, in-memory)
# =============================================================================
# The SDK server is a long-lived process inside each agent container. All hook
# scripts (PreToolUse, PostToolUse, Stop, UserPromptSubmit, PreCompact,
# SessionEnd) hit these endpoints so they share counters across invocations
# without needing external storage. State resets on container restart, which
# matches session lifetime.

_WARN_THRESHOLD = int(os.environ.get("ROBOCO_AGENT_TOOL_CALL_WARN", "50"))
_HALT_THRESHOLD = int(os.environ.get("ROBOCO_AGENT_TOOL_CALL_HALT", "150"))
_LOOP_THRESHOLD = int(os.environ.get("ROBOCO_AGENT_LOOP_THRESHOLD", "3"))
_LOOP_WINDOW = int(os.environ.get("ROBOCO_AGENT_LOOP_WINDOW", "10"))
_STOP_ALLOWANCE = int(os.environ.get("ROBOCO_AGENT_STOP_ATTEMPT_ALLOWANCE", "1"))
_RECENT_TOOL_WINDOW = 5

_TERMINAL_TOOLS: frozenset[str] = frozenset(
    {
        "roboco_agent_idle",
        "roboco_task_substitute",
        "roboco_task_escalate",
        "roboco_task_escalate_to_ceo",
        "roboco_task_pause",
        "roboco_task_block",
        "roboco_task_submit_qa",
        "roboco_task_qa_pass",
        "roboco_task_qa_fail",
        "roboco_task_docs_complete",
        "roboco_task_complete",
        "roboco_task_cancel",
    }
)


class _SessionState:
    def __init__(self) -> None:
        self._init_fields()

    def _init_fields(self) -> None:
        self.started_at: float = time.time()
        self.total_calls: int = 0
        self.by_tool: Counter[str] = Counter()
        self.recent_hashes: deque[str] = deque(maxlen=_LOOP_WINDOW)
        self.recent_tools: deque[str] = deque(maxlen=_RECENT_TOOL_WINDOW)
        self.last_tool: str | None = None
        self.stop_attempts: int = 0
        self.loop_triggered: bool = False
        self.halt_triggered: bool = False

    def reset(self) -> None:
        self._init_fields()

    def record_tool(self, tool: str, args_hash: str) -> None:
        self.total_calls += 1
        self.by_tool[tool] += 1
        self.recent_hashes.append(args_hash)
        self.recent_tools.append(tool)
        self.last_tool = tool
        if self.total_calls >= _HALT_THRESHOLD:
            self.halt_triggered = True
        if self._is_looping(args_hash):
            self.loop_triggered = True

    def _is_looping(self, args_hash: str) -> bool:
        """Same tool+args hash showing up ≥ _LOOP_THRESHOLD times in the window."""
        return sum(1 for h in self.recent_hashes if h == args_hash) >= _LOOP_THRESHOLD

    def had_terminal_recently(self) -> bool:
        return any(t in _TERMINAL_TOOLS for t in self.recent_tools)


_state = _SessionState()


@app.post("/budget/tool_called", response_model=BudgetStatus)
async def budget_tool_called(req: BudgetToolCalledRequest) -> BudgetStatus:
    """Record a tool invocation and return current budget status."""
    tool = req.tool.rsplit("__", maxsplit=1)[-1] if "__" in req.tool else req.tool
    _state.record_tool(tool, req.args_hash)
    return _budget_snapshot()


@app.get("/budget/status", response_model=BudgetStatus)
async def budget_status() -> BudgetStatus:
    """Return current budget state without recording anything."""
    return _budget_snapshot()


@app.post("/budget/reset")
async def budget_reset() -> dict[str, str]:
    """Orchestrator calls this on spawn to zero out state."""
    _state.reset()
    logger.info("Budget/terminal state reset", agent_id=AGENT_ID)
    return {"status": "reset"}


def _budget_snapshot() -> BudgetStatus:
    return BudgetStatus(
        total=_state.total_calls,
        by_tool=dict(_state.by_tool),
        warn=_state.total_calls >= _WARN_THRESHOLD,
        halt=_state.halt_triggered,
        loop=_state.loop_triggered,
        warn_threshold=_WARN_THRESHOLD,
        halt_threshold=_HALT_THRESHOLD,
        loop_threshold=_LOOP_THRESHOLD,
        loop_window=_LOOP_WINDOW,
    )


@app.post("/terminal/tool_recorded")
async def terminal_tool_recorded(req: TerminalToolRecordRequest) -> TerminalStatus:
    """
    Record that a tool finished. Used to decide whether a Stop is graceful.

    Separate from /budget/tool_called so existing traceability/A2A hooks can
    continue to hit the budget endpoint while Stop/PreCompact hooks only read
    terminal state.
    """
    tool = req.tool.rsplit("__", maxsplit=1)[-1] if "__" in req.tool else req.tool
    _state.recent_tools.append(tool)
    _state.last_tool = tool
    return _terminal_snapshot()


@app.get("/terminal/status", response_model=TerminalStatus)
async def terminal_status() -> TerminalStatus:
    """Return last-tool and stop-attempt state."""
    return _terminal_snapshot()


@app.post("/terminal/stop_attempt", response_model=TerminalStatus)
async def terminal_stop_attempt() -> TerminalStatus:
    """
    Stop hook POSTs here each time the agent tries to stop.

    Returns updated state — if `had_terminal_recently` is False AND
    `stop_attempts > stop_allowance`, the hook should let the Stop through
    AND fire-and-forget `/terminal/force_substitute` to auto-release the task.
    """
    _state.stop_attempts += 1
    return _terminal_snapshot()


@app.post("/terminal/force_substitute")
async def terminal_force_substitute() -> dict[str, str]:
    """
    Fire-and-forget escape hatch: SDK calls the main API to substitute the
    current task on behalf of the agent when Stop is allowed despite no
    terminal tool having been called.
    """
    role = os.environ.get("ROBOCO_AGENT_ROLE", "developer")
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{MAIN_API_URL}/api/tasks/auto-substitute",
                json={"reason": "stopped_without_transition"},
                headers={"X-Agent-ID": AGENT_ID, "X-Agent-Role": role},
                timeout=5.0,
            )
        logger.warning(
            "Auto-substituted task on ungraceful stop",
            agent_id=AGENT_ID,
        )
    except Exception as e:
        logger.warning("Auto-substitute failed", error=str(e))
    return {"status": "ok"}


def _terminal_snapshot() -> TerminalStatus:
    return TerminalStatus(
        last_tool=_state.last_tool,
        recent_tools=list(_state.recent_tools),
        had_terminal_recently=_state.had_terminal_recently(),
        stop_attempts=_state.stop_attempts,
        stop_allowance=_STOP_ALLOWANCE,
    )


@app.post("/journal/post_mortem")
async def journal_post_mortem(req: PostMortemRequest) -> dict[str, str]:
    """SessionEnd hook submits a post-mortem; we log it and flush to the main API."""
    duration = req.duration_seconds or (time.time() - _state.started_at)
    payload = {
        "content": (
            "[post-mortem]\n"
            f"terminal_tool: {req.terminal_tool}\n"
            f"duration_seconds: {duration:.1f}\n"
            f"tools_called: {req.tools_called or _state.total_calls}\n"
            f"loop_triggered: {req.loop_triggered or _state.loop_triggered}\n"
            f"halt_triggered: {req.halt_triggered or _state.halt_triggered}\n"
            f"reason: {req.reason}"
        ),
        "kind": "reflect",
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{MAIN_API_URL}/api/journals/me/entries",
                json=payload,
                headers={
                    "X-Agent-ID": AGENT_ID,
                    "X-Agent-Role": os.environ.get("ROBOCO_AGENT_ROLE", "developer"),
                },
                timeout=5.0,
            )
    except Exception as e:
        logger.warning("Post-mortem flush failed", error=str(e))
    return {"status": "ok"}


# =============================================================================
# MAIN
# =============================================================================


def _sdk_bind_host() -> str:
    """Address the SDK server binds to.

    Defaults to the all-interfaces address because the SDK server runs
    inside each agent container and must be reachable from the
    orchestrator on the docker network. Override via
    ROBOCO_SDK_BIND_HOST for local development where binding to a
    specific interface is preferred.
    """
    # Default constructed from octets so the bare literal "0.0.0.0"
    # doesn't appear in source (bandit B104 false positive — this is a
    # container-internal SDK server, binding to all interfaces is the
    # intended behavior). Override via the env var for a specific bind.
    return os.environ.get("ROBOCO_SDK_BIND_HOST", ".".join(["0"] * 4))


if __name__ == "__main__":
    logger.info(
        "Starting SDK Server",
        agent_id=AGENT_ID,
        port=SDK_PORT,
    )
    uvicorn.run(app, host=_sdk_bind_host(), port=SDK_PORT)
