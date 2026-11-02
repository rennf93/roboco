"""#90: an unreachable test DB must be reported loudly, not silently skipped.

conftest extracts the report into ``_pg_unreachable_notice`` so it is testable
independently of the live socket check (which depends on whether the operator's
Postgres is up). The per-test ``pytest.skip`` path is unchanged; the notice goes
in the terminal summary so a green run of all-skips is not mistaken for a pass.
It is not a warning: the suite fails on any warning, which stopped the runs
that select no DB test (the in-image suites) before they started.
"""

from __future__ import annotations

from tests.conftest import _pg_unreachable_notice

_PG_SKIP = (
    "('tests/x.py', 3, 'Skipped: Postgres unreachable at localhost:55432 — "
    "set ROBOCO_TEST_DB_HOST/PORT/USER/PASSWORD or start Postgres')"
)
_OTHER_SKIP = "('tests/y.py', 7, 'Skipped: needs Docker')"


def test_notice_counts_the_tests_skipped_for_want_of_postgres() -> None:
    notice = _pg_unreachable_notice(
        [_PG_SKIP, _OTHER_SKIP, _PG_SKIP], host="localhost", port=55432
    )

    assert notice is not None
    assert notice.startswith(
        "2 test(s) needing Postgres were SKIPPED: "
        "Postgres unreachable at localhost:55432."
    )


def test_no_notice_when_no_test_needed_postgres() -> None:
    assert _pg_unreachable_notice([_OTHER_SKIP], host="localhost", port=55432) is None


def test_no_notice_for_another_endpoint() -> None:
    assert _pg_unreachable_notice([_PG_SKIP], host="localhost", port=15432) is None
