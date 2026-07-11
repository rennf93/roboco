"""Vault janitor knobs — archival window + weekly-report flag (mirrors the
vault-intake flag test idiom)."""

from __future__ import annotations

import os
from unittest import mock

import pytest
from pydantic import ValidationError
from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_ARCHIVE_DAYS = 30


def test_vault_janitor_defaults() -> None:
    s = Settings()
    assert s.vault_archive_days == _DEFAULT_ARCHIVE_DAYS
    assert s.vault_report_enabled is True


def test_vault_archive_days_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_VAULT_ARCHIVE_DAYS": "0"}):
        assert Settings().vault_archive_days == 0


def test_vault_archive_days_rejects_negative() -> None:
    with (
        mock.patch.dict(os.environ, {"ROBOCO_VAULT_ARCHIVE_DAYS": "-1"}),
        pytest.raises(ValidationError),
    ):
        Settings()


def test_vault_report_flag_registered_in_feature_flags() -> None:
    assert "vault_report_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_vault_report_flag_validates_as_bool() -> None:
    validate_setting("vault_report_enabled", "false")
