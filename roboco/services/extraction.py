"""
Message Extraction Service

Extracts structured messages from raw agent LLM output.
Uses pattern matching and optional LLM-based classification to
identify different message types: reasoning, dialogue, decisions,
actions, blockers, and technical content.

Flow:
1. TranscriptionService yields ready buffer
2. ExtractionService analyzes content
3. Produces list of ExtractedMessage objects
4. Messages are stored and broadcast
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from roboco.models import MessageType
from roboco.models.message import ExtractedMessage

logger = structlog.get_logger()

# Maximum length for raw excerpt storage
MAX_EXCERPT_LENGTH = 200


@dataclass
class ExtractionContext:
    """Context for message extraction."""

    content: str
    agent_id: UUID
    channel_id: UUID
    session_id: UUID
    group_id: UUID
    task_id: UUID | None = None


# =============================================================================
# EXTRACTION PATTERNS
# =============================================================================

# Patterns for identifying message types
# These are heuristics; can be enhanced with LLM classification

REASONING_PATTERNS = [
    r"(?i)^I(?:'m| am) thinking",
    r"(?i)^Let me (?:think|consider|analyze)",
    r"(?i)^Hmm,? ",
    r"(?i)^I need to",
    r"(?i)^First,? I(?:'ll| will| should)",
    r"(?i)^My approach",
    r"(?i)^To solve this",
    r"(?i)^The (?:issue|problem|question) (?:is|seems)",
    r"(?i)^Looking at",
    r"(?i)^Analyzing",
    r"(?i)^Considering",
]

DIALOGUE_PATTERNS = [
    r"(?i)^Hey,? ",
    r"(?i)^Hi,? ",
    r"(?i)^@\w+",  # Mentions
    r"(?i)^Can (?:you|someone)",
    r"(?i)^Could (?:you|someone)",
    r"(?i)^Would (?:you|someone)",
    r"(?i)^I(?:'m| am) asking",
    r"(?i)^Question:",
    r"(?i)^Does anyone",
    r"(?i)^What do you think",
    r"(?i)^Thoughts\?",
    r"\?$",  # Ends with question mark
]

DECISION_PATTERNS = [
    r"(?i)^I(?:'ve| have) decided",
    r"(?i)^Decision:",
    r"(?i)^I(?:'ll| will) go with",
    r"(?i)^Let(?:'s| us) use",
    r"(?i)^We(?:'ll| will) use",
    r"(?i)^The (?:approach|solution|answer) is",
    r"(?i)^Choosing",
    r"(?i)^Selected:",
    r"(?i)^Going with",
    r"(?i)^After consideration,? I(?:'ll| will)",
]

ACTION_PATTERNS = [
    r"(?i)^Starting",
    r"(?i)^Creating",
    r"(?i)^Writing",
    r"(?i)^Implementing",
    r"(?i)^Running",
    r"(?i)^Executing",
    r"(?i)^Testing",
    r"(?i)^Committing",
    r"(?i)^Pushing",
    r"(?i)^Deploying",
    r"(?i)^Task (?:complete|done|finished)",
    r"(?i)^Done:",
    r"(?i)^Completed:",
    r"(?i)^✓",
    r"(?i)^✅",
]

BLOCKER_PATTERNS = [
    r"(?i)^Blocked:",
    r"(?i)^Blocker:",
    r"(?i)^I(?:'m| am) blocked",
    r"(?i)^Cannot proceed",
    r"(?i)^Waiting (?:on|for)",
    r"(?i)^Need (?:help|assistance|input)",
    r"(?i)^Stuck on",
    r"(?i)^Dependency:",
    r"(?i)^Missing:",
    r"(?i)^Error:",
    r"(?i)^Failed:",
    r"(?i)^Unable to",
    r"(?i)^🚫",
    r"(?i)^⛔",
]

TECHNICAL_PATTERNS = [
    r"```",  # Code blocks
    r"(?i)^The (?:function|class|method|variable)",
    r"(?i)^This (?:code|implementation|function)",
    r"(?i)^Here(?:'s| is) (?:the|how)",
    r"(?i)^API:",
    r"(?i)^Schema:",
    r"(?i)^Endpoint:",
    r"(?i)^Response:",
    r"(?i)^Request:",
    r"^[A-Z][a-zA-Z]+(?:Error|Exception)",  # Exception names
]


@dataclass
class ExtractionResult:
    """Result of extraction from a buffer."""

    messages: list[ExtractedMessage]
    raw_content: str
    agent_id: UUID
    channel_id: UUID
    session_id: UUID
    extracted_at: datetime = field(default_factory=datetime.now(UTC))

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


class ExtractionService:
    """
    Service for extracting structured messages from raw agent output.

    Uses pattern matching to classify segments into message types.
    Can be extended with LLM-based classification for better accuracy.

    Usage:
        service = ExtractionService()
        result = await service.extract(buffer)
        for message in result.messages:
            await store_message(message)
    """

    def __init__(self, config: ExtractionConfig | None = None) -> None:
        self.config = config or ExtractionConfig()
        self.log = logger.bind(component="extraction")

        # Compile patterns
        self._compiled_patterns: dict[MessageType, list[re.Pattern]] = {
            MessageType.REASONING: [re.compile(p) for p in REASONING_PATTERNS],
            MessageType.DIALOGUE: [re.compile(p) for p in DIALOGUE_PATTERNS],
            MessageType.DECISION: [re.compile(p) for p in DECISION_PATTERNS],
            MessageType.ACTION: [re.compile(p) for p in ACTION_PATTERNS],
            MessageType.BLOCKER: [re.compile(p) for p in BLOCKER_PATTERNS],
            MessageType.TECHNICAL: [re.compile(p) for p in TECHNICAL_PATTERNS],
        }

        # Mention pattern
        self._mention_pattern = re.compile(r"@(\w+)")

    async def extract(self, ctx: ExtractionContext) -> ExtractionResult:
        """
        Extract messages from raw content.

        Args:
            ctx: Extraction context with content and metadata

        Returns:
            ExtractionResult with extracted messages
        """
        if len(ctx.content) < self.config.min_content_length:
            return ExtractionResult(
                messages=[],
                raw_content=ctx.content,
                agent_id=ctx.agent_id,
                channel_id=ctx.channel_id,
                session_id=ctx.session_id,
            )

        # Segment the content
        segments = self._segment_content(ctx.content)

        messages: list[ExtractedMessage] = []
        pattern_matches: dict[str, list[str]] = {}
        confidence_scores: dict[UUID, float] = {}

        for segment in segments[: self.config.max_segments_per_buffer]:
            if not segment.strip():
                continue

            # Classify segment
            msg_type, confidence, matches = self._classify_segment(segment)

            # Store pattern matches for debugging
            if matches:
                pattern_matches[segment[:50]] = matches

            # Extract mentions
            mentions: list[UUID] = []
            if self.config.extract_mentions:
                mention_names = self._mention_pattern.findall(segment)
                # In production, resolve names to agent UUIDs
                # For now, just log them
                if mention_names:
                    self.log.debug("Found mentions", mentions=mention_names)

            # Create message
            message = ExtractedMessage(
                id=uuid4(),
                agent_id=ctx.agent_id,
                channel_id=ctx.channel_id,
                group_id=ctx.group_id,
                session_id=ctx.session_id,
                type=msg_type,
                content=segment.strip(),
                content_length=len(segment.strip()),
                mentions=mentions,
                task_id=ctx.task_id,
                confidence=confidence,
                raw_excerpt=segment[:MAX_EXCERPT_LENGTH]
                if len(segment) > MAX_EXCERPT_LENGTH
                else segment,
            )

            messages.append(message)
            confidence_scores[message.id] = confidence

        result = ExtractionResult(
            messages=messages,
            raw_content=ctx.content,
            agent_id=ctx.agent_id,
            channel_id=ctx.channel_id,
            session_id=ctx.session_id,
            pattern_matches=pattern_matches,
            confidence_scores=confidence_scores,
        )

        self.log.info(
            "Extraction complete",
            agent_id=str(ctx.agent_id),
            message_count=result.message_count,
            types=result.types_extracted,
        )

        return result

    def _segment_content(self, content: str) -> list[str]:
        """
        Segment content into logical chunks.

        Segmentation strategy:
        1. Split on double newlines (paragraphs)
        2. Split on code blocks
        3. Keep sentences together
        """
        segments: list[str] = []

        # First, handle code blocks specially
        code_block_pattern = re.compile(r"(```[\s\S]*?```)")
        parts = code_block_pattern.split(content)

        for part in parts:
            if part.startswith("```"):
                # Code block is its own segment
                segments.append(part)
            else:
                # Split non-code on double newlines
                paragraphs = re.split(r"\n\s*\n", part)
                for para in paragraphs:
                    if para.strip():
                        segments.append(para.strip())

        return segments

    def _classify_segment(
        self,
        segment: str,
    ) -> tuple[MessageType, float, list[str]]:
        """
        Classify a segment into a message type.

        Returns:
            Tuple of (MessageType, confidence, matched_patterns)
        """
        # Check each type's patterns
        type_scores: dict[MessageType, tuple[int, list[str]]] = {}

        for msg_type, patterns in self._compiled_patterns.items():
            matches: list[str] = []
            for pattern in patterns:
                if pattern.search(segment):
                    matches.append(pattern.pattern)

            if matches:
                type_scores[msg_type] = (len(matches), matches)

        if not type_scores:
            # Default to REASONING if no patterns match
            return MessageType.REASONING, 0.5, []

        # Get highest scoring type
        best_type = max(type_scores.keys(), key=lambda t: type_scores[t][0])
        match_count, matches = type_scores[best_type]

        # Calculate confidence based on match count
        total_patterns = len(self._compiled_patterns[best_type])
        confidence = min(1.0, (match_count / max(1, total_patterns)) + 0.5)

        return best_type, confidence, matches

    async def extract_with_llm(self, ctx: ExtractionContext) -> ExtractionResult:
        """
        Extract messages using LLM classification.

        This is more accurate but slower and more expensive.
        Falls back to pattern matching if LLM unavailable.
        Uses TOON format for token-efficient communication.
        """
        from anthropic import AsyncAnthropic

        from roboco.config import settings
        from roboco.llm import ToonAdapter

        toon = ToonAdapter()

        try:
            client = AsyncAnthropic(api_key=settings.anthropic_api_key)

            # Build prompt for LLM classification using TOON
            prompt = f"""Analyze this agent output and classify each distinct segment.

Agent output:
{ctx.content}

For each segment, identify:
- type: one of [reasoning, dialogue, decision, action, blocker, technical]
- content: the segment text
- confidence: 0.0 to 1.0

Return as TOON tabular format:
[N,]{{type,content,confidence}}:
reasoning,Analyzing the problem...,0.9
action,Creating file utils.py,0.95

Output only valid TOON, no other text."""

            response = await client.messages.create(
                model="claude-3-haiku-20240307",  # Fast, cheap for classification
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )

            # Parse response using TOON (falls back to JSON)
            response_text = response.content[0].text
            segments = toon.decode(response_text)

            messages: list[ExtractedMessage] = []
            for segment in segments:
                msg_type_str = segment.get("type", "reasoning")
                msg_type = MessageType(msg_type_str)
                msg_content = segment.get("content", "")
                confidence = segment.get("confidence", 0.8)

                messages.append(
                    ExtractedMessage(
                        id=uuid4(),
                        content=msg_content,
                        message_type=msg_type,
                        agent_id=ctx.agent_id,
                        channel_id=ctx.channel_id,
                        session_id=ctx.session_id,
                        group_id=ctx.group_id,
                        task_id=ctx.task_id,
                        confidence=confidence,
                        metadata={"extraction_method": "llm"},
                    )
                )

            return ExtractionResult(
                messages=messages,
                raw_content=ctx.content,
                extraction_time=0.0,  # Could measure actual time
                confidence=sum(m.confidence for m in messages) / max(1, len(messages)),
            )

        except Exception as e:
            # Fall back to pattern matching
            self.log.warning("LLM extraction failed, using patterns", error=str(e))
            return await self.extract(ctx)


# =============================================================================
# PIPELINE
# =============================================================================


class ExtractionPipeline:
    """
    Complete pipeline from transcription buffer to stored messages.

    Combines TranscriptionService and ExtractionService for end-to-end
    processing of agent LLM streams.

    Usage:
        from roboco.services.transcription import TranscriptionService

        transcription = TranscriptionService()
        pipeline = ExtractionPipeline(transcription)

        await pipeline.start()

        # Messages are automatically extracted and callbacks invoked
        pipeline.on_message(lambda msg: store_message(msg))
    """

    def __init__(
        self,
        extraction_service: ExtractionService | None = None,
    ) -> None:
        self.extraction = extraction_service or ExtractionService()
        self._message_callbacks: list[Any] = []
        self.log = logger.bind(component="extraction_pipeline")

    def on_message(self, callback: Any) -> None:
        """Register a callback for extracted messages."""
        self._message_callbacks.append(callback)

    async def process_buffer(self, ctx: ExtractionContext) -> ExtractionResult:
        """
        Process a buffer and invoke callbacks for each message.
        """
        result = await self.extraction.extract(ctx)

        # Invoke callbacks for each message
        for message in result.messages:
            for callback in self._message_callbacks:
                try:
                    await callback(message)
                except Exception as e:
                    self.log.error(
                        "Message callback error",
                        error=str(e),
                        message_id=str(message.id),
                    )

        return result
