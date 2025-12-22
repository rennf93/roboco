"""
RoboCo Services

Phase 2: Communication, transcription, and permissions.
Phase 3: Intelligence - RAG, knowledge base, and journals.
Phase 5: Management - Tasks, kanban, metrics, dashboards.
"""

from roboco.services.audit import (
    AuditEventType,
    AuditService,
    get_audit_service,
)
from roboco.services.base import (
    BaseService,
    ConflictError,
    NotFoundError,
    ServiceError,
    ServiceUnavailableError,
    SingletonHolder,
    SingletonService,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.dashboard import (
    DashboardService,
    get_dashboard_service,
)
from roboco.services.extraction import ExtractionResult, ExtractionService
from roboco.services.journal import (
    GrowthMetrics,
    JournalService,
    JournalStats,
    get_journal_service,
)
from roboco.services.kanban import (
    KanbanService,
    get_kanban_service,
)
from roboco.services.messaging import (
    ChannelCreateRequest,
    GroupCreateRequest,
    MessageCreateRequest,
    MessagingService,
    SessionCreateRequest,
    get_messaging_service,
)
from roboco.services.metrics import (
    AgentMetrics,
    BlockerMetrics,
    MetricsService,
    TeamMetrics,
    VelocityMetrics,
    get_metrics_service,
)
from roboco.services.notification import NotificationService
from roboco.services.notification_delivery import (
    NotificationDeliveryService,
    get_notification_delivery_service,
)
from roboco.services.optimal import (
    IndexType,
    OptimalService,
    QueryContext,
    RAGResponse,
    SearchResult,
    close_optimal_service,
    get_optimal_service,
)
from roboco.services.permissions import PermissionService
from roboco.services.task import (
    TaskService,
    get_task_service,
)
from roboco.services.transcription import TranscriptionService

__all__ = [
    "AgentMetrics",
    "AuditEventType",
    "AuditService",
    "BaseService",
    "BlockerMetrics",
    "ChannelCreateRequest",
    "ConflictError",
    "DashboardService",
    "ExtractionResult",
    "ExtractionService",
    "GroupCreateRequest",
    "GrowthMetrics",
    "IndexType",
    "JournalService",
    "JournalStats",
    "KanbanService",
    "MessageCreateRequest",
    "MessagingService",
    "MetricsService",
    "NotFoundError",
    "NotificationDeliveryService",
    "NotificationService",
    "OptimalService",
    "PermissionService",
    "QueryContext",
    "RAGResponse",
    "SearchResult",
    "ServiceError",
    "ServiceUnavailableError",
    "SessionCreateRequest",
    "SingletonHolder",
    "SingletonService",
    "TaskService",
    "TeamMetrics",
    "TranscriptionService",
    "UnauthorizedError",
    "ValidationError",
    "VelocityMetrics",
    "close_optimal_service",
    "get_audit_service",
    "get_dashboard_service",
    "get_journal_service",
    "get_kanban_service",
    "get_messaging_service",
    "get_metrics_service",
    "get_notification_delivery_service",
    "get_optimal_service",
    "get_task_service",
]
