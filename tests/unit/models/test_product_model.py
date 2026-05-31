from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from roboco.foundation.identity import Team
from roboco.models.product import ProductCellMapping, ProductCreate


def test_product_create_requires_slug_pattern() -> None:
    with pytest.raises(ValidationError):
        ProductCreate(name="X", slug="Has Spaces")


def test_cell_mapping_accepts_cell_team() -> None:
    m = ProductCellMapping(team=Team.BACKEND, project_id=uuid4())
    assert m.team is Team.BACKEND


def test_cell_mapping_rejects_non_cell_team() -> None:
    with pytest.raises(ValidationError):
        ProductCellMapping(team=Team.BOARD, project_id=uuid4())
