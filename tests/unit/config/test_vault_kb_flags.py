"""Vault KB ingest knobs — dirs/interval defaults + the reserved-dir overlap
guard (mirrors the vault-janitor flag test idiom)."""

from __future__ import annotations

import os
from unittest import mock

import pytest
from pydantic import ValidationError
from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_INTERVAL = 900


def test_vault_kb_defaults() -> None:
    s = Settings()
    assert s.vault_kb_enabled is False
    assert s.vault_kb_dirs == "RoboCo/Notes"
    assert s.vault_kb_interval_seconds == _DEFAULT_INTERVAL


def test_vault_kb_interval_rejects_below_minimum() -> None:
    with (
        mock.patch.dict(os.environ, {"ROBOCO_VAULT_KB_INTERVAL_SECONDS": "30"}),
        pytest.raises(ValidationError),
    ):
        Settings()


def test_vault_kb_flag_registered_in_feature_flags() -> None:
    assert "vault_kb_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_vault_kb_flag_validates_as_bool() -> None:
    validate_setting("vault_kb_enabled", "true")


def test_vault_kb_dirs_clean_config_passes() -> None:
    with mock.patch.dict(
        os.environ,
        {"ROBOCO_VAULT_KB_ENABLED": "true", "ROBOCO_VAULT_KB_DIRS": "RoboCo/Notes"},
    ):
        assert Settings().vault_kb_dirs == "RoboCo/Notes"


def test_vault_kb_dirs_overlap_with_intake_dir_rejected() -> None:
    with (
        mock.patch.dict(
            os.environ,
            {
                "ROBOCO_VAULT_KB_ENABLED": "true",
                "ROBOCO_VAULT_KB_DIRS": "RoboCo/Inbox",
            },
        ),
        pytest.raises(ValidationError),
    ):
        Settings()


@pytest.mark.parametrize(
    "kb_dirs",
    [
        "RoboCo/Tasks",
        "RoboCo/Tasks/Sub",  # nests under a reserved dir
        "RoboCo",  # nests OVER every reserved dir (reverse direction)
        ".obsidian",
        "RoboCo/Notes,RoboCo/Journals",  # one clean entry, one reserved
    ],
)
def test_vault_kb_dirs_reserved_overlap_rejected(kb_dirs: str) -> None:
    with (
        mock.patch.dict(
            os.environ,
            {"ROBOCO_VAULT_KB_ENABLED": "true", "ROBOCO_VAULT_KB_DIRS": kb_dirs},
        ),
        pytest.raises(ValidationError),
    ):
        Settings()


@pytest.mark.parametrize(
    "kb_dirs",
    [
        "/etc",  # absolute path
        "/etc/passwd",
        "../sibling_secret_dir",  # leading traversal
        "RoboCo/Notes/..",  # trailing traversal
        "RoboCo/../../outside",  # embedded traversal
        "RoboCo/Notes,../outside",  # one clean entry, one traversal
        ".",  # vault root itself — would rglob every projection dir
        "./",  # vault root, trailing-slash spelling
        "./RoboCo/Tasks",  # dot-prefixed reserved dir must not evade overlap
    ],
)
def test_vault_kb_dirs_traversal_rejected(kb_dirs: str) -> None:
    """Absolute paths and '..' segments would let KB ingest read files
    entirely outside the vault into the fleet-retrievable corpus."""
    with (
        mock.patch.dict(
            os.environ,
            {"ROBOCO_VAULT_KB_ENABLED": "true", "ROBOCO_VAULT_KB_DIRS": kb_dirs},
        ),
        pytest.raises(ValidationError),
    ):
        Settings()


def test_vault_kb_dirs_dot_prefixed_clean_entry_passes() -> None:
    """'./RoboCo/Notes' normalizes to a clean, non-reserved subfolder."""
    with mock.patch.dict(
        os.environ,
        {
            "ROBOCO_VAULT_KB_ENABLED": "true",
            "ROBOCO_VAULT_KB_DIRS": "./RoboCo/Notes",
        },
    ):
        assert Settings().vault_kb_dirs == "./RoboCo/Notes"


def test_vault_kb_dirs_dotted_name_is_not_traversal() -> None:
    """A '..' inside a segment name (not a whole segment) is a legal dir name."""
    with mock.patch.dict(
        os.environ,
        {
            "ROBOCO_VAULT_KB_ENABLED": "true",
            "ROBOCO_VAULT_KB_DIRS": "RoboCo/my..notes",
        },
    ):
        assert Settings().vault_kb_dirs == "RoboCo/my..notes"


def test_vault_kb_disabled_skips_dir_validation() -> None:
    """An invalid vault_kb_dirs is only enforced when vault_kb_enabled — off
    by default, so a stale/misconfigured env value never blocks startup."""
    with mock.patch.dict(
        os.environ,
        {"ROBOCO_VAULT_KB_ENABLED": "false", "ROBOCO_VAULT_KB_DIRS": "RoboCo/Tasks"},
    ):
        assert Settings().vault_kb_dirs == "RoboCo/Tasks"
