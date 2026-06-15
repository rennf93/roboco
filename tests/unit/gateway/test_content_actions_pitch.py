"""roboco.services.gateway.content_actions.pitch — Board-gated product proposal."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _actions(role: str) -> ContentActions:
    task = MagicMock()
    agent = MagicMock()
    agent.role = role
    task.agent_for = AsyncMock(return_value=agent)
    task.session = MagicMock()
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        messaging=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
    )
    return ContentActions(deps)


@pytest.mark.asyncio
async def test_pitch_forbidden_for_non_board() -> None:
    env = await _actions("developer").pitch(
        agent_id=uuid4(),
        title="T",
        slug="t",
        problem="p",
        proposed_solution="s",
        target_cells=["backend"],
    )
    assert env.error is not None
    assert env.status is None


@pytest.mark.asyncio
async def test_pitch_creates_for_board(monkeypatch: pytest.MonkeyPatch) -> None:
    created = MagicMock()
    created.id = uuid4()
    svc = MagicMock()
    svc.create = AsyncMock(return_value=created)
    monkeypatch.setattr("roboco.services.pitch.get_pitch_service", lambda _s: svc)
    env = await _actions("product_owner").pitch(
        agent_id=uuid4(),
        title="Widget",
        slug="widget",
        problem="people need widgets",
        proposed_solution="build a widget service",
        target_cells=["backend", "frontend"],
    )
    assert env.error is None
    assert env.status == "proposed"
    svc.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_pitch_rejects_non_cell_target() -> None:
    env = await _actions("head_marketing").pitch(
        agent_id=uuid4(),
        title="T",
        slug="t",
        problem="p",
        proposed_solution="s",
        target_cells=["board"],
    )
    assert env.error is not None
