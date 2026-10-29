"""Entire session checkpoints and Entire trails for the fleet are gated by
default-off flags."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS


def test_entire_disabled_by_default() -> None:
    assert Settings().entire_enabled is False


def test_entire_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_ENTIRE_ENABLED": "true"}):
        assert Settings().entire_enabled is True


def test_entire_flag_is_registered_on_the_panel_card() -> None:
    """The flag must be card-registered so the CEO can arm it from Settings;
    an unregistered flag silently stays env-only forever."""
    assert any(key == "entire_enabled" for key, _label in FEATURE_FLAGS)


def test_entire_trails_disabled_by_default() -> None:
    assert Settings().entire_trails_enabled is False


def test_entire_trails_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_ENTIRE_TRAILS_ENABLED": "true"}):
        assert Settings().entire_trails_enabled is True


def test_entire_trails_flag_is_registered_on_the_panel_card() -> None:
    assert any(key == "entire_trails_enabled" for key, _label in FEATURE_FLAGS)
