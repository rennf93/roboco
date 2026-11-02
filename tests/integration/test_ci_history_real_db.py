"""CI run history against a real Postgres: the reading key deduplicates.

``record_runs`` relies on ``ON CONFLICT (project_slug, workflow,
observed_at_str) DO NOTHING`` so a sweep that re-observes the run it already
recorded adds nothing. The ci-watch engine records ``workflow=None``; with
NULLs distinct (migration 108's key) every sweep re-inserted the same run,
and the ci_watch_route grader counted one red run seen twice as two reds.
Migration 111 made the key NULLS NOT DISTINCT; these tests pin that contract
on the model schema the suite builds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from roboco.db.tables import CiRunTable
from roboco.services.ci_history import record_runs, runs_for
from roboco.services.telemetry.source import TelemetrySample
from sqlalchemy import func, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_PROJECT = "ci-history-proj"


def _sample(
    observed_at: str, *, failed: bool, project: str = _PROJECT
) -> TelemetrySample:
    return TelemetrySample(
        signal_name="ci",
        value=1.0 if failed else 0.0,
        threshold=1.0,
        window="latest",
        repo_hint=project,
        observed_at=observed_at,
        raw_ref="https://example.invalid/run",
    )


async def _stored_rows(session: AsyncSession, project: str = _PROJECT) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(CiRunTable)
        .where(CiRunTable.project_slug == project)
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", [None, "ci.yml"])
async def test_a_reading_seen_on_two_sweeps_is_stored_once(
    db_session: AsyncSession, workflow: str | None
) -> None:
    """None is the ci-watch engine's workflow; a name is self-heal's."""
    red = _sample("2026-10-29T10:00:00Z", failed=True)

    assert await record_runs(db_session, [red], workflow=workflow) == 1
    assert await record_runs(db_session, [red], workflow=workflow) == 0

    assert await _stored_rows(db_session) == 1


@pytest.mark.asyncio
async def test_a_reading_without_a_stamp_dedupes_under_the_empty_stamp(
    db_session: AsyncSession,
) -> None:
    await record_runs(db_session, [_sample("", failed=False)], workflow=None)
    await record_runs(db_session, [_sample("", failed=False)], workflow=None)

    assert await _stored_rows(db_session) == 1


@pytest.mark.asyncio
async def test_distinct_readings_are_each_stored(db_session: AsyncSession) -> None:
    await record_runs(
        db_session,
        [
            _sample("2026-10-29T10:00:00Z", failed=True),
            _sample("2026-10-29T11:00:00Z", failed=False),
            _sample("2026-10-29T10:00:00Z", failed=True, project="other-proj"),
        ],
        workflow=None,
    )
    await record_runs(
        db_session, [_sample("2026-10-29T10:00:00Z", failed=True)], workflow="ci.yml"
    )

    stored = await db_session.execute(
        select(CiRunTable.project_slug, CiRunTable.workflow, CiRunTable.observed_at_str)
    )
    assert sorted(stored.tuples(), key=str) == [
        (_PROJECT, "ci.yml", "2026-10-29T10:00:00Z"),
        (_PROJECT, None, "2026-10-29T10:00:00Z"),
        (_PROJECT, None, "2026-10-29T11:00:00Z"),
        ("other-proj", None, "2026-10-29T10:00:00Z"),
    ]


@pytest.mark.asyncio
async def test_record_runs_returns_the_rows_it_inserted(
    db_session: AsyncSession,
) -> None:
    """The count the write reports is the rows that landed, not a failure
    swallowed after the commit."""
    first_sweep = [
        _sample("2026-10-29T10:00:00Z", failed=True),
        _sample("2026-10-29T11:00:00Z", failed=False),
        _sample("2026-10-29T10:00:00Z", failed=True, project="other-proj"),
    ]
    second_sweep = [first_sweep[1], _sample("2026-10-29T12:00:00Z", failed=False)]

    first = await record_runs(db_session, first_sweep, workflow=None)
    second = await record_runs(db_session, second_sweep, workflow=None)

    assert (first, second) == (len(first_sweep), 1)


@pytest.mark.asyncio
async def test_runs_for_returns_one_projects_window_oldest_first(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    for hours_ago, project in ((5, _PROJECT), (3, _PROJECT), (1, _PROJECT), (2, "x")):
        db_session.add(
            CiRunTable(
                created_at=now - timedelta(hours=hours_ago),
                project_slug=project,
                workflow=None,
                conclusion="success",
                is_breach=False,
                observed_at_str=f"stamp-{hours_ago}",
            )
        )
    await db_session.flush()

    runs = await runs_for(
        db_session, _PROJECT, now - timedelta(hours=4), now - timedelta(minutes=30)
    )

    assert [r.observed_at_str for r in runs] == ["stamp-3", "stamp-1"]
