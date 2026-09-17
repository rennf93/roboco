"""Unit tests for roboco.llm.providers.hummin_cli_usage."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from roboco.llm.providers import hummin_cli_usage

if TYPE_CHECKING:
    from pathlib import Path


def _assistant_end(
    *,
    tin: int,
    tout: int,
    cr: int = 0,
    cw: int = 0,
    text: str = "",
) -> str:
    content = [{"type": "text", "text": text}] if text else []
    return json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": content,
                "stopReason": "stop",
                "usage": {
                    "input": tin,
                    "output": tout,
                    "cacheRead": cr,
                    "cacheWrite": cw,
                    "totalTokens": tin + tout + cr + cw,
                },
            },
        }
    )


def _write_log(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_aggregate_sums_all_assistant_message_end_events(tmp_path: Path) -> None:
    # Spec §2: task totals are the SUM of usage across ALL assistant
    # message_end events (a tool loop produces several); turns = the count.
    run_log = _write_log(
        tmp_path / "run.jsonl",
        [
            json.dumps({"type": "session", "version": 3, "id": "s1"}),
            json.dumps({"type": "agent_start"}),
            _assistant_end(tin=100, tout=10, cr=5, cw=2, text="step one"),
            json.dumps({"type": "message_end", "message": {"role": "user"}}),
            _assistant_end(tin=200, tout=20, cr=15, cw=3, text="step two"),
            json.dumps({"type": "agent_end"}),
        ],
    )
    agg = hummin_cli_usage.aggregate_usage(run_log)
    assert agg["tokens_input"] == 300  # noqa: PLR2004
    assert agg["tokens_output"] == 30  # noqa: PLR2004
    assert agg["tokens_cache_read"] == 20  # noqa: PLR2004
    assert agg["tokens_cache_write"] == 5  # noqa: PLR2004
    assert agg["turns"] == 2  # noqa: PLR2004
    # The final assistant text is the LAST assistant message_end's text.
    assert agg["result_text"] == "step two"


def test_aggregate_tolerates_missing_or_garbled_log(tmp_path: Path) -> None:
    missing = tmp_path / "nope.jsonl"
    assert hummin_cli_usage.aggregate_usage(missing)["turns"] == 0
    garbled = _write_log(tmp_path / "bad.jsonl", ["not json", "[]", "{}"])
    agg = hummin_cli_usage.aggregate_usage(garbled)
    assert agg["tokens_input"] == 0
    assert agg["turns"] == 0


def test_capture_run_usage_writes_contract(tmp_path: Path) -> None:
    run_log = _write_log(
        tmp_path / "run.jsonl",
        [_assistant_end(tin=1_000_000, tout=100, cr=50)],
    )
    out = tmp_path / "usage.json"
    tin, tout, cr, cw = hummin_cli_usage.capture_run_usage(
        run_log=run_log, model="glm-5.3", out_path=out
    )
    assert (tin, tout, cr, cw) == (1_000_000, 100, 50, 0)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["model"] == "glm-5.3"
    assert data["tokens_input"] == 1_000_000  # noqa: PLR2004
    assert data["tokens_output"] == 100  # noqa: PLR2004
    assert data["tokens_cache_read"] == 50  # noqa: PLR2004
    assert data["tokens_cache_write"] == 0
    assert data["turns"] == 1
    # glm-5.3: $1.40/1M in, $4.40/1M out, $0.26/1M cache-read (the static
    # _PRICING table).
    assert data["cost_usd"] == pytest.approx(1.40 + 100 / 1e6 * 4.40 + 50 / 1e6 * 0.26)


def test_capture_run_usage_best_effort_on_io_error(tmp_path: Path) -> None:
    unreadable = tmp_path / "dir"
    unreadable.mkdir()
    assert hummin_cli_usage.capture_run_usage(
        run_log=unreadable / "usage.json",
        model="glm-5.3",
        out_path=unreadable / "out" / "usage.json",
    ) == (0, 0, 0, 0)


def test_main_warns_without_run_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ROBOCO_HUMMIN_RUN_LOG", raising=False)
    with caplog.at_level("WARNING"):
        assert hummin_cli_usage.main() == 0
    assert any("ROBOCO_HUMMIN_RUN_LOG" in r.message for r in caplog.records)
