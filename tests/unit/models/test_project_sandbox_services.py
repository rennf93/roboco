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


def _project(
    sandbox_services: list[str] | None = None,
    sandbox_extensions: dict[str, list[str]] | None = None,
) -> Project:
    return Project(
        name="P",
        slug="p",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=uuid4(),
        sandbox_services=sandbox_services,
        sandbox_extensions=sandbox_extensions,
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


# ---------------------------------------------------------------------------
# sandbox_extensions — per-service allowlist-validated extension/module map.
# The allowlist is the security containment: a plpython3u (superuser-RCE) must
# be rejected at the model boundary, never persisted.
# ---------------------------------------------------------------------------


def test_project_accepts_valid_sandbox_extensions() -> None:
    project = _project(sandbox_extensions={"postgres": ["vector", "postgis"]})
    assert project.sandbox_extensions == {"postgres": ["postgis", "vector"]}


def test_project_sandbox_extensions_normalizes_order_and_dedupes() -> None:
    project = _project(
        sandbox_extensions={"postgres": ["postgis", "vector", "postgis"]}
    )
    assert project.sandbox_extensions == {"postgres": ["postgis", "vector"]}


def test_project_sandbox_extensions_defaults_to_none() -> None:
    assert _project().sandbox_extensions is None


def test_project_sandbox_extensions_rejects_plpython() -> None:
    """plpython3u is a superuser-RCE vector — the allowlist rejects it."""
    with pytest.raises(ValidationError):
        _project(sandbox_extensions={"postgres": ["plpython3u"]})


def test_project_sandbox_extensions_rejects_unallowed_redis_module() -> None:
    with pytest.raises(ValidationError):
        _project(sandbox_extensions={"redis": ["not_a_module"]})


def test_project_sandbox_extensions_rejects_feature_for_unknown_service() -> None:
    with pytest.raises(ValidationError):
        _project(sandbox_extensions={"mysql": ["vector"]})


def test_project_sandbox_extensions_drops_empty_feature_list() -> None:
    """A service with an empty feature list is bare — dropped, not stored."""
    project = _project(sandbox_extensions={"postgres": []})
    assert project.sandbox_extensions is None


def test_project_sandbox_extensions_drops_bare_keeps_others() -> None:
    project = _project(sandbox_extensions={"postgres": [], "redis": ["search"]})
    assert project.sandbox_extensions == {"redis": ["search"]}


def test_project_update_accepts_valid_sandbox_extensions() -> None:
    update = ProjectUpdate(sandbox_extensions={"redis": ["json", "bloom"]})
    assert update.sandbox_extensions == {"redis": ["bloom", "json"]}


def test_project_update_rejects_plpython() -> None:
    with pytest.raises(ValidationError):
        ProjectUpdate(sandbox_extensions={"postgres": ["plpython3u"]})
