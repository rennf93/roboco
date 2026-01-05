"""
A2A (Agent-to-Agent) Protocol Models

Implements Google's A2A protocol for agent interoperability.
See: https://a2a-protocol.org/latest/specification/

This module defines the data structures for:
- AgentCard: Agent metadata and capability discovery
- Task: Work unit lifecycle management
- Message: Communication between agents
- Skill: Capability units an agent can perform
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import ConfigDict, Field

from roboco.models.base import RobocoBase

# =============================================================================
# ENUMS
# =============================================================================


class A2ATaskState(str, Enum):
    """
    A2A Task lifecycle states.

    Per A2A specification section 4.2.1.
    """

    SUBMITTED = "submitted"  # Task acknowledged and created
    WORKING = "working"  # Actively processing
    COMPLETED = "completed"  # Finished successfully (terminal)
    FAILED = "failed"  # Finished with error (terminal)
    CANCELLED = "cancelled"  # Stopped before completion (terminal)
    INPUT_REQUIRED = "input_required"  # Awaiting additional information
    REJECTED = "rejected"  # Agent declined the task (terminal)
    AUTH_REQUIRED = "auth_required"  # Needs client authentication


class A2APartType(str, Enum):
    """Types of content parts in a message."""

    TEXT = "text"
    FILE = "file"
    DATA = "data"
    ARTIFACT = "artifact"


# =============================================================================
# AGENT CARD MODELS
# =============================================================================


class AgentProvider(RobocoBase):
    """Provider information for an agent."""

    organization: str = Field(..., description="Organization name")
    url: str | None = Field(default=None, description="Organization URL")


class AgentCapabilities(RobocoBase):
    """Capabilities supported by an agent."""

    streaming: bool = Field(default=False, description="Supports SSE streaming")
    push_notifications: bool = Field(
        default=False, description="Supports webhook push notifications"
    )
    state_transition_history: bool = Field(
        default=False, description="Returns task state history"
    )


class SecurityScheme(RobocoBase):
    """Security scheme for authentication."""

    type: str = Field(..., description="Scheme type (apiKey, http, oauth2)")
    name: str | None = Field(default=None, description="Name of the header/param")
    scheme: str | None = Field(default=None, description="HTTP auth scheme (bearer)")
    bearer_format: str | None = Field(
        default=None, alias="bearerFormat", description="Format hint for tokens"
    )
    in_location: str | None = Field(
        default=None, alias="in", description="Where to send (header, query, cookie)"
    )


class AgentSkill(RobocoBase):
    """A capability unit an agent can perform."""

    id: str = Field(..., description="Unique skill identifier")
    name: str = Field(..., description="Human-readable skill name")
    description: str = Field(..., description="What this skill does")
    tags: list[str] = Field(default_factory=list, description="Capability categories")
    examples: list[str] = Field(default_factory=list, description="Example invocations")
    input_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        alias="inputModes",
        description="Supported input MIME types",
    )
    output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        alias="outputModes",
        description="Supported output MIME types",
    )


class AgentCard(RobocoBase):
    """
    A2A Agent Card - The agent's public identity and capabilities.

    Published at /.well-known/agent.json per A2A specification.
    Acts as the agent's "business card" for discovery.
    """

    # Required fields
    id: str = Field(..., description="Unique agent identifier")
    name: str = Field(..., description="Human-readable agent name")
    provider: AgentProvider = Field(..., description="Provider information")
    protocol_version: str = Field(
        default="1.0",
        alias="protocolVersion",
        description="Supported A2A protocol version",
    )
    service_endpoint: str = Field(
        ..., alias="serviceEndpoint", description="Base URL for A2A operations"
    )
    capabilities: AgentCapabilities = Field(
        default_factory=AgentCapabilities, description="Supported features"
    )
    security_schemes: dict[str, SecurityScheme] = Field(
        default_factory=dict,
        alias="securitySchemes",
        description="Available auth methods",
    )
    security: list[dict[str, list[str]]] = Field(
        default_factory=list, description="Required security scheme(s)"
    )

    # Optional fields
    description: str | None = Field(
        default=None, description="Agent purpose and capabilities"
    )
    skills: list[AgentSkill] = Field(
        default_factory=list, description="Available operations"
    )
    default_input_modes: list[str] = Field(
        default_factory=lambda: ["text/plain", "application/json"],
        alias="defaultInputModes",
        description="Default accepted input MIME types",
    )
    default_output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain", "application/json"],
        alias="defaultOutputModes",
        description="Default output MIME types",
    )
    documentation_url: str | None = Field(
        default=None, alias="documentationUrl", description="Agent documentation URL"
    )
    version: str | None = Field(default=None, description="Agent version")
    supports_extended_agent_card: bool = Field(
        default=False,
        alias="supportsExtendedAgentCard",
        description="Whether authenticated card available",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Custom metadata"
    )

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",  # A2A cards may have extensions
    )


# =============================================================================
# MESSAGE MODELS
# =============================================================================


class TextPart(RobocoBase):
    """Plain text content part."""

    type: Literal["text"] = "text"
    text: str = Field(..., description="Text content")


class FilePart(RobocoBase):
    """File reference content part."""

    type: Literal["file"] = "file"
    file: dict[str, Any] = Field(
        ..., description="File data (uri, mimeType, name, data)"
    )


class DataPart(RobocoBase):
    """Structured JSON data content part."""

    type: Literal["data"] = "data"
    data: dict[str, Any] = Field(..., description="Structured data")


class ArtifactPart(RobocoBase):
    """Reference to a generated artifact."""

    type: Literal["artifact"] = "artifact"
    artifact: dict[str, Any] = Field(..., description="Artifact reference")


# Union type for message parts
Part = Annotated[
    TextPart | FilePart | DataPart | ArtifactPart, Field(discriminator="type")
]


class A2AMessage(RobocoBase):
    """
    A2A Message - A communication turn between agents.

    Contains one or more parts with content.
    """

    role: Literal["user", "agent"] = Field(..., description="Message sender role")
    parts: list[Part] = Field(..., description="Content parts")
    context_id: str | None = Field(
        default=None, alias="contextId", description="Conversation grouping"
    )
    task_id: str | None = Field(
        default=None, alias="taskId", description="Associated task"
    )
    message_id: str = Field(
        default_factory=lambda: str(uuid4()),
        alias="messageId",
        description="Unique message ID",
    )

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# TASK MODELS
# =============================================================================


class A2ATaskStatus(RobocoBase):
    """Status container for an A2A task."""

    state: A2ATaskState = Field(..., description="Current lifecycle state")
    message: A2AMessage | None = Field(
        default=None, description="Associated status message"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When status was recorded",
    )


class A2AArtifact(RobocoBase):
    """An output artifact produced by a task."""

    id: str = Field(default_factory=lambda: str(uuid4()), description="Artifact ID")
    name: str = Field(..., description="Artifact name")
    parts: list[Part] = Field(..., description="Artifact content parts")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Custom metadata"
    )


class A2ATask(RobocoBase):
    """
    A2A Task - A unit of work with lifecycle management.

    Maps to RoboCo's internal TaskTable but follows A2A semantics.
    """

    id: str = Field(
        default_factory=lambda: str(uuid4()), description="Server-generated task ID"
    )
    context_id: str = Field(
        ..., alias="contextId", description="Groups related interactions"
    )
    status: A2ATaskStatus = Field(..., description="Current task status")
    artifacts: list[A2AArtifact] = Field(
        default_factory=list, description="Output artifacts"
    )
    history: list[A2AMessage] = Field(
        default_factory=list, description="Interaction history"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Custom metadata"
    )

    model_config = ConfigDict(populate_by_name=True)


# =============================================================================
# JSON-RPC REQUEST/RESPONSE MODELS
# =============================================================================


class SendMessageConfiguration(RobocoBase):
    """Configuration for SendMessage request."""

    accepted_output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain", "application/json"],
        alias="acceptedOutputModes",
        description="Client-accepted output MIME types",
    )
    history_length: int | None = Field(
        default=None,
        alias="historyLength",
        description="Number of history turns to include in response",
    )
    blocking: bool = Field(
        default=False, description="Wait for task completion before responding"
    )
    urgent: bool = Field(
        default=False,
        description="Priority request - interrupts busy agents, goes to front of queue",
    )
    push_notification_config: dict[str, Any] | None = Field(
        default=None,
        alias="pushNotificationConfig",
        description="Webhook config for async updates",
    )

    model_config = ConfigDict(populate_by_name=True)


class SendMessageRequest(RobocoBase):
    """Request payload for SendMessage JSON-RPC method."""

    message: A2AMessage = Field(..., description="Message to send")
    configuration: SendMessageConfiguration | None = Field(
        default=None, description="Request configuration"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Custom request metadata"
    )


class SendMessageResponse(RobocoBase):
    """Response payload for SendMessage JSON-RPC method."""

    task: A2ATask = Field(..., description="Created or updated task")


class GetTaskRequest(RobocoBase):
    """Request payload for GetTask JSON-RPC method."""

    name: str = Field(..., description="Task resource name (tasks/{id})")
    history_length: int | None = Field(
        default=None,
        alias="historyLength",
        description="Number of history turns to include",
    )

    model_config = ConfigDict(populate_by_name=True)


class ListTasksRequest(RobocoBase):
    """Request payload for ListTasks JSON-RPC method."""

    page_size: int = Field(
        default=20, alias="pageSize", ge=1, le=100, description="Results per page"
    )
    page_token: str | None = Field(
        default=None, alias="pageToken", description="Pagination token"
    )
    filter: str | None = Field(default=None, description="Filter expression")
    order_by: str | None = Field(
        default=None, alias="orderBy", description="Sort order"
    )

    model_config = ConfigDict(populate_by_name=True)


class ListTasksResponse(RobocoBase):
    """Response payload for ListTasks JSON-RPC method."""

    tasks: list[A2ATask] = Field(..., description="Task list")
    next_page_token: str | None = Field(
        default=None, alias="nextPageToken", description="Token for next page"
    )

    model_config = ConfigDict(populate_by_name=True)


class CancelTaskRequest(RobocoBase):
    """Request payload for CancelTask JSON-RPC method."""

    name: str = Field(..., description="Task resource name (tasks/{id})")
    reason: str | None = Field(default=None, description="Cancellation reason")


# =============================================================================
# STATE MAPPING UTILITIES
# =============================================================================


def task_status_to_a2a_state(roboco_status: str) -> A2ATaskState:
    """
    Map RoboCo TaskStatus to A2A TaskState.

    This enables interoperability between RoboCo's internal
    task lifecycle and the A2A protocol.
    """
    mapping = {
        "backlog": A2ATaskState.SUBMITTED,
        "pending": A2ATaskState.SUBMITTED,
        "claimed": A2ATaskState.WORKING,
        "in_progress": A2ATaskState.WORKING,
        "blocked": A2ATaskState.INPUT_REQUIRED,
        "paused": A2ATaskState.INPUT_REQUIRED,
        "verifying": A2ATaskState.WORKING,
        "needs_revision": A2ATaskState.INPUT_REQUIRED,
        "awaiting_qa": A2ATaskState.WORKING,
        "awaiting_documentation": A2ATaskState.WORKING,
        "awaiting_pm_review": A2ATaskState.WORKING,
        "awaiting_ceo_approval": A2ATaskState.INPUT_REQUIRED,  # Awaiting CEO decision
        "completed": A2ATaskState.COMPLETED,
        "cancelled": A2ATaskState.CANCELLED,
    }
    return mapping.get(roboco_status, A2ATaskState.WORKING)


def a2a_state_to_task_status(a2a_state: A2ATaskState) -> str:
    """
    Map A2A TaskState back to RoboCo TaskStatus.

    Used when creating tasks via A2A protocol.
    """
    mapping = {
        A2ATaskState.SUBMITTED: "pending",
        A2ATaskState.WORKING: "in_progress",
        A2ATaskState.COMPLETED: "completed",
        A2ATaskState.FAILED: "cancelled",  # RoboCo uses cancelled for failures
        A2ATaskState.CANCELLED: "cancelled",
        A2ATaskState.INPUT_REQUIRED: "blocked",
        A2ATaskState.REJECTED: "cancelled",
        A2ATaskState.AUTH_REQUIRED: "blocked",
    }
    return mapping.get(a2a_state, "pending")
