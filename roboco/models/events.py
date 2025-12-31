"""
Event Models

Domain types for the event bus system.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from roboco.runtime.orchestrator import WaitingRecord


class EventType(str, Enum):
    """Types of events in the system."""

    # Task lifecycle events
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = (
        "task.assigned"  # A2A: task assigned to agent, triggers spawn/notify
    )
    TASK_CLAIMED = "task.claimed"
    TASK_STARTED = "task.started"
    TASK_BLOCKED = "task.blocked"
    TASK_UNBLOCKED = "task.unblocked"
    TASK_PAUSED = "task.paused"
    TASK_RESUMED = "task.resumed"
    TASK_VERIFYING = "task.verifying"
    TASK_AWAITING_QA = "task.awaiting_qa"
    TASK_QA_PASSED = "task.qa_passed"
    TASK_QA_FAILED = "task.qa_failed"
    TASK_AWAITING_DOCS = "task.awaiting_docs"
    TASK_ESCALATED_TO_MAIN_PM = "task.escalated_to_main_pm"  # Cell PM → Main PM
    TASK_AWAITING_CEO_APPROVAL = "task.awaiting_ceo_approval"  # Escalated to CEO
    TASK_CEO_APPROVED = "task.ceo_approved"  # CEO approved
    TASK_CEO_REJECTED = "task.ceo_rejected"  # CEO rejected, needs revision
    TASK_COMPLETED = "task.completed"
    TASK_CANCELLED = "task.cancelled"

    # Session events
    SESSION_CREATED = "session.created"
    SESSION_CLOSED = "session.closed"
    SESSION_TIMEOUT = "session.timeout"

    # Handoff events
    HANDOFF_CREATED = "handoff.created"
    HANDOFF_ACCEPTED = "handoff.accepted"

    # Agent events
    AGENT_SPAWNED = "agent.spawned"
    AGENT_STOPPED = "agent.stopped"
    AGENT_WAITING = "agent.waiting"
    AGENT_RESUMED = "agent.resumed"
    AGENT_ERROR = "agent.error"

    # Notification events
    NOTIFICATION_SENT = "notification.sent"
    NOTIFICATION_ACKED = "notification.acked"

    # Blocker events
    BLOCKER_REPORTED = "blocker.reported"
    BLOCKER_RESOLVED = "blocker.resolved"

    # Question events
    QUESTION_ASKED = "question.asked"
    QUESTION_ANSWERED = "question.answered"


@dataclass
class Event:
    """An event in the system."""

    type: EventType
    data: dict[str, Any]
    id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    source_agent: str | None = None
    correlation_id: str | None = None  # For tracking related events

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(
            {
                "id": str(self.id),
                "type": self.type.value,
                "data": self.data,
                "timestamp": self.timestamp.isoformat(),
                "source_agent": self.source_agent,
                "correlation_id": self.correlation_id,
            }
        )

    @classmethod
    def from_json(cls, json_str: str) -> "Event":
        """Deserialize from JSON."""
        data = json.loads(json_str)
        return cls(
            id=UUID(data["id"]),
            type=EventType(data["type"]),
            data=data["data"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source_agent=data.get("source_agent"),
            correlation_id=data.get("correlation_id"),
        )


# =============================================================================
# SERVICE PROTOCOLS (for dependency injection)
# =============================================================================


class NotificationServiceProtocol(Protocol):
    """Protocol for notification service."""

    async def send_blocker_notification(
        self,
        task_id: str,
        blocker_reason: str,
        from_agent: str | None,
        to_pm: str,
    ) -> None: ...

    async def send_qa_ready_notification(
        self,
        task_id: str,
        from_agent: str | None,
        to_qa: str,
    ) -> None: ...

    async def send_qa_failed_notification(
        self,
        task_id: str,
        qa_notes: str,
        to_developer: str,
    ) -> None: ...

    async def send_docs_ready_notification(
        self,
        task_id: str,
        from_agent: str | None,
        to_documenter: str,
    ) -> None: ...

    async def send_handoff_notification(
        self,
        task_id: str,
        handoff_id: str,
        from_agent: str | None,
        to_documenter: str,
    ) -> None: ...


class OrchestratorAccessProtocol(Protocol):
    """Protocol for orchestrator access."""

    def get_waiting_agents(self) -> dict[str, "WaitingRecord"]: ...

    def get_running_agents(self) -> set[str]: ...

    async def resolve_wait(self, agent_id: str, resolution: dict[str, Any]) -> Any: ...

    async def spawn_agent(
        self,
        agent_id: str,
        initial_prompt: str | None = None,
    ) -> Any: ...


# =============================================================================
# EVENT CONTEXT (dependency container)
# =============================================================================


@dataclass
class EventContext:
    """
    Dependency container for event handlers.

    Set once during application initialization, then used by all handlers.
    This avoids runtime imports inside handler functions.
    """

    notification_service: NotificationServiceProtocol | None = None
    orchestrator: OrchestratorAccessProtocol | None = None
