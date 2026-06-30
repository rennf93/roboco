"""#90: an unreachable test DB must warn loudly, not silently skip.

conftest extracts the warning into ``_warn_if_pg_unavailable`` so it is testable
independently of the live socket check (which depends on whether the operator's
Postgres is up). The per-test ``pytest.skip`` path is unchanged; this only adds a
visible import-time warning so a green run of all-skips is not mistaken for a pass.
"""

from __future__ import annotations

import warnings

import pytest
from tests.conftest import _warn_if_pg_unavailable


def test_warns_loudly_when_pg_unavailable() -> None:
    with pytest.warns(UserWarning, match="Postgres unreachable"):
        _warn_if_pg_unavailable(available=False, host="localhost", port=55432)


def test_no_warning_when_pg_available() -> None:
    # No warning should be emitted when the DB is reachable.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _warn_if_pg_unavailable(available=True, host="localhost", port=55432)
