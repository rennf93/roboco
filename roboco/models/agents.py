"""
Agent Runtime Models

Domain types for the agent runtime system including phases, configs, and contexts.
These models are used by the agent implementations (developer, qa, pm, etc.) at runtime.

For API/persistence models, see roboco.models.agent.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.agent import ModelConfig
from roboco.models.base import ModelProvider

# =============================================================================
# AGENT RUNTIME CONFIGURATION
# =============================================================================


class AgentConfig(BaseModel):
    """
    Runtime configuration for an agent instance.

    This is used by the agent runtime (base.py, developer.py, etc.) to configure
    agent behavior. It aligns with the Agent model in roboco.models.agent but is
    optimized for runtime use.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4)
    name: str
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$")
    role: AgentRole
    team: Team | None = None

    # Model configuration (aligned with agent.py ModelConfig)
    model_config_data: ModelConfig = Field(
        default_factory=ModelConfig,
        description="LLM model configuration",
    )

    # System prompt (loaded from blueprints)
    system_prompt: str

    # Capabilities
    capabilities: list[str] = Field(default_factory=list)

    # Permissions
    can_notify: bool = False
    channel_ids: list[UUID] = Field(default_factory=list)

    # Convenience properties for backward compatibility
    @property
    def provider(self) -> ModelProvider:
        """Get the model provider."""
        return self.model_config_data.provider

    @property
    def model(self) -> str:
        """Get the model name."""
        return self.model_config_data.name

    @property
    def temperature(self) -> float:
        """Get the temperature setting."""
        return self.model_config_data.temperature

    @property
    def max_tokens(self) -> int:
        """Get the max tokens setting."""
        return self.model_config_data.max_tokens


class AgentState(BaseModel):
    """Current state of an agent."""

    status: AgentStatus = AgentStatus.OFFLINE
    current_task_id: UUID | None = None
    current_session_id: UUID | None = None
    last_activity: datetime | None = None
    error: str | None = None

    # Metrics
    messages_sent: int = 0
    tasks_completed: int = 0


# =============================================================================
# DEVELOPER PHASES AND CONTEXT
# =============================================================================


class DevTaskPhase(StrEnum):
    """Phases of the developer task lifecycle."""

    SCAN = "scan"
    CLAIM = "claim"
    UNDERSTAND = "understand"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    NOTES = "notes"
    CLOSE = "close"
    BLOCKED = "blocked"


@dataclass
class TaskContext:
    """Context for the current task being worked on by a developer."""

    task_id: UUID
    title: str
    session_id: UUID | None = None  # Primary session for this task
    phase: DevTaskPhase = DevTaskPhase.CLAIM
    subtasks: list[dict[str, Any]] = field(default_factory=list)
    current_subtask: int = 0
    blockers: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    journal_entries: list[str] = field(default_factory=list)


# =============================================================================
# QA PHASES AND CONTEXT
# =============================================================================


class QATaskPhase(StrEnum):
    """Phases of the QA lifecycle."""

    MONITOR = "monitor"
    RECEIVE = "receive"
    UNDERSTAND = "understand"
    TEST = "test"
    VERDICT = "verdict"
    DOCUMENT = "document"
    RETURN = "return"


class TestResult(StrEnum):
    """Test result outcomes."""

    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


@dataclass
class TestCase:
    """A single test case."""

    name: str
    description: str
    steps: list[str]
    expected: str
    result: TestResult | None = None
    actual: str | None = None
    notes: str | None = None


@dataclass
class ReviewContext:
    """Context for the current review being conducted by QA."""

    task_id: UUID
    title: str
    session_id: UUID | None = None  # Primary session for this task
    phase: QATaskPhase = QATaskPhase.RECEIVE
    test_cases: list[TestCase] = field(default_factory=list)
    current_test: int = 0
    findings: list[str] = field(default_factory=list)
    verdict: TestResult | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    notes: list[str] = field(default_factory=list)


# =============================================================================
# PM PHASES AND CONTEXT
# =============================================================================


class CellPMPhase(StrEnum):
    """Phases of the Cell PM lifecycle."""

    MONITOR = "monitor"
    TRIAGE = "triage"
    ASSIGN = "assign"
    FACILITATE = "facilitate"
    ESCALATE = "escalate"
    TRACK = "track"
    REPORT = "report"


class MainPMPhase(StrEnum):
    """Phases of the Main PM lifecycle."""

    OVERSEE = "oversee"
    RECEIVE = "receive"
    PRIORITIZE = "prioritize"
    COORDINATE = "coordinate"
    DISTRIBUTE = "distribute"
    REPORT_UP = "report_up"
    FACILITATE = "facilitate"


@dataclass
class CellStatus:
    """Status of a cell."""

    name: str
    active_tasks: int = 0
    blocked_tasks: int = 0
    completed_today: int = 0
    available_devs: int = 0
    concerns: list[str] = field(default_factory=list)


@dataclass
class TaskAssignment:
    """A task assignment decision."""

    task_id: UUID
    agent_id: UUID
    agent_name: str
    reason: str


@dataclass
class Escalation:
    """An escalation to higher management."""

    issue: str
    severity: str  # low, medium, high, critical
    task_id: UUID | None = None
    proposed_solution: str | None = None


# =============================================================================
# DOCUMENTER PHASES AND CONTEXT
# =============================================================================


class DocTaskPhase(StrEnum):
    """Phases of the Documenter lifecycle."""

    MONITOR = "monitor"
    RECEIVE = "receive"
    GATHER = "gather"
    SYNTHESIZE = "synthesize"
    WRITE = "write"
    REVIEW = "review"
    PUBLISH = "publish"


class DocType(StrEnum):
    """Types of documentation."""

    API = "api"
    README = "readme"
    ARCHITECTURE = "architecture"
    CHANGELOG = "changelog"
    KNOWLEDGE_BASE = "knowledge_base"
    COMPONENT = "component"
    DESIGN_SYSTEM = "design_system"


@dataclass
class DocumentSpec:
    """Specification for a document to create/update."""

    doc_type: DocType
    title: str
    path: str
    priority: str = "required"  # required, optional
    content: str | None = None


@dataclass
class DocContext:
    """Context for the current documentation task."""

    task_id: UUID
    title: str
    session_id: UUID | None = None  # Primary session for this task
    phase: DocTaskPhase = DocTaskPhase.RECEIVE
    # Gathered materials
    dev_notes: str | None = None
    qa_feedback: str | None = None
    commits: list[str] = field(default_factory=list)
    conversations: list[str] = field(default_factory=list)
    code_changes: list[str] = field(default_factory=list)
    # Synthesis
    summary: str | None = None
    documents_needed: list[DocumentSpec] = field(default_factory=list)
    current_doc: int = 0
    # Output
    written_docs: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    notes: list[str] = field(default_factory=list)


# =============================================================================
# BOARD PHASES AND CONTEXT
# =============================================================================


class ProductOwnerPhase(StrEnum):
    """Phases of the Product Owner lifecycle."""

    VISION = "vision"
    ROADMAP = "roadmap"
    DEFINE = "define"
    PRIORITIZE = "prioritize"
    REVIEW = "review"
    FEEDBACK = "feedback"


@dataclass
class Feature:
    """A feature or epic."""

    id: UUID
    title: str
    description: str
    acceptance_criteria: list[str]
    priority: int  # 0-3
    status: str = "backlog"


class HeadMarketingPhase(StrEnum):
    """Phases of the Head of Marketing lifecycle."""

    RESEARCH = "research"
    STRATEGY = "strategy"
    PLAN = "plan"
    CREATE = "create"
    EXECUTE = "execute"
    ANALYZE = "analyze"


@dataclass
class Campaign:
    """A marketing campaign."""

    id: UUID
    name: str
    objective: str
    channels: list[str]
    start_date: datetime | None = None
    end_date: datetime | None = None
    status: str = "planning"
    metrics: dict[str, Any] = field(default_factory=dict)


class AuditorPhase(StrEnum):
    """Phases of the Auditor lifecycle."""

    OBSERVE = "observe"
    ANALYZE = "analyze"
    FLAG = "flag"
    REPORT = "report"
    AUDIT = "audit"
    ADVISE = "advise"


class AuditorFlagSeverity(StrEnum):
    """Severity of flagged issues from auditor."""

    INFO = "info"
    WARNING = "warning"
    CONCERN = "concern"
    CRITICAL = "critical"


@dataclass
class AuditFlag:
    """A flagged issue from audit observation."""

    id: UUID
    severity: AuditorFlagSeverity
    category: str  # quality, process, communication, efficiency
    description: str
    evidence: list[str]
    recommendation: str | None = None
    reported_to_ceo: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AuditReport:
    """A report to the CEO."""

    period: str
    summary: str
    flags: list[AuditFlag]
    metrics: dict[str, Any]
    recommendations: list[str]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
