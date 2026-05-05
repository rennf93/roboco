"""ExtractionService coverage — pattern-based message classification."""

from __future__ import annotations

from uuid import uuid4

import pytest
from roboco.models import MessageType
from roboco.models.extraction import (
    ExtractionConfig,
    ExtractionContext,
)
from roboco.services.extraction import ExtractionPipeline, ExtractionService


def _ctx(content: str) -> ExtractionContext:
    return ExtractionContext(
        content=content,
        agent_id=uuid4(),
        channel_id=uuid4(),
        session_id=uuid4(),
        group_id=uuid4(),
    )


@pytest.fixture
def svc() -> ExtractionService:
    return ExtractionService()


# ---------------------------------------------------------------------------
# Empty / short content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_returns_empty_for_short_content(
    svc: ExtractionService,
) -> None:
    result = await svc.extract(_ctx("hi"))
    assert result.messages == []


@pytest.mark.asyncio
async def test_extract_handles_empty_segments(
    svc: ExtractionService,
) -> None:
    """Whitespace-only segments are skipped."""
    result = await svc.extract(_ctx("\n\n   \n\n   \n\n"))
    assert result.messages == []


# ---------------------------------------------------------------------------
# Pattern classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classifies_reasoning(svc: ExtractionService) -> None:
    result = await svc.extract(
        _ctx("I'm thinking about how to solve this problem efficiently.")
    )
    assert len(result.messages) >= 1
    assert MessageType.REASONING in result.types_extracted


@pytest.mark.asyncio
async def test_classifies_dialogue(svc: ExtractionService) -> None:
    result = await svc.extract(_ctx("Hey, can someone help me debug this issue?"))
    assert len(result.messages) >= 1


@pytest.mark.asyncio
async def test_classifies_decision(svc: ExtractionService) -> None:
    result = await svc.extract(
        _ctx("Decision: I will use the async pattern for this case.")
    )
    assert len(result.messages) >= 1
    assert MessageType.DECISION in result.types_extracted


@pytest.mark.asyncio
async def test_classifies_action(svc: ExtractionService) -> None:
    result = await svc.extract(_ctx("Starting the deployment process now."))
    assert len(result.messages) >= 1
    assert MessageType.ACTION in result.types_extracted


@pytest.mark.asyncio
async def test_classifies_blocker(svc: ExtractionService) -> None:
    result = await svc.extract(_ctx("Blocked: waiting for QA to review the PR."))
    assert len(result.messages) >= 1
    assert MessageType.BLOCKER in result.types_extracted


@pytest.mark.asyncio
async def test_classifies_technical_with_code_block(
    svc: ExtractionService,
) -> None:
    content = "Here is the implementation:\n\n```python\ndef foo(): pass\n```"
    result = await svc.extract(_ctx(content))
    assert len(result.messages) >= 1
    assert MessageType.TECHNICAL in result.types_extracted


@pytest.mark.asyncio
async def test_unmatched_defaults_to_reasoning(svc: ExtractionService) -> None:
    """Content without recognizable patterns falls back to REASONING."""
    result = await svc.extract(_ctx("xyzzy plugh fnord arglebargle"))
    assert len(result.messages) >= 1
    # Default classification is REASONING.
    assert any(m.type == MessageType.REASONING for m in result.messages)


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_splits_on_double_newlines(
    svc: ExtractionService,
) -> None:
    content = "First paragraph here.\n\nSecond paragraph here."
    result = await svc.extract(_ctx(content))
    assert len(result.messages) == 2


@pytest.mark.asyncio
async def test_extract_keeps_code_blocks_intact(svc: ExtractionService) -> None:
    content = (
        "Some explanation.\n\n```\nline1\nline2\nline3\n```\n\nSome more explanation."
    )
    result = await svc.extract(_ctx(content))
    code_segments = [m for m in result.messages if m.content.startswith("```")]
    assert len(code_segments) >= 1


# ---------------------------------------------------------------------------
# Config respect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_respects_max_segments() -> None:
    svc = ExtractionService(ExtractionConfig(max_segments_per_buffer=2))
    content = "First.\n\nSecond.\n\nThird.\n\nFourth.\n\nFifth."
    result = await svc.extract(_ctx(content))
    assert len(result.messages) <= 2


@pytest.mark.asyncio
async def test_extract_respects_min_content_length() -> None:
    svc = ExtractionService(ExtractionConfig(min_content_length=100))
    result = await svc.extract(_ctx("Short message only."))
    assert result.messages == []


# ---------------------------------------------------------------------------
# Result metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_populates_confidence_scores(
    svc: ExtractionService,
) -> None:
    result = await svc.extract(_ctx("I'm thinking carefully about this."))
    assert result.confidence_scores
    for score in result.confidence_scores.values():
        assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_extract_records_pattern_matches(
    svc: ExtractionService,
) -> None:
    result = await svc.extract(_ctx("Decision: going with option A."))
    assert result.pattern_matches  # Non-empty.


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_invokes_callback() -> None:
    pipeline = ExtractionPipeline()
    received: list = []

    async def on_message(msg) -> None:
        received.append(msg)

    pipeline.on_message(on_message)
    result = await pipeline.process_buffer(
        _ctx("First message here.\n\nSecond message here.")
    )
    assert result.message_count >= 1
    assert len(received) == result.message_count


@pytest.mark.asyncio
async def test_pipeline_swallows_callback_errors() -> None:
    """Callback failure should not abort the pipeline."""
    pipeline = ExtractionPipeline()

    async def bad_callback(msg) -> None:
        raise RuntimeError("boom")

    pipeline.on_message(bad_callback)
    # Should complete without raising despite callback error.
    result = await pipeline.process_buffer(_ctx("Hello there.\n\nGoodbye."))
    assert result is not None
