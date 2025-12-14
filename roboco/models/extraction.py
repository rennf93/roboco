"""
Extraction Models

Data classes for message extraction from agent LLM output.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from roboco.models import MessageType
from roboco.models.message import ExtractedMessage


@dataclass
class ExtractionContext:
    """Context for message extraction."""

    content: str
    agent_id: UUID
    channel_id: UUID
    session_id: UUID
    group_id: UUID
    task_id: UUID | None = None


@dataclass
class ExtractionResult:
    """Result of extraction from a buffer."""

    messages: list[ExtractedMessage]
    raw_content: str
    agent_id: UUID
    channel_id: UUID
    session_id: UUID
    extracted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Metadata
    pattern_matches: dict[str, list[str]] = field(default_factory=dict)
    confidence_scores: dict[UUID, float] = field(default_factory=dict)

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def types_extracted(self) -> list[MessageType]:
        return list({m.type for m in self.messages})


@dataclass
class ExtractionConfig:
    """Configuration for the extraction service."""

    # Minimum content length to attempt extraction
    min_content_length: int = 10

    # Confidence threshold for pattern matching
    min_pattern_confidence: float = 0.5

    # Whether to use LLM for classification (future)
    use_llm_classification: bool = False

    # Maximum segments to extract from a single buffer
    max_segments_per_buffer: int = 20

    # Whether to extract @mentions
    extract_mentions: bool = True
