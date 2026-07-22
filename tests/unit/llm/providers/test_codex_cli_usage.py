"""codex_cli_usage — sum real input/output/cache usage across ``turn.completed``
events in a captured ``codex exec --json`` JSONL log."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import codex_cli_usage as cu

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _turn_completed(
    *,
    input_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
    output_tokens: int,
    reasoning_output_tokens: int = 0,
) -> str:
    return json.dumps(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cache_write_input_tokens": cache_write_input_tokens,
                "output_tokens": output_tokens,
                "reasoning_output_tokens": reasoning_output_tokens,
            },
        }
    )


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_aggregate_sums_across_multiple_turn_completed_events(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            json.dumps({"type": "thread.started"}),
            json.dumps({"type": "turn.started"}),
            _turn_completed(
                input_tokens=1000, cached_input_tokens=200, output_tokens=100
            ),
            json.dumps({"type": "item.completed", "item": {"type": "command"}}),
            _turn_completed(
                input_tokens=500,
                cached_input_tokens=100,
                cache_write_input_tokens=50,
                output_tokens=80,
                reasoning_output_tokens=20,
            ),
        ],
    )
    agg = cu.aggregate_usage_from_jsonl(log)
    assert agg["input_tokens"] == 1500  # noqa: PLR2004
    assert agg["cached_input_tokens"] == 300  # noqa: PLR2004
    assert agg["cache_write_input_tokens"] == 50  # noqa: PLR2004
    assert agg["output_tokens"] == 180  # noqa: PLR2004
    assert agg["reasoning_output_tokens"] == 20  # noqa: PLR2004
    assert agg["turns"] == 2  # noqa: PLR2004


def test_aggregate_ignores_turn_failed_and_bad_lines(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            "not json",
            json.dumps({"type": "turn.failed", "error": {"message": "boom"}}),
            _turn_completed(input_tokens=10, output_tokens=5),
        ],
    )
    agg = cu.aggregate_usage_from_jsonl(log)
    assert agg["input_tokens"] == 10  # noqa: PLR2004
    assert agg["turns"] == 1


def test_aggregate_zero_for_missing_or_empty_log(tmp_path: Path) -> None:
    agg = cu.aggregate_usage_from_jsonl(tmp_path / "nope.jsonl")
    assert agg["turns"] == 0
    assert all(v == 0 for k, v in agg.items() if k != "turns")


def test_usage_and_cost_treats_cached_as_subset_of_input() -> None:
    # cached_input_tokens is a SUBSET of input_tokens (not additional) — the
    # "fresh" input priced at the full rate is the difference.
    agg = {
        "input_tokens": 1000,
        "cached_input_tokens": 300,
        "cache_write_input_tokens": 0,
        "output_tokens": 200,
        "reasoning_output_tokens": 50,
    }
    tin, tout, cr, cw, cost = cu.usage_and_cost("gpt-5.3-codex", agg)
    assert tin == 700  # 1000 - 300  # noqa: PLR2004
    assert tout == 250  # output + reasoning folded in  # noqa: PLR2004
    assert cr == 300  # noqa: PLR2004
    assert cw == 0
    assert cost > 0.0


def test_usage_and_cost_never_goes_negative_when_cached_exceeds_input() -> None:
    agg = {
        "input_tokens": 10,
        "cached_input_tokens": 50,  # malformed/inconsistent upstream data
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    tin, *_rest = cu.usage_and_cost("gpt-5.3-codex", agg)
    assert tin == 0


def test_capture_run_usage_writes_usage_json(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_turn_completed(input_tokens=100, output_tokens=50)])
    out = tmp_path / "usage.json"
    tokens = cu.capture_run_usage(run_log=log, model="gpt-5.3-codex", out_path=out)
    assert tokens == (100, 50, 0, 0)
    data = json.loads(out.read_text())
    assert data["model"] == "gpt-5.3-codex"
    assert data["tokens_input"] == 100  # noqa: PLR2004
    assert data["tokens_output"] == 50  # noqa: PLR2004
    assert data["turns"] == 1
    assert data["cost_usd"] > 0.0


def test_main_writes_usage_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_turn_completed(input_tokens=200, output_tokens=100)])
    out = tmp_path / "usage.json"
    monkeypatch.setattr(cu, "USAGE_OUT_PATH", out)
    monkeypatch.setenv("ROBOCO_CODEX_RUN_LOG", str(log))
    monkeypatch.setenv("ROBOCO_AGENT_MODEL", "gpt-5.3-codex")
    assert cu.main() == 0
    data = json.loads(out.read_text())
    assert data["tokens_input"] == 200  # noqa: PLR2004
    assert data["tokens_output"] == 100  # noqa: PLR2004


def test_main_warns_when_run_log_env_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("ROBOCO_CODEX_RUN_LOG", raising=False)
    with caplog.at_level("WARNING", logger="roboco.llm.providers.codex_cli_usage"):
        assert cu.main() == 0
    assert any("ROBOCO_CODEX_RUN_LOG" in r.message for r in caplog.records)
