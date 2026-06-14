from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.services.base import NotFoundError
from roboco.services.prompter import (
    PrompterService,
    compose_redraft_message,
    format_board_briefing,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


# --------------------------------------------------------------------------- #
# Pure helpers (no DB)
# --------------------------------------------------------------------------- #
def test_format_board_briefing_labels_and_orders() -> None:
    entries = [
        {"author_role": "product_owner", "author": "po", "title": "PO", "content": "x"},
        {
            "author_role": "head_marketing",
            "author": "hom",
            "title": "HoM",
            "content": "y",
        },
    ]
    out = format_board_briefing(entries)
    assert "Product Owner" in out
    assert "Head of Marketing" in out
    assert out.index("Product Owner") < out.index("Head of Marketing")
    assert "x" in out and "y" in out


def test_format_board_briefing_empty() -> None:
    assert format_board_briefing([]) == ""


def test_compose_redraft_message_includes_draft_and_brief() -> None:
    task = SimpleNamespace(
        title="My Task",
        description="The current description.",
        acceptance_criteria=["does X", "does Y"],
    )
    entries = [
        {"author_role": "product_owner", "author": "po", "title": "PO", "content": "z"}
    ]
    msg = compose_redraft_message(cast(TaskTable, task), entries)
    assert "My Task" in msg
    assert "The current description." in msg
    assert "- does X" in msg
    assert "Product Owner" in msg
    assert "z" in msg


# --------------------------------------------------------------------------- #
# update_live_draft (DB)
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def redraft_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    def _agent(slug: str, role: AgentRole) -> AgentTable:
        return AgentTable(
            id=uuid4(),
            name=slug,
            slug=slug,
            role=role,
            team=None,
            status=AgentStatus.ACTIVE,
            model_config={},
            system_prompt="x",
            capabilities=[],
            permissions={},
            metrics={},
        )

    main_pm = _agent("main-pm", AgentRole.MAIN_PM)
    po = _agent(f"product-owner-{uuid4().hex[:4]}", AgentRole.PRODUCT_OWNER)
    db_session.add_all([main_pm, po])
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=po.id,
    )
    db_session.add(project)
    await db_session.flush()

    def _board_task(board_review_complete: bool) -> TaskTable:
        t = TaskTable(
            id=uuid4(),
            title="Original title",
            description="Original description, long enough.",
            acceptance_criteria=["original ac"],
            status=TaskStatus.PENDING,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            project_id=project.id,
            created_by=po.id,
            team=Team.BOARD,
            board_review_complete=board_review_complete,
            assigned_to=po.id,
        )
        db_session.add(t)
        return t

    yield {
        "svc": PrompterService(db_session),
        "db": db_session,
        "main_pm": main_pm,
        "po": po,
        "mk": _board_task,
    }


_DRAFT = {
    "title": "Revised title",
    "objective": "Revised objective after board feedback for the task at hand.",
    "acceptance_criteria": ["revised ac one", "revised ac two"],
    "description": "Revised description that is comfortably over twenty chars.",
}


@pytest.mark.asyncio
async def test_update_live_draft_main_pm_updates_and_hands_off(
    redraft_setup: dict,
) -> None:
    task = redraft_setup["mk"](True)  # board review complete
    await redraft_setup["db"].flush()
    out_id = await redraft_setup["svc"].update_live_draft(
        task.id, _DRAFT, route="main_pm"
    )
    assert out_id == task.id
    assert task.title == "Revised title"
    assert task.acceptance_criteria == ["revised ac one", "revised ac two"]
    assert "Revised" in task.description
    # Approve & Start ran → handed to Main PM.
    assert task.assigned_to == redraft_setup["main_pm"].id
    assert task.team == Team.MAIN_PM


@pytest.mark.asyncio
async def test_update_live_draft_reboard_resets_flag(redraft_setup: dict) -> None:
    task = redraft_setup["mk"](True)
    await redraft_setup["db"].flush()
    await redraft_setup["svc"].update_live_draft(task.id, _DRAFT, route="board")
    assert task.title == "Revised title"
    assert task.board_review_complete is False  # back for another review round
    assert task.assigned_to == redraft_setup["po"].id  # still on the board
    assert task.team == Team.BOARD


@pytest.mark.asyncio
async def test_update_live_draft_missing_task_raises(redraft_setup: dict) -> None:
    with pytest.raises(NotFoundError):
        await redraft_setup["svc"].update_live_draft(uuid4(), _DRAFT, route="main_pm")
