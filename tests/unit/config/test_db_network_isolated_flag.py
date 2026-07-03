"""DB network isolation is a compose-coupled config flag, not a panel flag."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS


def test_db_network_isolated_false_by_default() -> None:
    assert Settings().db_network_isolated is False


def test_db_network_isolated_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_DB_NETWORK_ISOLATED": "true"}):
        assert Settings().db_network_isolated is True


def test_db_network_isolated_not_a_panel_feature_flag() -> None:
    """It must travel with the compose networks: topology; a runtime toggle
    cannot change network membership, so exposing it in the panel misleads."""
    assert "db_network_isolated" not in [key for key, _ in FEATURE_FLAGS]
