"""timed_phase emits one verb_phase event and never swallows errors."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from roboco.utils import timing


def test_fast_phase_logs_info_with_duration() -> None:
    log = MagicMock()
    with (
        patch.object(timing, "logger", log),
        timing.timed_phase("open_pr", "push", task_id="t1"),
    ):
        pass
    log.info.assert_called_once()
    args, kwargs = log.info.call_args
    assert args == ("verb_phase",)
    assert kwargs["verb"] == "open_pr"
    assert kwargs["phase"] == "push"
    assert kwargs["outcome"] == "ok"
    assert kwargs["task_id"] == "t1"
    assert kwargs["duration_ms"] >= 0
    log.warning.assert_not_called()


def test_slow_phase_logs_warning() -> None:
    log = MagicMock()
    with (
        patch.object(timing, "logger", log),
        patch.object(timing, "SLOW_PHASE_MS", -1.0),
        timing.timed_phase("i_am_done", "gate"),
    ):
        pass
    log.warning.assert_called_once()
    log.info.assert_not_called()


def test_error_is_recorded_and_reraised() -> None:
    log = MagicMock()
    with (
        patch.object(timing, "logger", log),
        pytest.raises(ValueError, match="boom"),
        timing.timed_phase("open_pr", "forge"),
    ):
        raise ValueError("boom")
    assert log.info.call_args.kwargs["outcome"] == "error"
