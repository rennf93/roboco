"""
API Schemas

Pydantic models for request/response serialization.
"""

from roboco.api.schemas.channels import (
    ChannelDetailResponse,
    ChannelListResponse,
    ChannelResponse,
    GroupResponse,
    ListChannelsQuery,
)
from roboco.api.schemas.dashboard import (
    AuditorDashboard,
    AuditorFlag,
    AuditorReport,
    CEOOverview,
    ChannelFeed,
    CreateFlagRequest,
    CreateReportRequest,
    FlagSeverity,
    TeamHealth,
)
from roboco.api.schemas.health import HealthResponse, ReadinessResponse
from roboco.api.schemas.journals import (
    CreateEntryRequest,
    DecisionLogRequest,
    GeneralEntryRequest,
    GrowthMetricsResponse,
    JournalEntryResponse,
    JournalResponse,
    JournalStatsResponse,
    LearningRequest,
    ListEntriesParams,
    SearchEntriesRequest,
    StruggleRequest,
    TaskReflectionRequest,
)
from roboco.api.schemas.messages import (
    ListMessagesParams,
    MessageCreateRequest,
    MessageEditRequest,
    MessageListResponse,
    MessageResponse,
)
from roboco.api.schemas.notifications import (
    ListNotificationsParams,
    NotificationCreateRequest,
    NotificationListResponse,
    NotificationResponse,
)
from roboco.api.schemas.optimal import (
    ClearIndexResponse,
    IndexCodeRequest,
    IndexDocsRequest,
    IndexResponse,
    IndexStatsResponse,
    PromptTemplateRequest,
    PromptTemplateResponse,
    RAGQueryRequest,
    RAGQueryResponse,
    RefreshIndexResponse,
    RefreshRequest,
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
    TokenEstimateRequest,
    TokenEstimateResponse,
)
from roboco.api.schemas.orchestrator import (
    AgentStatusResponse,
    OrchestratorStatusResponse,
    ResolveWaitRequest,
    SpawnAgentRequest,
    WaitingAgentResponse,
)
from roboco.api.schemas.sessions import (
    ListSessionsParams,
    SessionCreateRequest,
    SessionListResponse,
    SessionResponse,
)
from roboco.api.schemas.stream import (
    ExtractedMessageResponse,
    ExtractionResponse,
    ExtractRequest,
    StreamChunkRequest,
    StreamCompleteRequest,
    TranscriptionStatsResponse,
)
from roboco.api.schemas.tasks import (
    CheckpointRequest,
    ClaimRequest,
    CommitRequest,
    ListTasksQuery,
    ProgressRequest,
    QANotes,
    TaskCountResponse,
    TaskResponse,
    TaskUpdate,
    TeamTasksQuery,
)

__all__ = [
    # Orchestrator
    "AgentStatusResponse",
    # Dashboard
    "AuditorDashboard",
    "AuditorFlag",
    "AuditorReport",
    "CEOOverview",
    "ChannelDetailResponse",
    "ChannelFeed",
    # Channels
    "ChannelListResponse",
    "ChannelResponse",
    # Tasks
    "CheckpointRequest",
    "ClaimRequest",
    # Optimal
    "ClearIndexResponse",
    "CommitRequest",
    # Journals
    "CreateEntryRequest",
    "CreateFlagRequest",
    "CreateReportRequest",
    "DecisionLogRequest",
    "ExtractRequest",
    # Stream
    "ExtractedMessageResponse",
    "ExtractionResponse",
    "FlagSeverity",
    "GeneralEntryRequest",
    "GroupResponse",
    "GrowthMetricsResponse",
    # Health
    "HealthResponse",
    "IndexCodeRequest",
    "IndexDocsRequest",
    "IndexResponse",
    "IndexStatsResponse",
    "JournalEntryResponse",
    "JournalResponse",
    "JournalStatsResponse",
    "LearningRequest",
    "ListChannelsQuery",
    "ListEntriesParams",
    # Messages
    "ListMessagesParams",
    # Notifications
    "ListNotificationsParams",
    # Sessions
    "ListSessionsParams",
    "ListTasksQuery",
    "MessageCreateRequest",
    "MessageEditRequest",
    "MessageListResponse",
    "MessageResponse",
    "NotificationCreateRequest",
    "NotificationListResponse",
    "NotificationResponse",
    "OrchestratorStatusResponse",
    "ProgressRequest",
    "PromptTemplateRequest",
    "PromptTemplateResponse",
    "QANotes",
    "RAGQueryRequest",
    "RAGQueryResponse",
    "ReadinessResponse",
    "RefreshIndexResponse",
    "RefreshRequest",
    "ResolveWaitRequest",
    "SearchEntriesRequest",
    "SearchRequest",
    "SearchResponse",
    "SearchResultResponse",
    "SessionCreateRequest",
    "SessionListResponse",
    "SessionResponse",
    "SpawnAgentRequest",
    "StreamChunkRequest",
    "StreamCompleteRequest",
    "StruggleRequest",
    "TaskCountResponse",
    "TaskReflectionRequest",
    "TaskResponse",
    "TaskUpdate",
    "TeamHealth",
    "TeamTasksQuery",
    "TokenEstimateRequest",
    "TokenEstimateResponse",
    "TranscriptionStatsResponse",
    "WaitingAgentResponse",
]
