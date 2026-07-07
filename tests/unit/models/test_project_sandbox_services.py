"""Project.sandbox_services / ProjectUpdate.sandbox_services validation.

Recognized services are whatever the engine registry exposes
(``VALID_SANDBOX_SERVICES`` in ``roboco.models.sandbox`` — postgres, redis,
mongo) — an unknown value must be rejected with a clear message rather than
silently accepted and later failing at provision time inside a container spawn.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from roboco.models.base import Team
from roboco.models.project import Project, ProjectUpdate


def _project(sandbox_services: list[str] | None = None) -> Project:
    return Project(
        name="P",
        slug="p",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=uuid4(),
        sandbox_services=sandbox_services,
    )


def test_project_accepts_valid_sandbox_services() -> None:
    project = _project(sandbox_services=["postgres", "redis"])
    assert project.sandbox_services == ["postgres", "redis"]


def test_project_accepts_mongo() -> None:
    project = _project(sandbox_services=["mongo"])
    assert project.sandbox_services == ["mongo"]


def test_project_normalizes_sandbox_services_order_and_dupes() -> None:
    project = _project(sandbox_services=["redis", "postgres", "redis"])
    assert project.sandbox_services == ["postgres", "redis"]


def test_project_defaults_sandbox_services_to_none() -> None:
    project = _project()
    assert project.sandbox_services is None


def test_project_rejects_unknown_sandbox_service() -> None:
    with pytest.raises(ValidationError):
        _project(sandbox_services=["mysql"])


def test_project_update_accepts_valid_sandbox_services() -> None:
    update = ProjectUpdate(sandbox_services=["postgres"])
    assert update.sandbox_services == ["postgres"]


def test_project_update_rejects_unknown_sandbox_service() -> None:
    with pytest.raises(ValidationError):
        ProjectUpdate(sandbox_services=["mysql"])


def test_project_update_accepts_empty_list() -> None:
    update = ProjectUpdate(sandbox_services=[])
    assert update.sandbox_services == []
