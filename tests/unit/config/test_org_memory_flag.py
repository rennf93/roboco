"""The org-memory loop is gated by default-off config flags (mirrors self-heal)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_TOP_K = 3
_DEFAULT_MIN_SCORE = 0.6


def test_org_memory_disabled_by_default() -> None:
    s = Settings()
    assert s.org_memory_enabled is False
    assert s.org_memory_top_k == _DEFAULT_TOP_K
    assert s.org_memory_min_score == _DEFAULT_MIN_SCORE


def test_org_memory_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_ORG_MEMORY_ENABLED": "true"}):
        assert Settings().org_memory_enabled is True


def test_org_memory_top_k_reads_env_var() -> None:
    override = 5
    with mock.patch.dict(os.environ, {"ROBOCO_ORG_MEMORY_TOP_K": str(override)}):
        assert Settings().org_memory_top_k == override


def test_org_memory_flag_registered_in_feature_flags() -> None:
    assert "org_memory_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_org_memory_flag_validates_as_bool() -> None:
    validate_setting("org_memory_enabled", "true")
