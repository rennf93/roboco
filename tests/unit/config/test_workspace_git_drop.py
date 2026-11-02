"""The workspace git drop is an explicit setting, never read off the euid:
off on the host (dev and CI), required in the orchestrator image."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError
from roboco.config import Settings

_DOCKERFILE = Path(__file__).parents[3] / "docker" / "orchestrator.Dockerfile"


def test_the_drop_is_off_by_default() -> None:
    with mock.patch.dict(os.environ):
        os.environ.pop("ROBOCO_WORKSPACE_GIT_DROP", None)
        assert Settings(_env_file=None).workspace_git_drop == "off"


def test_the_drop_reads_its_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_WORKSPACE_GIT_DROP": "required"}):
        assert Settings(_env_file=None).workspace_git_drop == "required"


def test_an_unknown_value_is_refused() -> None:
    with (
        mock.patch.dict(os.environ, {"ROBOCO_WORKSPACE_GIT_DROP": "auto"}),
        pytest.raises(ValidationError),
    ):
        Settings(_env_file=None)


def test_the_orchestrator_image_requires_the_drop() -> None:
    assert "ROBOCO_WORKSPACE_GIT_DROP=required" in _DOCKERFILE.read_text()
