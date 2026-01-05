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
    """
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

    return {"status": "queued", "message_id": str(msg.id)}


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
# MAIN
# =============================================================================

if __name__ == "__main__":
    logger.info(
        "Starting SDK Server",
        agent_id=AGENT_ID,
        port=SDK_PORT,
    )
    uvicorn.run(app, host="0.0.0.0", port=SDK_PORT)
