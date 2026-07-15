"""Possibilities matrix (work-already-done fast path) is gated by a default-off
config flag, registered as a panel-tunable feature flag (the #487 lesson)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting


def test_possibilities_matrix_disabled_by_default() -> None:
    assert Settings().possibilities_matrix_enabled is False


def test_possibilities_matrix_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_POSSIBILITIES_MATRIX_ENABLED": "true"}):
        assert Settings().possibilities_matrix_enabled is True


def test_possibilities_matrix_flag_registered_in_feature_flags() -> None:
    assert "possibilities_matrix_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_possibilities_matrix_flag_validates_as_bool() -> None:
    validate_setting("possibilities_matrix_enabled", "true")
