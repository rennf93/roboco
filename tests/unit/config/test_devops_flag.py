"""The DevOps agent subsystem is gated by a default-off flag (Stage 1)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS


def test_devops_disabled_by_default() -> None:
    assert Settings().devops_enabled is False


def test_devops_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_DEVOPS_ENABLED": "true"}):
        assert Settings().devops_enabled is True


def test_devops_flag_is_registered_on_the_panel_card() -> None:
    """The flag must be card-registered so the CEO can arm it from Settings;
    an unregistered flag silently stays env-only forever."""
    assert any(key == "devops_enabled" for key, _label in FEATURE_FLAGS)
