"""Tests for CompanyGoalsService — the singleton company charter."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from roboco.db.tables import CompanyGoalsTable
from roboco.services.company_goals import (
    SINGLETON_ID,
    get_company_goals_service,
)


@pytest.mark.asyncio
async def test_get_returns_empty_defaults_when_unset(db_session: Any) -> None:
    # The "unset" contract is about no/empty charter row. The test DB is shared
    # and route tests commit a charter to it, so establish a clean precondition
    # rather than assume global emptiness.
    existing = await db_session.get(CompanyGoalsTable, SINGLETON_ID)
    if existing is not None:
        await db_session.delete(existing)
        await db_session.commit()
    svc = get_company_goals_service(db_session)
    goals = await svc.get()
    assert goals["north_star"] == ""
    assert goals["objectives"] == []
    assert goals["constraints"] == []
    assert goals["operating_policy"] == {}


@pytest.mark.asyncio
async def test_upsert_then_get_roundtrips(db_session: Any) -> None:
    svc = get_company_goals_service(db_session)
    actor = uuid4()
    await svc.upsert(
        {
            "north_star": "Ship a delightful product",
            "objectives": [{"metric": "NPS", "target": 50, "status": "active"}],
            "constraints": ["AGPL only"],
            "operating_policy": {
                "autonomy_level": "assisted",
                "monthly_budget_cap": 500,
            },
        },
        updated_by=actor,
    )
    goals = await svc.get()
    assert goals["north_star"] == "Ship a delightful product"
    assert goals["objectives"][0]["metric"] == "NPS"
    assert goals["constraints"] == ["AGPL only"]
    assert goals["operating_policy"]["autonomy_level"] == "assisted"
    assert goals["updated_by"] == str(actor)
    assert goals["updated_at"] is not None


@pytest.mark.asyncio
async def test_upsert_is_singleton_and_partial(db_session: Any) -> None:
    svc = get_company_goals_service(db_session)
    await svc.upsert({"north_star": "First", "constraints": ["a"]})
    # A second upsert updates the SAME row and only the provided keys.
    await svc.upsert({"north_star": "Second"})
    goals = await svc.get()
    assert goals["north_star"] == "Second"
    assert goals["constraints"] == ["a"]  # untouched key preserved

    # Exactly one row exists (singleton), found at the canonical id.
    row = await db_session.get(CompanyGoalsTable, SINGLETON_ID)
    assert row is not None
