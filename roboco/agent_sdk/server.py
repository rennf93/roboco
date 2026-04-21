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

import os
from collections import deque

import httpx
import structlog
import uvicorn
from fastapi import FastAPI
from fastapi import status as http_status

from roboco.agent_sdk.models import (
    A2AMessage,
    HealthResponse,
    InboxResponse,
    MessagePriority,
    SendRequest,
    SendResponse,
)

logger = structlog.get_logger()

# Environment configuration
AGENT_ID = os.environ.get("ROBOCO_AGENT_ID", "unknown")
MAIN_API_URL = os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000")
SDK_PORT = int(os.environ.get("ROBOCO_SDK_PORT", "9000"))

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
                f"{MAIN_API_URL}/api/v1/a2a/chat/conversations",
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
                f"{MAIN_API_URL}/api/v1/a2a/message/send",
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
                    f"{MAIN_API_URL}/api/v1/journals/me/entries",
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
# MAIN
# =============================================================================

if __name__ == "__main__":
    logger.info(
        "Starting SDK Server",
        agent_id=AGENT_ID,
        port=SDK_PORT,
    )
    uvicorn.run(app, host="0.0.0.0", port=SDK_PORT)
