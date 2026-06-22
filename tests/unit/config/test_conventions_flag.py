"""The architectural-conventions subsystem is gated by a default-off flag."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings


def test_conventions_disabled_by_default() -> None:
    assert Settings().conventions_enabled is False


def test_conventions_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_CONVENTIONS_ENABLED": "true"}):
        assert Settings().conventions_enabled is True
