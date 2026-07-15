"""Unit tests for ProductService.progress_for_products.

Mocks the SQLAlchemy AsyncSession.execute() boundary and verifies the
per-product aggregation (one grouped query, summed per product, monorepo
dedup of the same project across a product's cells).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from roboco.services.product import ProductService

if TYPE_CHECKING:
    from roboco.db.tables import ProductTable

_PRODUCT_A = UUID("11111111-1111-1111-1111-111111111111")
_PRODUCT_B = UUID("22222222-2222-2222-2222-222222222222")
_PROJECT_1 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_PROJECT_2 = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _cell(project_id: UUID) -> MagicMock:
    cell = MagicMock()
    cell.project_id = project_id
    return cell


def _product(pid: UUID, project_ids: list[UUID]) -> ProductTable:
    p = MagicMock()
    p.id = pid
    p.cells = [_cell(pid_proj) for pid_proj in project_ids]
    return cast("ProductTable", p)


def _result_fetchall(rows: list[MagicMock]) -> MagicMock:
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    return result


def _row(project_id: UUID, done: int, active: int, blocked: int) -> MagicMock:
    row = MagicMock()
    row.project_id = project_id
    row.done = done
    row.active = active
    row.blocked = blocked
    return row


class TestProgressForProducts:
    @pytest.mark.asyncio
    async def test_sums_per_product_across_its_projects(self) -> None:
        """Product A spans projects 1+2; each project's counts are summed."""
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=_result_fetchall(
                [
                    _row(_PROJECT_1, done=3, active=2, blocked=1),
                    _row(_PROJECT_2, done=5, active=0, blocked=0),
                ]
            )
        )
        svc = ProductService(session)
        products = [_product(_PRODUCT_A, [_PROJECT_1, _PROJECT_2])]
        out = await svc.progress_for_products(products)
        assert out[_PRODUCT_A] == {"done": 8, "active": 2, "blocked": 1}

    @pytest.mark.asyncio
    async def test_monorepo_dedup_counts_project_once_per_product(self) -> None:
        """Two cells of the same product pointing at the same project must not
        double-count that project's tasks for the product."""
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=_result_fetchall(
                [_row(_PROJECT_1, done=4, active=1, blocked=0)]
            )
        )
        svc = ProductService(session)
        # Product A has two cells both -> project 1 (monorepo).
        products = [_product(_PRODUCT_A, [_PROJECT_1, _PROJECT_1])]
        out = await svc.progress_for_products(products)
        assert out[_PRODUCT_A] == {"done": 4, "active": 1, "blocked": 0}

    @pytest.mark.asyncio
    async def test_shared_project_attributed_to_both_products(self) -> None:
        """A project referenced by two products contributes to each once."""
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=_result_fetchall(
                [_row(_PROJECT_2, done=2, active=1, blocked=0)]
            )
        )
        svc = ProductService(session)
        products = [
            _product(_PRODUCT_A, [_PROJECT_1, _PROJECT_2]),
            _product(_PRODUCT_B, [_PROJECT_2]),
        ]
        out = await svc.progress_for_products(products)
        # Project 1 has no row -> contributes 0; project 2 -> both products.
        assert out[_PRODUCT_A] == {"done": 2, "active": 1, "blocked": 0}
        assert out[_PRODUCT_B] == {"done": 2, "active": 1, "blocked": 0}

    @pytest.mark.asyncio
    async def test_no_cells_returns_empty(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock()
        svc = ProductService(session)
        out = await svc.progress_for_products([_product(_PRODUCT_A, [])])
        assert out == {}
        session.execute.assert_not_called()
