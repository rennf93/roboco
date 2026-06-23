"""Regression: SQL avg/extract "hours" aggregates must serialize as JSON numbers.

``EXTRACT(epoch ...)`` returns ``numeric`` on PostgreSQL 14+, which asyncpg
surfaces as a ``Decimal``. A ``Decimal`` serializes to a JSON *string*, so the
panel's ``avg_cycle_hours.toFixed(1)`` (and the other hours fields) threw
``toFixed is not a function`` against the live deploy. ``_as_hours`` coerces to a
real ``float`` so the field is always a JSON number.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from roboco.services.metrics import _as_hours


def test_as_hours_coerces_decimal_to_float() -> None:
    result = _as_hours(Decimal("1.21"))
    assert result == pytest.approx(1.21)
    assert isinstance(result, float)  # not Decimal -> serializes as a JSON number


def test_as_hours_rounds_to_two_places() -> None:
    assert _as_hours(Decimal("1.236")) == pytest.approx(1.24)
    assert _as_hours(3.14159) == pytest.approx(3.14)
    assert isinstance(_as_hours(3.14159), float)


def test_as_hours_none_and_zero_yield_none() -> None:
    assert _as_hours(None) is None
    assert _as_hours(0) is None
    assert _as_hours(Decimal("0")) is None
