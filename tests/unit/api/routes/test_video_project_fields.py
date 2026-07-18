"""``roboco/api/routes/video.py`` response-builder wiring for project_slug/
project_name. The sa_inspect(task).unloaded guard branches themselves are
covered once on the shared helper in tests/unit/api/schemas/test_project_fields.py
— this only asserts the three builders actually populate the response from it
(loaded case; a real ORM task always resolves the "loaded" branch since
``project`` is lazy="joined")."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from roboco.api.routes.video import (
    _to_history_response,
    _to_pipeline_item,
    _to_response,
)


def _stub_task(*, with_project: bool = False) -> Any:
    """A TaskTable stand-in matching the three response builders' reads."""
    return SimpleNamespace(
        id="task-1",
        source="video_post",
        title="Video post: release 1.0.0",
        status="pending",
        pr_number=None,
        orchestration_markers=None,
        project=(
            SimpleNamespace(slug="acme-robotics", name="Acme Robotics")
            if with_project
            else None
        ),
        updated_at=None,
        created_at="2026-07-18T00:00:00+00:00",
    )


def _loaded_inspector() -> MagicMock:
    inspector = MagicMock()
    inspector.unloaded = set()
    return inspector


def test_to_response_includes_project_fields_when_loaded() -> None:
    with patch(
        "roboco.api.schemas.project_fields.sa_inspect",
        return_value=_loaded_inspector(),
    ):
        resp = _to_response(_stub_task(with_project=True))
    assert resp.project_slug == "acme-robotics"
    assert resp.project_name == "Acme Robotics"


def test_to_pipeline_item_includes_project_fields_when_loaded() -> None:
    with patch(
        "roboco.api.schemas.project_fields.sa_inspect",
        return_value=_loaded_inspector(),
    ):
        resp = _to_pipeline_item(_stub_task(with_project=True))
    assert resp.project_slug == "acme-robotics"
    assert resp.project_name == "Acme Robotics"


def test_to_pipeline_item_omits_project_fields_when_project_unset() -> None:
    with patch(
        "roboco.api.schemas.project_fields.sa_inspect",
        return_value=_loaded_inspector(),
    ):
        resp = _to_pipeline_item(_stub_task(with_project=False))
    assert resp.project_slug is None
    assert resp.project_name is None


def test_to_history_response_includes_project_fields_when_loaded() -> None:
    with patch(
        "roboco.api.schemas.project_fields.sa_inspect",
        return_value=_loaded_inspector(),
    ):
        resp = _to_history_response(_stub_task(with_project=True))
    assert resp.project_slug == "acme-robotics"
    assert resp.project_name == "Acme Robotics"


def test_to_history_response_omits_project_fields_when_project_unset() -> None:
    with patch(
        "roboco.api.schemas.project_fields.sa_inspect",
        return_value=_loaded_inspector(),
    ):
        resp = _to_history_response(_stub_task(with_project=False))
    assert resp.project_slug is None
    assert resp.project_name is None
