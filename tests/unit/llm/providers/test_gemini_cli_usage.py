"""gemini_cli_usage — stats-from-stdout usage capture + exit classification."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from roboco.llm.providers import gemini_cli_usage as gu

if TYPE_CHECKING:
    from pathlib import Path


def _single_json(stats: dict) -> str:
    return json.dumps({"response": "ok", "stats": stats, "error": None})


def _stream_json(events: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in events)


# Flat ModelStreamStats — the REAL shape our entrypoint actually parses,
# transcribed verbatim from the terminal `result` event's `stats.models.<name>`
# entry (--output-format stream-json), per
# packages/core/src/output/types.ts's ModelStreamStats interface:
# {total_tokens, input_tokens, output_tokens, cached, input} — NO nested
# "tokens" key. `input_tokens` is already the full billable prompt count.
_FLAT_MODEL_STATS = {
    "models": {
        "gemini-2.5-pro": {
            "total_tokens": 1500,
            "input_tokens": 1000,
            "output_tokens": 500,
            "cached": 0,
            "input": 1000,
        }
    }
}

# Nested SessionMetrics.ModelMetrics — the --output-format json FALLBACK
# shape (never actually emitted by our stream-json entrypoint, but tolerated
# defensively), transcribed verbatim from
# packages/core/src/telemetry/uiTelemetry.ts's ModelMetrics interface:
# tokens: {input, prompt, candidates, total, cached, thoughts, tool}.
_NESTED_MODEL_STATS = {
    "models": {
        "gemini-2.5-pro": {
            "tokens": {
                "input": 1000,
                "prompt": 1000,
                "candidates": 500,
                "total": 1700,
                "cached": 0,
                "thoughts": 200,
                "tool": 0,
            }
        }
    }
}


def test_extract_model_stats_reads_flat_stream_json_shape() -> None:
    # The PRIMARY path: this is the real shape produced by our entrypoint's
    # --output-format stream-json — no "tokens" nesting, no thoughts/tool
    # fields to fold (they aren't broken out in this flat shape at all).
    result = gu.extract_model_stats(_FLAT_MODEL_STATS)
    assert result == {"gemini-2.5-pro": (1000, 500)}


def test_extract_model_stats_empty_for_missing_models() -> None:
    assert gu.extract_model_stats({}) == {}
    assert gu.extract_model_stats({"models": "not-a-dict"}) == {}


def test_extract_model_stats_reads_nested_json_mode_fallback() -> None:
    # The regression test for the shape bug: a fixture in the OTHER mode's
    # (--output-format json) shape must still produce sane non-zero usage via
    # the nested-"tokens" fallback branch, even though our entrypoint never
    # actually emits this shape. thoughts folds into output: 500 + 200 = 700.
    result = gu.extract_model_stats(_NESTED_MODEL_STATS)
    assert result == {"gemini-2.5-pro": (1000, 700)}


def test_usage_and_cost_prices_each_model_at_its_own_rate() -> None:
    stats = {
        "models": {
            # pro: $1.25/$10.00 per 1M
            "gemini-2.5-pro": {"input_tokens": 1_000_000, "output_tokens": 0},
            # flash-lite: $0.10/$0.40 per 1M
            "gemini-2.5-flash-lite": {"input_tokens": 0, "output_tokens": 1_000_000},
        }
    }
    tokens, cost = gu.usage_and_cost(stats)
    assert tokens == 2_000_000  # noqa: PLR2004
    assert cost == pytest.approx(1.25 + 0.40)


def test_usage_and_cost_zero_for_empty_stats() -> None:
    assert gu.usage_and_cost({}) == (0, 0.0)


def test_stats_from_run_log_single_json(tmp_path: Path) -> None:
    log = tmp_path / "run.json"
    log.write_text(_single_json(_FLAT_MODEL_STATS), encoding="utf-8")
    assert gu.stats_from_run_log(log) == _FLAT_MODEL_STATS


def test_stats_from_run_log_stream_json_terminal_result_wins(tmp_path: Path) -> None:
    log = tmp_path / "run.ndjson"
    log.write_text(
        _stream_json(
            [
                {"type": "init"},
                {"type": "message", "data": "hi"},
                {"type": "result", "stats": _FLAT_MODEL_STATS},
            ]
        ),
        encoding="utf-8",
    )
    assert gu.stats_from_run_log(log) == _FLAT_MODEL_STATS


def test_stats_from_run_log_missing_or_empty(tmp_path: Path) -> None:
    assert gu.stats_from_run_log(tmp_path / "absent.json") == {}
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    assert gu.stats_from_run_log(empty) == {}


def test_is_quota_error_detects_terminal_and_retryable(tmp_path: Path) -> None:
    terminal = tmp_path / "terminal.json"
    terminal.write_text(
        _single_json({}).replace(
            '"error": null', '"error": {"type": "TerminalQuotaError"}'
        ),
        encoding="utf-8",
    )
    assert gu.is_quota_error(terminal) is True

    retryable = tmp_path / "retryable.ndjson"
    retryable.write_text(
        _stream_json([{"type": "error", "error": {"type": "RetryableQuotaError"}}]),
        encoding="utf-8",
    )
    assert gu.is_quota_error(retryable) is True


def test_is_quota_error_false_for_unrelated_error(tmp_path: Path) -> None:
    log = tmp_path / "run.ndjson"
    log.write_text(
        _stream_json([{"type": "error", "error": {"type": "SomeOtherError"}}]),
        encoding="utf-8",
    )
    assert gu.is_quota_error(log) is False
    assert gu.is_quota_error(tmp_path / "absent.ndjson") is False


def test_classify_exit_code_auth_passes_through(tmp_path: Path) -> None:
    # 41 is returned unchanged regardless of what the log carries.
    log = tmp_path / "run.json"
    log.write_text(_single_json({}), encoding="utf-8")
    assert gu.classify_exit_code(41, log) == 41  # noqa: PLR2004


def test_classify_exit_code_remaps_quota_to_75(tmp_path: Path) -> None:
    log = tmp_path / "run.ndjson"
    log.write_text(
        _stream_json([{"type": "error", "error": {"type": "TerminalQuotaError"}}]),
        encoding="utf-8",
    )
    assert gu.classify_exit_code(1, log) == 75  # noqa: PLR2004


def test_classify_exit_code_passes_through_other_codes(tmp_path: Path) -> None:
    log = tmp_path / "run.json"
    log.write_text(_single_json({}), encoding="utf-8")
    for code in (0, 42, 52, 53, 54, 130):
        assert gu.classify_exit_code(code, log) == code


def test_capture_run_usage_writes_usage_json(tmp_path: Path) -> None:
    log = tmp_path / "run.ndjson"
    log.write_text(
        _stream_json([{"type": "result", "stats": _FLAT_MODEL_STATS}]),
        encoding="utf-8",
    )
    out = tmp_path / "usage.json"
    tokens = gu.capture_run_usage(
        run_log=log, fallback_model="gemini-2.5-pro", out_path=out
    )
    assert tokens == 1500  # noqa: PLR2004 — 1000 input + 500 output
    data = json.loads(out.read_text())
    assert data["model"] == "gemini-2.5-pro"
    assert data["total_tokens"] == 1500  # noqa: PLR2004
    assert data["cost_usd"] > 0.0


def test_capture_run_usage_zero_when_log_absent(tmp_path: Path) -> None:
    out = tmp_path / "usage.json"
    tokens = gu.capture_run_usage(
        run_log=tmp_path / "absent.ndjson",
        fallback_model="gemini-2.5-pro",
        out_path=out,
    )
    assert tokens == 0
    assert json.loads(out.read_text())["total_tokens"] == 0


def test_main_writes_usage_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "run.ndjson"
    log.write_text(
        _stream_json([{"type": "result", "stats": _FLAT_MODEL_STATS}]),
        encoding="utf-8",
    )
    out = tmp_path / "usage.json"
    monkeypatch.setattr(gu, "USAGE_OUT_PATH", out)
    monkeypatch.setenv("ROBOCO_GEMINI_RUN_LOG", str(log))
    monkeypatch.setenv("ROBOCO_AGENT_MODEL", "gemini-2.5-pro")
    assert gu.main([]) == 0
    assert json.loads(out.read_text())["total_tokens"] == 1500  # noqa: PLR2004


def test_main_classify_exit_prints_remapped_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log = tmp_path / "run.ndjson"
    log.write_text(
        _stream_json([{"type": "error", "error": {"type": "RetryableQuotaError"}}]),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROBOCO_GEMINI_RUN_LOG", str(log))
    monkeypatch.setenv("ROBOCO_GEMINI_CLI_EXIT_CODE", "1")
    assert gu.main(["--classify-exit"]) == 0
    assert capsys.readouterr().out.strip() == "75"
