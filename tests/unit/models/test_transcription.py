"""StreamBuffer + TranscriptionConfig coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from roboco.models.transcription import StreamBuffer, TranscriptionConfig


def _buffer() -> StreamBuffer:
    return StreamBuffer(
        agent_id=uuid4(),
        channel_id=uuid4(),
        session_id=uuid4(),
    )


def test_append_accumulates() -> None:
    buf = _buffer()
    buf.append("hello ")
    buf.append("world")
    assert buf.content == "hello world"
    assert buf.chunks == ["hello ", "world"]


def test_clear_returns_content_and_resets() -> None:
    buf = _buffer()
    buf.append("data")
    out = buf.clear()
    assert out == "data"
    assert buf.content == ""
    assert buf.chunks == []
    assert buf.is_complete is False


def test_char_count() -> None:
    buf = _buffer()
    buf.append("12345")
    assert buf.char_count == 5


def test_age_property_positive() -> None:
    buf = _buffer()
    assert buf.age >= timedelta(0)


def test_idle_time_property_positive() -> None:
    buf = _buffer()
    assert buf.idle_time >= timedelta(0)


def test_is_ready_when_complete() -> None:
    buf = _buffer()
    buf.is_complete = True
    assert buf.is_ready_for_extraction() is True


def test_is_ready_when_max_chars_exceeded() -> None:
    buf = _buffer()
    buf.append("a" * 6000)
    assert buf.is_ready_for_extraction(max_chars=5000) is True


def test_is_ready_when_idle_after_min_chars() -> None:
    buf = _buffer()
    buf.append("a" * 100)  # Above min_chars
    # Force last_chunk_at to be old.
    buf.last_chunk_at = datetime.now(UTC) - timedelta(seconds=10)
    assert buf.is_ready_for_extraction(idle_threshold=timedelta(seconds=2)) is True


def test_is_ready_with_sentence_ending() -> None:
    buf = _buffer()
    buf.append("a" * 50)
    buf.append(".")
    assert buf.is_ready_for_extraction() is True


def test_is_ready_with_question_mark() -> None:
    buf = _buffer()
    buf.append("a" * 50)
    buf.append("?")
    assert buf.is_ready_for_extraction() is True


def test_is_not_ready_below_min_chars() -> None:
    buf = _buffer()
    buf.append("short.")
    assert buf.is_ready_for_extraction(min_chars=50) is False


def test_has_sentence_ending_empty_returns_false() -> None:
    buf = _buffer()
    assert buf._has_sentence_ending() is False


def test_transcription_config_defaults() -> None:
    cfg = TranscriptionConfig()
    assert cfg.min_chars_for_extraction == 50
    assert cfg.max_chars_before_flush == 5000
    assert cfg.idle_threshold_seconds == 2.0


def test_transcription_config_custom() -> None:
    cfg = TranscriptionConfig(min_chars_for_extraction=10, max_buffers_per_agent=5)
    assert cfg.min_chars_for_extraction == 10
    assert cfg.max_buffers_per_agent == 5
