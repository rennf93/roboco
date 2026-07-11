"""The Obsidian vault projection is gated by default-off config flags
(mirrors the roadmap engine / video engine idiom)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting


def test_obsidian_vault_disabled_by_default() -> None:
    s = Settings()
    assert s.obsidian_vault_enabled is False
    assert s.vault_path == "/data/vault"


def test_obsidian_vault_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_OBSIDIAN_VAULT_ENABLED": "true"}):
        assert Settings().obsidian_vault_enabled is True


def test_vault_path_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_VAULT_PATH": "/mnt/my-vault"}):
        assert Settings().vault_path == "/mnt/my-vault"


def test_obsidian_vault_flag_registered_in_feature_flags() -> None:
    assert "obsidian_vault_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_obsidian_vault_flag_validates_as_bool() -> None:
    validate_setting("obsidian_vault_enabled", "true")
