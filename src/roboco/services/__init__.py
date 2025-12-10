"""
RoboCo Services

Phase 2: Communication, transcription, and permissions.
Phase 3: Intelligence - RAG, knowledge base, and journals.
Phase 5: Management - Tasks, kanban, metrics, dashboards.
"""

from roboco.services.transcription import TranscriptionService
from roboco.services.extraction import ExtractionService, ExtractionResult
from roboco.services.permissions import PermissionService
from roboco.services.optimal import (
    OptimalService,
    IndexType,
    SearchResult,
    RAGResponse,
    QueryContext,
    get_optimal_service,
    close_optimal_service,
)
from roboco.services.journal import (
    JournalService,
    JournalStats,
    GrowthMetrics,
    get_journal_service,
)
from roboco.services.task import (
    TaskService,
    get_task_service,
)
from roboco.services.kanban import (
    KanbanService,
    get_kanban_service,
)
from roboco.services.metrics import (
    MetricsService,
    VelocityMetrics,
    BlockerMetrics,
    TeamMetrics,
    AgentMetrics,
    get_metrics_service,
)

__all__ = [
    # Phase 2
    "TranscriptionService",
    "ExtractionService",
    "ExtractionResult",
    "PermissionService",
    # Phase 3 - Optimal API
    "OptimalService",
    "IndexType",
    "SearchResult",
    "RAGResponse",
    "QueryContext",
    "get_optimal_service",
    "close_optimal_service",
    # Phase 3 - Journal API
    "JournalService",
    "JournalStats",
    "GrowthMetrics",
    "get_journal_service",
    # Phase 5 - Task API
    "TaskService",
    "get_task_service",
    # Phase 5 - Kanban
    "KanbanService",
    "get_kanban_service",
    # Phase 5 - Metrics
    "MetricsService",
    "VelocityMetrics",
    "BlockerMetrics",
    "TeamMetrics",
    "AgentMetrics",
    "get_metrics_service",
]
