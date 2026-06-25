"""CI-watch routes its fix-task notification to the project's cell PM.

Not the CEO (a delivery/client repo's red CI is a cell concern) and once per
project per cycle (the engine opens at most one task per repo per cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.services.ci_watch_engine import get_ci_watch_engine
from roboco.services.telemetry.source import TelemetrySample

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid


class _FakeSource:
    def __init__(self, samples: list[TelemetrySample]) -> None:
        self._samples = samples

    async def fetch(self, _projects: list[object]) -> list[TelemetrySample]:
        return list(self._samples)


def _breach(slug: str) -> TelemetrySample:
    return TelemetrySample(
        signal_name=f"ci_conclusion:{slug}",
        value=1.0,
        threshold=1.0,
        window="latest_completed_run",
        repo_hint=slug,
        observed_at="2026-06-25T00:00:00Z",
        raw_ref=f"https://github.com/x/{slug}/actions/runs/1",
        detail=f"CI on {slug}@master concluded 'failure'",
    )


async def _agent(db: AsyncSession, agent_id: Any, role: AgentRole, slug: str) -> None:
    if await db.get(AgentTable, agent_id) is None:
        db.add(
            AgentTable(
                id=agent_id,
                name=slug,
                slug=f"{slug}-{uuid4().hex[:8]}",
                role=role,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="x",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
        await db.flush()


@pytest.fixture(autouse=True)
async def _setup(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ci_watch_enabled", True)
    monkeypatch.setattr(settings, "ci_watch_max_per_cycle", 5)
    monkeypatch.setattr(settings, "ci_watch_max_open_tasks", 5)
    await _agent(db_session, SYSTEM_UUID, AgentRole.SYSTEM, "system")
    await _agent(db_session, MAIN_PM_UUID, AgentRole.MAIN_PM, "main-pm")


@pytest.mark.asyncio
async def test_notifies_backend_cell_pm_once(db_session: AsyncSession) -> None:
    proj = ProjectTable(
        id=uuid4(),
        name="red",
        slug="red",
        git_url="https://github.com/x/a.git",
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        ci_watch_enabled=True,
    )
    db_session.add(proj)
    await db_session.flush()

    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    engine = get_ci_watch_engine(db_session, source=_FakeSource([_breach("red")]))
    with patch(
        "roboco.services.ci_watch_engine.NotificationService", return_value=notifier
    ):
        created = await engine.run_cycle([proj])

    assert len(created) == 1
    notifier.send_ack_notification.assert_awaited_once()
    kwargs = notifier.send_ack_notification.await_args.kwargs
    assert kwargs["to_agent"] == "be-pm"  # the BACKEND cell PM, not "ceo"
    assert "red" in kwargs["body"]
