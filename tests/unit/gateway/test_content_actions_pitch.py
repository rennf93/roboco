"""roboco.services.gateway.content_actions.pitch — Board-gated product proposal."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _actions(role: str, *, notification_delivery: Any = None) -> ContentActions:
    task = MagicMock()
    agent = MagicMock()
    agent.role = role
    task.agent_for = AsyncMock(return_value=agent)
    task.session = MagicMock()
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
        notification_delivery=notification_delivery,
    )
    return ContentActions(deps)


@pytest.mark.asyncio
async def test_pitch_forbidden_for_non_board() -> None:
    # Substantive fields so the soup guard passes and the ROLE gate is what
    # rejects (a developer may not pitch) — not an incidental short-field fail.
    env = await _actions("developer").pitch(
        agent_id=uuid4(),
        title="A self-serve widget catalog",
        slug="widget-catalog",
        problem="customers cannot browse widgets without an account",
        proposed_solution="add a public widget catalog with search",
        target_cells=["backend"],
    )
    assert env.error == "not_authorized"
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
    # Substantive fields so the soup guard passes and the non-cell TARGET is
    # what rejects (a pitch can only target cells, not the board).
    env = await _actions("head_marketing").pitch(
        agent_id=uuid4(),
        title="A self-serve widget catalog",
        slug="widget-catalog",
        problem="customers cannot browse widgets without an account",
        proposed_solution="add a public widget catalog with search",
        target_cells=["board"],
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_pitch_notifies_ceo_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful pitch nudges the CEO via the notification-delivery seam."""
    created = MagicMock()
    created.id = uuid4()
    svc = MagicMock()
    svc.create = AsyncMock(return_value=created)
    monkeypatch.setattr("roboco.services.pitch.get_pitch_service", lambda _s: svc)
    notification_delivery = AsyncMock()
    env = await _actions(
        "product_owner", notification_delivery=notification_delivery
    ).pitch(
        agent_id=uuid4(),
        title="Widget",
        slug="widget",
        problem="people need widgets",
        proposed_solution="build a widget service",
        target_cells=["backend", "frontend"],
    )
    assert env.error is None
    assert env.status == "proposed"
    notification_delivery.notify_ceo_of_pitch.assert_awaited_once_with(pitch=created)


@pytest.mark.asyncio
async def test_pitch_survives_notification_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CEO-notification failure never fails pitch() — best-effort only."""
    created = MagicMock()
    created.id = uuid4()
    svc = MagicMock()
    svc.create = AsyncMock(return_value=created)
    monkeypatch.setattr("roboco.services.pitch.get_pitch_service", lambda _s: svc)
    notification_delivery = AsyncMock()
    notification_delivery.notify_ceo_of_pitch.side_effect = RuntimeError("db down")
    env = await _actions(
        "product_owner", notification_delivery=notification_delivery
    ).pitch(
        agent_id=uuid4(),
        title="Widget",
        slug="widget",
        problem="people need widgets",
        proposed_solution="build a widget service",
        target_cells=["backend", "frontend"],
    )
    assert env.error is None
    assert env.status == "proposed"
    notification_delivery.notify_ceo_of_pitch.assert_awaited_once()
