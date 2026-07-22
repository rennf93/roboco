"""
RoboCo Services

Phase 2: Communication, transcription, and permissions.
Phase 3: Intelligence - RAG, knowledge base, and journals.
Phase 5: Management - Tasks, kanban, dashboards.
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
    "AuditEventType",
    "AuditService",
    "BaseService",
    "ConflictError",
    "ExtractionResult",
    "ExtractionService",
    "GrowthMetrics",
    "IndexType",
    "JournalService",
    "JournalStats",
    "KanbanService",
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
    "SingletonHolder",
    "SingletonService",
    "TaskService",
    "TranscriptionService",
    "UnauthorizedError",
    "ValidationError",
    "close_optimal_service",
    "get_audit_service",
    "get_journal_service",
    "get_kanban_service",
    "get_notification_delivery_service",
    "get_optimal_service",
    "get_task_service",
]
