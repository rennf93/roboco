"""Per-task and per-project cost budgets are gated by a default-off config
flag, registered as a panel-tunable feature flag (mirrors possibilities_matrix)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting


def test_task_budgets_disabled_by_default() -> None:
    assert Settings().task_budgets_enabled is False


def test_task_budgets_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_TASK_BUDGETS_ENABLED": "true"}):
        assert Settings().task_budgets_enabled is True


def test_task_budgets_flag_registered_in_feature_flags() -> None:
    assert "task_budgets_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_task_budgets_flag_validates_as_bool() -> None:
    validate_setting("task_budgets_enabled", "true")
