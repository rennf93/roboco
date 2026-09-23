"""Stage-1 devops board-routing: a materializable item's optional
``target_agent`` hint pre-assigns the materialized delivery task to devops-1,
ONLY while ROBOCO_DEVOPS_ENABLED is on.

Flag off (or an unknown slug) => the hint is ignored and every program
materializer lands the task on the Main PM exactly as before, byte-for-byte
default assignment. The hint is also strictly scoped to the one devops slug:
a typo'd or hostile payload cannot redirect materialization elsewhere.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.models.base import TaskStatus, Team
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services import project as project_mod
from roboco.services import prompter as prompter_mod
from roboco.services import roadmap_service as roadmap_module
from roboco.services.board_programs import resolve_materialize_target
from roboco.services.prompter import BatchPlacement
from roboco.services.task import _ROLE_CLAIM_STATUSES, TaskService

if TYPE_CHECKING:
    from roboco.db.tables import AgentTable, TaskTable

DEVOPS_UUID = AGENT_UUIDS["devops-1"]
MAIN_PM_UUID = AGENT_UUIDS["main-pm"]
CEO_BY = _foundation.AGENTS["ceo"].uuid


def test_resolve_target_flag_off_ignores_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", False)
    assert resolve_materialize_target({"target_agent": "devops-1"}) is None


def test_resolve_target_flag_on_honors_devops_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    assert resolve_materialize_target({"target_agent": "devops-1"}) == "devops-1"


def test_resolve_target_ignores_unknown_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    # Not a real slug, and not the devops slug even if it were.
    assert resolve_materialize_target({"target_agent": "main-pm"}) is None
    assert resolve_materialize_target({"target_agent": "no-such-agent"}) is None
    assert resolve_materialize_target({}) is None


def _materialize_item(target: dict[str, Any] | None = None) -> dict[str, Any]:
    item = {
        "id": "item-0",
        "title": "Harden the compose topology",
        "description": "Split the data-plane network off the default bridge.",
        "acceptance_criteria": ["networks declared"],
        "project_slug": "backend-svc",
        "team": "backend",
        "priority": 2,
        "rationale": "infra-shaped item",
        "status": "proposed",
    }
    if target:
        item.update(target)
    return item


def _patched_materialize_env(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub the project + prompter services _materialize resolves locally and
    capture the create_task_from_draft kwargs."""
    project = MagicMock(id="project-uuid", board_programs=None)

    project_service = MagicMock()
    project_service.get_by_slug = AsyncMock(return_value=project)

    prompter = MagicMock()
    create = AsyncMock(return_value=MagicMock(id="task-uuid"))
    prompter.create_task_from_draft = create

    # _materialize imports these helpers function-locally, so patch them on
    # their source modules (the local import resolves at call time).
    monkeypatch.setattr(
        prompter_mod, "get_prompter_service", MagicMock(return_value=prompter)
    )
    monkeypatch.setattr(
        project_mod, "get_project_service", MagicMock(return_value=project_service)
    )
    return create


@pytest.mark.asyncio
async def test_roadmap_materialize_flag_on_preassigns_devops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Armed: the hint routes the item to devops-1 as a NORMAL delivery task
    : no Main-PM team_override, or the dev dispatcher would never see it."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    create = _patched_materialize_env(monkeypatch)
    svc = roadmap_module.RoadmapService(MagicMock())
    await svc._materialize(
        _materialize_item({"target_agent": "devops-1"}), created_by=CEO_BY
    )
    create.assert_awaited_once()
    call = create.await_args
    assert call is not None
    kwargs = call.kwargs
    assert kwargs["assigned_to"] == UUID(DEVOPS_UUID)
    assert kwargs.get("placement") is None


@pytest.mark.asyncio
async def test_roadmap_materialize_flag_off_keeps_main_pm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off: the same payload materializes byte-for-byte as today:
    Main-PM-owned coordination root."""
    monkeypatch.setattr(settings, "devops_enabled", False)
    create = _patched_materialize_env(monkeypatch)
    svc = roadmap_module.RoadmapService(MagicMock())
    await svc._materialize(
        _materialize_item({"target_agent": "devops-1"}), created_by=CEO_BY
    )
    create.assert_awaited_once()
    call = create.await_args
    assert call is not None
    kwargs = call.kwargs
    assert kwargs["assigned_to"] == UUID(MAIN_PM_UUID)
    assert kwargs["placement"] == BatchPlacement(team_override=Team.MAIN_PM)


@pytest.mark.asyncio
async def test_roadmap_materialize_unknown_slug_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    create = _patched_materialize_env(monkeypatch)
    svc = roadmap_module.RoadmapService(MagicMock())
    await svc._materialize(
        _materialize_item({"target_agent": "not-a-real-agent"}), created_by=CEO_BY
    )
    call = create.await_args
    assert call is not None
    assert call.kwargs["assigned_to"] == UUID(MAIN_PM_UUID)


# ---------------------------------------------------------------------------
# Service-layer claim parity: the floating devops author (team=board) claims
# its assigned cell-team task only while the flag is armed.
# ---------------------------------------------------------------------------


def test_claim_team_devops_exemption_flag_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _validate_claim_team is a pure predicate, so lightweight role/team
    # stand-ins suffice; cast keeps them type-clean (no DB rows).
    task = cast("TaskTable", SimpleNamespace(team=Team.BACKEND))
    agent = cast(
        "AgentTable",
        SimpleNamespace(role=SimpleNamespace(value="devops"), team=Team.BOARD),
    )
    svc = TaskService(MagicMock())

    monkeypatch.setattr(settings, "devops_enabled", False)
    assert svc._validate_claim_team(task, agent) == "agent not in task's team"

    monkeypatch.setattr(settings, "devops_enabled", True)
    assert svc._validate_claim_team(task, agent) is None


def test_devops_runtime_claim_statuses_mirror_spec_author_edges() -> None:
    """The runtime claim table carries the devops author edges (PENDING +
    NEEDS_REVISION); its third spec edge (AWAITING_PR_REVIEW) routes through
    pr_gate_claim, which never consults this map."""
    assert _ROLE_CLAIM_STATUSES["devops"] == {
        TaskStatus.PENDING,
        TaskStatus.NEEDS_REVISION,
    }
