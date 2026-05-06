"""roboco.models.llm coverage — TOON config and metrics dataclasses.

Covers ToonConfig defaults, EncodedBlock __str__, LLMUsage totals, and
ToonMetrics record/reset/savings/fallback rate computations.
"""

from __future__ import annotations

from datetime import UTC, datetime

from roboco.models.llm import (
    EncodedBlock,
    LLMUsage,
    ToonConfig,
    ToonMetrics,
)

_BIG_JSON = 100
_BIG_TOON = 60
_INPUT = 100
_OUTPUT = 50
_CACHE_CREATE = 10
_CACHE_READ = 20
_FALLBACK_PERCENT_HALF = 50.0
_TWO_DECODES = 2


def test_toon_config_defaults() -> None:
    cfg = ToonConfig()
    assert cfg.delimiter == ","
    assert cfg.indent > 0
    assert cfg.include_length is True


def test_encoded_block_str_includes_label_and_content() -> None:
    blk = EncodedBlock(content="data", label="Section")
    s = str(blk)
    assert "Section" in s
    assert "data" in s


def test_llm_usage_total_tokens() -> None:
    usage = LLMUsage(input_tokens=_INPUT, output_tokens=_OUTPUT)
    assert usage.total_tokens == _INPUT + _OUTPUT


def test_llm_usage_total_input_with_cache() -> None:
    usage = LLMUsage(
        input_tokens=_INPUT,
        cache_creation_input_tokens=_CACHE_CREATE,
        cache_read_input_tokens=_CACHE_READ,
    )
    assert usage.total_input_with_cache == _INPUT + _CACHE_CREATE + _CACHE_READ


def test_toon_metrics_initial_state() -> None:
    m = ToonMetrics()
    assert m.json_chars == 0
    assert m.toon_chars == 0
    assert m.encode_count == 0
    assert m.decode_count == 0
    assert m.savings_percent == 0.0  # zero json_chars guard
    assert m.fallback_rate == 0.0  # zero decode_count guard


def test_toon_metrics_record_encode() -> None:
    m = ToonMetrics()
    m.record_encode(json_chars=_BIG_JSON, toon_chars=_BIG_TOON)
    assert m.json_chars == _BIG_JSON
    assert m.toon_chars == _BIG_TOON
    assert m.encode_count == 1
    # 40% reduction from 100 -> 60.
    expected_savings = (1 - _BIG_TOON / _BIG_JSON) * 100
    assert m.savings_percent == expected_savings


def test_toon_metrics_record_decode_no_fallback() -> None:
    m = ToonMetrics()
    m.record_decode(used_fallback=False)
    assert m.decode_count == 1
    assert m.decode_fallback_count == 0
    assert m.fallback_rate == 0.0


def test_toon_metrics_record_decode_with_fallback() -> None:
    m = ToonMetrics()
    m.record_decode(used_fallback=False)
    m.record_decode(used_fallback=True)
    assert m.decode_count == _TWO_DECODES
    assert m.decode_fallback_count == 1
    assert m.fallback_rate == _FALLBACK_PERCENT_HALF


def test_toon_metrics_to_dict_shape() -> None:
    m = ToonMetrics()
    m.record_encode(_BIG_JSON, _BIG_TOON)
    m.record_decode(used_fallback=True)
    out = m.to_dict()
    assert out["json_chars"] == _BIG_JSON
    assert out["toon_chars"] == _BIG_TOON
    assert out["encode_count"] == 1
    assert out["decode_count"] == 1
    assert "started_at" in out


def test_toon_metrics_reset_clears_counters() -> None:
    m = ToonMetrics()
    m.record_encode(_BIG_JSON, _BIG_TOON)
    m.record_decode(used_fallback=True)
    before_reset = m.started_at
    m.reset()
    assert m.json_chars == 0
    assert m.toon_chars == 0
    assert m.encode_count == 0
    assert m.decode_count == 0
    assert m.decode_fallback_count == 0
    # started_at is refreshed after reset.
    assert isinstance(m.started_at, datetime)
    assert m.started_at >= before_reset
    assert m.started_at.tzinfo == UTC
