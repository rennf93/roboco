"""openrouter_cli_usage — sum step_finish events' part.tokens + part.cost from
the ``--format json`` JSONL stream into the 4-bucket usage.json.

The structural guarantee under test: opencode puts usage inline in each
``step_finish`` event (unlike kimi, which requires a session-dir wire.jsonl
lookup), so the usage reader sums the RUN_LOG directly. The cost comes from
OpenRouter's metered ``cost`` field, NOT the static ``_PRICING`` table (no
OpenRouter rows by design).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import openrouter_cli_usage as ou

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _step_finish(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    cost: float = 0.0,
) -> str:
    return json.dumps(
        {
            "type": "step_finish",
            "part": {
                "type": "step-finish",
                "tokens": {
                    "total": input_tokens + output_tokens + reasoning + cache_read,
                    "input": input_tokens,
                    "output": output_tokens,
                    "reasoning": reasoning,
                    "cache": {"write": cache_write, "read": cache_read},
                },
                "cost": cost,
            },
        }
    )


# ---------------------------------------------------------------------------
# _tokens_from_step_finish
# ---------------------------------------------------------------------------


def test_tokens_from_step_finish_extracts_all_five_fields() -> None:
    event = json.loads(
        _step_finish(
            input_tokens=100,
            output_tokens=50,
            reasoning=10,
            cache_read=20,
            cache_write=5,
        )
    )
    tokens = ou._tokens_from_step_finish(event)
    assert tokens is not None
    assert tokens["input"] == 100
    assert tokens["output"] == 50
    assert tokens["reasoning"] == 10
    assert tokens["cache_read"] == 20
    assert tokens["cache_write"] == 5


def test_tokens_from_step_finish_returns_none_for_non_step_finish() -> None:
    event = {"type": "text", "part": {"text": "hello"}}
    assert ou._tokens_from_step_finish(event) is None
    assert ou._tokens_from_step_finish({"type": "error", "error": {}}) is None


def test_tokens_from_step_finish_returns_none_when_no_tokens_key() -> None:
    event = {"type": "step_finish", "part": {"type": "step-finish"}}
    assert ou._tokens_from_step_finish(event) is None


def test_tokens_from_step_finish_handles_missing_cache() -> None:
    event = {"type": "step_finish", "part": {"tokens": {"input": 10, "output": 5}}}
    tokens = ou._tokens_from_step_finish(event)
    assert tokens is not None
    assert tokens["input"] == 10
    assert tokens["output"] == 5
    assert tokens["cache_read"] == 0
    assert tokens["cache_write"] == 0


# ---------------------------------------------------------------------------
# _cost_from_step_finish
# ---------------------------------------------------------------------------


def test_cost_from_step_finish_extracts_metered_cost() -> None:
    event = json.loads(_step_finish(cost=0.0123))
    assert ou._cost_from_step_finish(event) == 0.0123


def test_cost_from_step_finish_returns_none_for_non_step_finish() -> None:
    assert ou._cost_from_step_finish({"type": "text"}) is None


def test_cost_from_step_finish_defaults_to_zero() -> None:
    event = {"type": "step_finish", "part": {"tokens": {"input": 1}}}
    assert ou._cost_from_step_finish(event) == 0.0


# ---------------------------------------------------------------------------
# aggregate_usage — the 4-bucket mapping + cost sum
# ---------------------------------------------------------------------------


def test_aggregate_sums_multiple_step_finish_events(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            _step_finish(
                input_tokens=100,
                output_tokens=50,
                reasoning=10,
                cache_read=20,
                cache_write=5,
                cost=0.01,
            ),
            json.dumps({"type": "text", "part": {"text": "ignored"}}),
            _step_finish(
                input_tokens=200,
                output_tokens=80,
                reasoning=20,
                cache_read=40,
                cache_write=10,
                cost=0.02,
            ),
        ],
    )
    agg = ou.aggregate_usage(log)
    # 4-bucket mapping: output includes reasoning, cache is split read/write.
    assert agg["input"] == 300
    assert agg["output"] == 160  # (50+80) + (10+20) — reasoning folded into output
    assert agg["cache_read"] == 60
    assert agg["cache_write"] == 15
    assert agg["turns"] == 2
    assert agg["cost_usd"] == 0.03


def test_aggregate_ignores_non_step_finish_and_bad_lines(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            "not json",
            json.dumps({"type": "text", "part": {"text": "hello"}}),
            json.dumps({"type": "error", "error": {"message": "oops"}}),
            _step_finish(input_tokens=10, output_tokens=5),
        ],
    )
    agg = ou.aggregate_usage(log)
    assert agg["input"] == 10
    assert agg["output"] == 5
    assert agg["turns"] == 1
    assert agg["cost_usd"] == 0.0


def test_aggregate_zero_for_missing_log(tmp_path: Path) -> None:
    agg = ou.aggregate_usage(tmp_path / "nope.jsonl")
    assert agg["turns"] == 0
    assert agg["input"] == 0
    assert agg["output"] == 0
    assert agg["cache_read"] == 0
    assert agg["cache_write"] == 0
    assert agg["cost_usd"] == 0.0


def test_aggregate_empty_log(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    log.write_text("", encoding="utf-8")
    agg = ou.aggregate_usage(log)
    assert agg["turns"] == 0


# ---------------------------------------------------------------------------
# capture_run_usage / main
# ---------------------------------------------------------------------------


def test_capture_run_usage_writes_usage_json(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            _step_finish(
                input_tokens=100,
                output_tokens=50,
                cache_read=10,
                cost=0.015,
            ),
        ],
    )
    out = tmp_path / "usage.json"
    tokens = ou.capture_run_usage(
        run_log=log,
        model="anthropic/claude-sonnet-4",
        out_path=out,
    )
    assert tokens == (100, 50, 10, 0)
    data = json.loads(out.read_text())
    assert data["model"] == "anthropic/claude-sonnet-4"
    assert data["tokens_input"] == 100
    assert data["tokens_output"] == 50
    assert data["tokens_cache_read"] == 10
    assert data["tokens_cache_write"] == 0
    assert data["cost_usd"] == 0.015
    assert data["turns"] == 1


def test_capture_run_usage_zero_when_log_missing(tmp_path: Path) -> None:
    out = tmp_path / "usage.json"
    tokens = ou.capture_run_usage(
        run_log=tmp_path / "nope.jsonl",
        model="m",
        out_path=out,
    )
    assert tokens == (0, 0, 0, 0)
    # Best-effort: writes nothing on IO failure.
    assert not out.exists()


def test_main_writes_usage_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [_step_finish(input_tokens=200, output_tokens=100, cost=0.02)],
    )
    out = tmp_path / "usage.json"
    monkeypatch.setattr(ou, "USAGE_OUT_PATH", out)
    monkeypatch.setenv("ROBOCO_OPENROUTER_RUN_LOG", str(log))
    monkeypatch.setenv("ROBOCO_AGENT_MODEL", "anthropic/claude-sonnet-4")

    assert ou.main() == 0
    data = json.loads(out.read_text())
    assert data["tokens_input"] == 200
    assert data["tokens_output"] == 100
    assert data["cost_usd"] == 0.02


def test_main_warns_when_run_log_env_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("ROBOCO_OPENROUTER_RUN_LOG", raising=False)
    with caplog.at_level("WARNING", logger="roboco.llm.providers.openrouter_cli_usage"):
        assert ou.main() == 0
    assert any("ROBOCO_OPENROUTER_RUN_LOG" in r.message for r in caplog.records)
