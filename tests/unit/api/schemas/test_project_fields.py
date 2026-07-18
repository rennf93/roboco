"""``task_project_fields`` — the shared (project_slug, project_name)
response-builder helper the X and video routes' five builders all call.
Mirrors tests/unit/api/test_schemas_tasks.py's task_to_response project_slug
coverage for the same sa_inspect(task).unloaded guard convention."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from roboco.api.schemas.project_fields import task_project_fields


def _stub_task(*, with_project: bool = False) -> Any:
    return SimpleNamespace(
        project=(
            SimpleNamespace(slug="acme-robotics", name="Acme Robotics")
            if with_project
            else None
        ),
    )


def test_omits_fields_when_project_unloaded() -> None:
    stub = _stub_task(with_project=False)
    fake_inspector = MagicMock()
    fake_inspector.unloaded = {"project"}
    with patch(
        "roboco.api.schemas.project_fields.sa_inspect", return_value=fake_inspector
    ):
        assert task_project_fields(stub) == (None, None)


def test_omits_fields_when_project_id_unset() -> None:
    stub = _stub_task(with_project=False)
    fake_inspector = MagicMock()
    fake_inspector.unloaded = set()  # loaded, but task.project is None
    with patch(
        "roboco.api.schemas.project_fields.sa_inspect", return_value=fake_inspector
    ):
        assert task_project_fields(stub) == (None, None)


def test_returns_slug_and_name_when_loaded() -> None:
    stub = _stub_task(with_project=True)
    fake_inspector = MagicMock()
    fake_inspector.unloaded = set()
    with patch(
        "roboco.api.schemas.project_fields.sa_inspect", return_value=fake_inspector
    ):
        assert task_project_fields(stub) == ("acme-robotics", "Acme Robotics")
