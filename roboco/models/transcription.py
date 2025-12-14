"""
Transcription Models

Data classes for stream transcription and buffering.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4


@dataclass
class StreamBuffer:
    """
    Buffer for accumulating stream chunks from an agent.

    Tracks the current buffer content, timing, and metadata.
    """

    agent_id: UUID
    channel_id: UUID
    session_id: UUID
    connection_id: UUID = field(default_factory=uuid4)

    # Buffer content
    content: str = ""
    chunks: list[str] = field(default_factory=list)

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_chunk_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # State
    is_complete: bool = False

    def append(self, chunk: str) -> None:
        """Append a chunk to the buffer."""
        self.chunks.append(chunk)
        self.content += chunk
        self.last_chunk_at = datetime.now(UTC)

    def clear(self) -> str:
        """Clear buffer and return content."""
        content = self.content
        self.content = ""
        self.chunks.clear()
        self.started_at = datetime.now(UTC)
        self.is_complete = False
        return content

    @property
    def age(self) -> timedelta:
        """Time since buffer started."""
        return datetime.now(UTC) - self.started_at

    @property
    def idle_time(self) -> timedelta:
        """Time since last chunk."""
        return datetime.now(UTC) - self.last_chunk_at

    @property
    def char_count(self) -> int:
        """Current buffer character count."""
        return len(self.content)

    def is_ready_for_extraction(
        self,
        min_chars: int = 50,
        idle_threshold: timedelta = timedelta(seconds=2),
        max_chars: int = 5000,
    ) -> bool:
        """
        Determine if buffer is ready for extraction.

        Ready conditions:
        - Has minimum characters AND idle for threshold
        - Exceeds max characters (force flush)
        - Is marked complete
        - Contains sentence-ending punctuation after min_chars
        """
        if self.is_complete:
            return True

        if self.char_count >= max_chars:
            return True

        if self.char_count >= min_chars:
            # Idle long enough
            if self.idle_time >= idle_threshold:
                return True

            # Contains sentence ending
            if self._has_sentence_ending():
                return True

        return False

    def _has_sentence_ending(self) -> bool:
        """Check if buffer ends with sentence-ending punctuation."""
        stripped = self.content.rstrip()
        if not stripped:
            return False
        return stripped[-1] in ".!?:\n"


@dataclass
class TranscriptionConfig:
    """Configuration for the transcription service."""

    # Buffer thresholds
    min_chars_for_extraction: int = 50
    max_chars_before_flush: int = 5000
    idle_threshold_seconds: float = 2.0

    # Processing
    flush_interval_seconds: float = 1.0

    # Limits
    max_buffers_per_agent: int = 10
