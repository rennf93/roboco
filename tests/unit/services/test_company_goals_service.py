"""Tests for CompanyGoalsService — the singleton company charter."""

from __future__ import annotations

from types import SimpleNamespace
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
    assert goals["brand_voice"] == ""
    assert goals["company_name"] == ""


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


@pytest.mark.asyncio
async def test_brand_voice_roundtrips(db_session: Any) -> None:
    svc = get_company_goals_service(db_session)
    await svc.upsert({"brand_voice": "Confident, dry wit, no exclamation points."})
    goals = await svc.get()
    assert goals["brand_voice"] == "Confident, dry wit, no exclamation points."


@pytest.mark.asyncio
async def test_brand_voice_untouched_by_partial_upsert(db_session: Any) -> None:
    svc = get_company_goals_service(db_session)
    await svc.upsert({"brand_voice": "Speak as 'we'."})
    # A later partial upsert that omits brand_voice must leave it unchanged —
    # the same partial-update contract north_star/constraints already have.
    await svc.upsert({"north_star": "Ship a delightful product"})
    goals = await svc.get()
    assert goals["brand_voice"] == "Speak as 'we'."
    assert goals["north_star"] == "Ship a delightful product"


@pytest.mark.asyncio
async def test_company_name_roundtrips(db_session: Any) -> None:
    svc = get_company_goals_service(db_session)
    await svc.upsert({"company_name": "Acme Robotics"})
    goals = await svc.get()
    assert goals["company_name"] == "Acme Robotics"


@pytest.mark.asyncio
async def test_company_name_untouched_by_partial_upsert(db_session: Any) -> None:
    svc = get_company_goals_service(db_session)
    await svc.upsert({"company_name": "Acme Robotics"})
    # Mirrors brand_voice's partial-update contract.
    await svc.upsert({"north_star": "Ship a delightful product"})
    goals = await svc.get()
    assert goals["company_name"] == "Acme Robotics"
    assert goals["north_star"] == "Ship a delightful product"


# --------------------------------------------------------------------------- #
# resolve_product_name — the shared fallback chain XEngine and VideoEngine
# both brand their drafting prompts with (project name -> company_name ->
# "RoboCo"). A SimpleNamespace stands in for a ProjectTable: the method only
# reads ``.name``, so a full project/agent seed adds nothing here.
# --------------------------------------------------------------------------- #


def _project_stub(name: str) -> Any:
    """A ProjectTable stand-in for resolve_product_name, which only reads
    ``.name`` — returning ``Any`` (not the SimpleNamespace type) so it type-
    checks against the real ``ProjectTable | None`` parameter with no cast."""
    return SimpleNamespace(name=name)


@pytest.mark.asyncio
async def test_resolve_product_name_uses_project_name(db_session: Any) -> None:
    svc = get_company_goals_service(db_session)
    name = await svc.resolve_product_name(_project_stub("Acme Robotics"))
    assert name == "Acme Robotics"


@pytest.mark.asyncio
async def test_resolve_product_name_falls_back_to_company_name(db_session: Any) -> None:
    svc = get_company_goals_service(db_session)
    await svc.upsert({"company_name": "Acme Robotics"})
    name = await svc.resolve_product_name(None)
    assert name == "Acme Robotics"


@pytest.mark.asyncio
async def test_resolve_product_name_ignores_a_project_with_no_name(
    db_session: Any,
) -> None:
    svc = get_company_goals_service(db_session)
    await svc.upsert({"company_name": "Acme Robotics"})
    name = await svc.resolve_product_name(_project_stub(""))
    assert name == "Acme Robotics"


@pytest.mark.asyncio
async def test_resolve_product_name_defaults_to_roboco(db_session: Any) -> None:
    svc = get_company_goals_service(db_session)
    await svc.upsert({"company_name": ""})
    name = await svc.resolve_product_name(None)
    assert name == "RoboCo"
