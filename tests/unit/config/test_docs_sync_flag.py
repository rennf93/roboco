"""Docs-divergence sync is gated by a default-off config flag."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting


def test_docs_sync_disabled_by_default() -> None:
    s = Settings()
    assert s.docs_sync_enabled is False


def test_docs_sync_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_DOCS_SYNC_ENABLED": "true"}):
        assert Settings().docs_sync_enabled is True


def test_docs_sync_flag_registered_in_feature_flags() -> None:
    assert "docs_sync_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_docs_sync_flag_validates_as_bool() -> None:
    validate_setting("docs_sync_enabled", "true")
