"""Unit tests for PrompterService.

Covers the live-intake draft → task flow (``create_task_from_draft`` /
``confirm_live_draft`` + the enum/priority/team coercion) and the pure
draft/description helpers. DB-backed tests use an in-memory async session via
conftest fixtures.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import (
    AgentTable,
    ProductTable,
    ProjectTable,
    TaskTable,
)
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.base import ServiceError
from roboco.services.prompter import (
    PrompterService,
    compose_description,
    derive_scale,
    get_prompter_service,
    parse_readiness,
)

# =============================================================================
# Pure function tests (no DB)
# =============================================================================


def test_parse_readiness_extracts_and_strips_tag() -> None:
    content = (
        "Here is my question about scope.\n\n"
        '```roboco-meta\n{"covered": ["objective", "scope"], '
        '"ready": true, "scale": "multi"}\n```'
    )
    clean, tag = parse_readiness(content)
    assert clean == "Here is my question about scope."
    assert tag is not None
    assert tag.ready is True
    assert tag.scale == "multi"
    assert tag.covered == ["objective", "scope"]
    # The control block must not leak into the user-visible text.
    assert "roboco-meta" not in clean


def test_parse_readiness_absent_block_is_not_ready() -> None:
    clean, tag = parse_readiness("Just a plain reply, no control block.")
    assert clean == "Just a plain reply, no control block."
    assert tag is None


def test_parse_readiness_malformed_json_is_graceful() -> None:
    content = "Reply text.\n```roboco-meta\n{not valid json]\n```"
    clean, tag = parse_readiness(content)
    assert "roboco-meta" not in clean
    assert clean == "Reply text."
    assert tag is None


def test_parse_readiness_uses_last_block() -> None:
    content = (
        '```roboco-meta\n{"ready": false, "scale": "single"}\n```\n'
        "Final answer.\n"
        '```roboco-meta\n{"ready": true, "scale": "multi"}\n```'
    )
    clean, tag = parse_readiness(content)
    assert tag is not None
    assert tag.ready is True
    assert tag.scale == "multi"
    assert "roboco-meta" not in clean


def test_derive_scale_single_vs_multi() -> None:
    assert derive_scale([{"team": "backend"}]) == "single"
    assert derive_scale([{"team": "backend"}, {"team": "frontend"}]) == "multi"
    # Non-cell teams (e.g. main_pm) do not count toward cell breadth.
    assert derive_scale([{"team": "backend"}, {"team": "main_pm"}]) == "single"
    assert derive_scale([]) == "single"


def test_compose_description_single_cell_markdown() -> None:
    draft = {
        "objective": "Let humans track token usage.",
        "what_this_builds": ["A usage panel on the Metrics page"],
        "the_work": [
            {
                "team": "frontend",
                "summary": "Render the usage panel",
                "items": ["Add the chart", "Wire the API"],
            }
        ],
        "notes": ["Reuse the existing Metrics layout"],
        "acceptance_criteria": ["Panel shows totals", "Panel filters by range"],
    }
    md = compose_description(draft)
    assert "## Objective" in md
    assert "## What This Builds" in md
    assert "## The Work" in md
    assert "**Frontend** — Render the usage panel" in md
    assert "## Notes" in md
    assert "## Success Criteria" in md
    assert "- Panel shows totals" in md
    # Single-cell tasks get no board-led lead line.
    assert "Board-led" not in md


def test_compose_description_multi_cell_has_board_led_lead() -> None:
    draft = {
        "objective": "Ship the Prompter.",
        "the_work": [
            {"team": "backend", "summary": "Chat endpoint", "items": []},
            {"team": "frontend", "summary": "Chat UI", "items": []},
            {"team": "ux_ui", "summary": "Interaction design", "items": []},
        ],
        "acceptance_criteria": ["It works end to end"],
    }
    md = compose_description(draft)
    assert "Board-led" in md
    assert "**Backend**" in md
    assert "**UX/UI**" in md


def test_compose_description_falls_back_to_provided_description() -> None:
    # Sparse structured fields → fall back to a model-provided description.
    draft = {"description": "A perfectly adequate fallback description here."}
    md = compose_description(draft)
    assert md == "A perfectly adequate fallback description here."


def test_lead_cell_team_prefers_the_work_cell() -> None:
    draft = {"the_work": [{"team": "frontend"}], "team": "backend"}
    assert PrompterService._lead_cell_team(draft, default=Team.BACKEND) is Team.FRONTEND
    # Empty the_work falls back to the provided default.
    assert PrompterService._lead_cell_team({}, default=Team.BACKEND) is Team.BACKEND


def test_lead_cell_team_skips_invalid_cell_names() -> None:
    # An off-enum cell name is skipped, not raised on; falls through to a valid one.
    draft = {"the_work": [{"team": "nonsense"}, {"team": "frontend"}]}
    assert PrompterService._lead_cell_team(draft, default=Team.BACKEND) is Team.FRONTEND


def test_coerce_draft_enums_defaults_invalid_values() -> None:
    # Regression: the LLM emits off-enum values (e.g. task_type="feature"). The
    # confirm must coerce to defaults, never raise — a bad enum guess must not
    # 400 the launch and force the agent to self-correct in-chat.
    draft = {
        "team": "backend",
        "task_type": "feature",  # not a valid TaskType
        "nature": "bogus",  # not a valid TaskNature
        "estimated_complexity": "enormous",  # not a valid Complexity
    }
    team, task_type, nature, complexity = PrompterService._coerce_draft_enums(draft)
    assert team is Team.BACKEND
    assert task_type is TaskType.CODE
    assert nature is TaskNature.TECHNICAL
    assert complexity is Complexity.MEDIUM


def test_coerce_priority_maps_words_clamps_and_defaults() -> None:
    # Regression: priority is the one non-enum field the agent guesses, and it
    # guesses a word ("high") as often as a number — int("high") used to 500.
    # word/number -> expected priority int (0=urgent .. 3=low).
    cases: dict[object, int] = {
        "urgent": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
        1: 1,
        "3": 3,
        99: 3,  # clamped into range
        "nonsense": 2,  # unrecognized -> default medium
        None: 2,  # missing -> default medium
    }
    for value, expected in cases.items():
        assert PrompterService._coerce_priority(value) == expected


def test_coerce_draft_enums_keeps_valid_and_derives_missing_team() -> None:
    # Valid values pass through; a missing team is derived from the_work.
    draft = {
        "task_type": "documentation",
        "nature": "technical",
        "estimated_complexity": "medium",
        "the_work": [{"team": "frontend"}],
    }
    team, task_type, nature, complexity = PrompterService._coerce_draft_enums(draft)
    assert team is Team.FRONTEND
    assert task_type is TaskType.DOCUMENTATION
    assert nature is TaskNature.TECHNICAL
    assert complexity is Complexity.MEDIUM


# =============================================================================
# Factory
# =============================================================================


def test_get_prompter_service_no_db() -> None:
    service = get_prompter_service()
    assert isinstance(service, PrompterService)
    assert service._db is None


def test_get_prompter_service_raises_without_db_for_session_methods() -> None:
    service = get_prompter_service()
    with pytest.raises(ServiceError, match="DB session"):
        _ = service._session


# =============================================================================
# DB-backed: assignee routing + confirm_live_draft
# =============================================================================


@pytest.mark.asyncio
async def test_assignee_is_board_distinguishes_roles(db_session: Any) -> None:
    """Drives product team routing: a board reviewer keeps the root on the board.

    A product confirmed via "Board review & Start" is assigned to a board
    reviewer and must stay team=board so the CEO's Approve & Start gate appears;
    one assigned to main-pm (or a cell dev) is not a board task.
    """
    service = get_prompter_service(db=db_session)

    def _agent(role: AgentRole) -> AgentTable:
        return AgentTable(
            id=uuid4(),
            name="A",
            slug=f"a-{uuid4().hex[:8]}",
            role=role,
            team=None,
            status=AgentStatus.ACTIVE,
            model_config={},
            system_prompt="x",
            capabilities=[],
            permissions={},
            metrics={},
        )

    po = _agent(AgentRole.PRODUCT_OWNER)
    hom = _agent(AgentRole.HEAD_MARKETING)
    dev = _agent(AgentRole.DEVELOPER)
    db_session.add_all([po, hom, dev])
    await db_session.flush()

    assert await service._assignee_is_board(cast("UUID", po.id)) is True
    assert await service._assignee_is_board(cast("UUID", hom.id)) is True
    assert await service._assignee_is_board(cast("UUID", dev.id)) is False
    # Unknown id is not a board agent — defensive, must not raise.
    assert await service._assignee_is_board(uuid4()) is False


async def _seed_project_and_ceo(db_session: Any) -> tuple[UUID, UUID]:
    """Seed a system agent + project + CEO; return (project_id, ceo_id).

    Returns plain ``UUID``s (not the ORM rows) so callers pass real uuids to the
    service — no casting the ORM ``.id`` column type at the call site.
    """
    system_id, project_id, ceo_id = uuid4(), uuid4(), uuid4()
    system = AgentTable(
        id=system_id,
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(system)
    await db_session.flush()
    project = ProjectTable(
        id=project_id,
        name="Intake Test Project",
        slug=f"intake-{uuid4().hex[:8]}",
        git_url="https://github.com/example/intake.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_id,
        is_active=True,
    )
    ceo = AgentTable(
        id=ceo_id,
        name="CEO",
        slug=f"ceo-{uuid4().hex[:8]}",
        role=AgentRole.CEO,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="ceo",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([project, ceo])
    await db_session.flush()
    # The "& Start" routes assign the draft to a fixed board/PM agent
    # (product-owner for "Board review", main-pm for "Approve & Start"); those
    # rows must exist for the assigned_to FK. merge() is idempotent, so this is
    # safe whether or not another test already committed them on the shared DB.
    for slug, role, team in (
        ("product-owner", AgentRole.PRODUCT_OWNER, None),
        ("main-pm", AgentRole.MAIN_PM, Team.MAIN_PM),
    ):
        await db_session.merge(
            AgentTable(
                id=UUID(AGENT_UUIDS[slug]),
                name=slug,
                slug=slug,
                role=role,
                team=team,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt=slug,
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
    await db_session.flush()
    return project_id, ceo_id


@pytest.mark.asyncio
async def test_confirm_live_draft_board_route_assigns_po(db_session: Any) -> None:
    """ "Board review & Start" (default route) → PENDING, assigned to the Product
    Owner so the orchestrator fires the PO + HoM review."""
    project_id, ceo_id = await _seed_project_and_ceo(db_session)
    service = get_prompter_service(db=db_session)

    draft = {
        "title": "Add token metrics",
        "objective": "See token usage at a glance.",
        "acceptance_criteria": ["Dashboard shows total tokens"],
        "team": "backend",
        "the_work": [
            {"team": "backend", "summary": "instrument", "items": ["count tokens"]}
        ],
    }
    task_id = await service.confirm_live_draft(draft, ceo_id, project_id=project_id)

    row = await db_session.get(TaskTable, task_id)
    assert row is not None
    assert row.status == TaskStatus.PENDING  # "& Start" — started now
    assert row.assigned_to == UUID(AGENT_UUIDS["product-owner"])  # board review
    assert row.source == "prompter"
    assert row.confirmed_by_human is True
    assert row.team == Team.BACKEND  # lead cell from the_work
    assert row.created_by == ceo_id
    assert row.nature is not None and row.task_type is not None


@pytest.mark.asyncio
async def test_confirm_live_draft_main_pm_route_assigns_main_pm(
    db_session: Any,
) -> None:
    """ "Approve & Start" (route="main_pm") → PENDING, assigned to the Main PM."""
    project_id, ceo_id = await _seed_project_and_ceo(db_session)
    service = get_prompter_service(db=db_session)
    draft = {
        "title": "Quick fix",
        "acceptance_criteria": ["done"],
        "team": "backend",
    }
    task_id = await service.confirm_live_draft(
        draft, ceo_id, project_id=project_id, route="main_pm"
    )
    row = await db_session.get(TaskTable, task_id)
    assert row.status == TaskStatus.PENDING
    assert row.assigned_to == UUID(AGENT_UUIDS["main-pm"])


@pytest.mark.asyncio
async def test_confirm_live_draft_product_routes_to_main_pm(db_session: Any) -> None:
    """A product-scoped draft via the "Approve & Start" path is a Main-PM root.

    The board path (the ``route="board"`` default) keeps the root at
    ``team=board`` until the CEO approves; the Main-PM path is selected
    explicitly with ``route="main_pm"``.
    """
    _project_id, ceo_id = await _seed_project_and_ceo(db_session)
    product_id = uuid4()
    product = ProductTable(
        id=product_id,
        name="Intake Product",
        slug=f"prod-{uuid4().hex[:8]}",
        description="x",
        created_by=ceo_id,
    )
    db_session.add(product)
    await db_session.flush()

    service = get_prompter_service(db=db_session)
    draft = {
        "title": "Board-led feature",
        "acceptance_criteria": ["works end to end"],
        "team": "backend",
    }
    task_id = await service.confirm_live_draft(
        draft, ceo_id, product_id=product_id, route="main_pm"
    )
    row = await db_session.get(TaskTable, task_id)
    assert row.team == Team.MAIN_PM
    assert row.product_id == product_id
    assert row.project_id is None
