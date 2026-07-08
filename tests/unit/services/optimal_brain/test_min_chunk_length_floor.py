"""Per-index-type chunk-length floor — journals/learnings are short by
design (templated notes, distilled lessons) and were always discarded by
the global 200-char garbage filter ("All chunks filtered as garbage",
raw_count=1 on every journal write). Journals get a 40-char floor,
learnings an 80-char floor; every other index keeps the 200-char default.
"""

from __future__ import annotations

from roboco.models.optimal import IndexType
from roboco.services.optimal_brain.indexes.base import (
    _MIN_CHUNK_LENGTH,
    IndexConfig,
    _filter_quality_chunks,
)
from roboco.services.optimal_brain.text_chunker import Chunk

# Named constants — ruff PLR2004 forbids magic-number comparisons.
_JOURNALS_FLOOR = 40
_LEARNINGS_FLOOR = 80
_DEFAULT_FLOOR = _MIN_CHUNK_LENGTH  # 200


def _chunk(text: str) -> Chunk:
    return Chunk(text=text, source="roboco://journals/entry-1")


# ---------------------------------------------------------------------------
# IndexConfig.from_settings — per-index-type floor
# ---------------------------------------------------------------------------


def test_journals_config_lowers_floor_to_40() -> None:
    assert IndexConfig.from_settings(IndexType.JOURNALS).min_chunk_length == (
        _JOURNALS_FLOOR
    )


def test_learnings_config_lowers_floor_to_80() -> None:
    assert IndexConfig.from_settings(IndexType.LEARNINGS).min_chunk_length == (
        _LEARNINGS_FLOOR
    )


def test_other_index_keeps_default_floor() -> None:
    assert IndexConfig.from_settings(IndexType.DOCUMENTATION).min_chunk_length == (
        _DEFAULT_FLOOR
    )
    assert IndexConfig.from_settings(IndexType.CODE).min_chunk_length == _DEFAULT_FLOOR
    assert IndexConfig(persist_dir="/tmp/idx").min_chunk_length == _DEFAULT_FLOOR


# ---------------------------------------------------------------------------
# _filter_quality_chunks — the actual gate
# ---------------------------------------------------------------------------


def test_120_char_journal_chunk_passes_journals_floor() -> None:
    text = (
        "Reflected on task rate-limiter-fix: fixed the off-by-one boundary "
        "check in the 429 threshold and added a regression test."
    )
    assert len(text) >= _JOURNALS_FLOOR * 3
    kept = _filter_quality_chunks([_chunk(text)], min_chunk_length=_JOURNALS_FLOOR)
    assert len(kept) == 1


def test_150_char_chunk_still_fails_default_200_floor() -> None:
    text = "x" * (_DEFAULT_FLOOR - 50)
    kept = _filter_quality_chunks([_chunk(text)], min_chunk_length=_DEFAULT_FLOOR)
    assert kept == []


def test_default_index_behavior_unchanged_no_arg() -> None:
    """Omitting min_chunk_length keeps the historical 200-char behavior."""
    short = _chunk("x" * (_DEFAULT_FLOOR - 50))
    long_enough = _chunk("x" * (_DEFAULT_FLOOR + 50))
    assert _filter_quality_chunks([short]) == []
    assert _filter_quality_chunks([long_enough]) == [long_enough]


def test_markdown_only_chunk_still_filtered_at_lowered_floor() -> None:
    """A 40-char chunk that's mostly ``` / --- / # formatting is still junk."""
    text = "#" * (_JOURNALS_FLOOR // 2) + "-" * (_JOURNALS_FLOOR // 2)
    kept = _filter_quality_chunks([_chunk(text)], min_chunk_length=_JOURNALS_FLOOR)
    assert kept == []
