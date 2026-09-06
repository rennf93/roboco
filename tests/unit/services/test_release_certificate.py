"""Pure release-certificate primitives: version normalization, per-AC QA
verification counts, and severity-bucketed findings aggregation.

The DB-backed assembly (proposal lookup + windowed task set) is covered by
the integration suite in tests/integration/test_release_routes.py.
"""

from __future__ import annotations

from uuid import uuid4

from roboco.db.tables import TaskReviewFindingTable, TaskTable
from roboco.models.base import TaskStatus
from roboco.services.release_certificate import (
    ReleaseCertificateService,
    _counts_by_severity,
    _verified_criteria_count,
    normalized_version,
)

# --------------------------------------------------------------------------- #
# normalized_version
# --------------------------------------------------------------------------- #


def test_normalized_version_strips_v_prefix_and_whitespace() -> None:
    assert normalized_version(" v0.13.0 ") == "0.13.0"


def test_normalized_version_keeps_bare_version() -> None:
    assert normalized_version("0.13.0") == "0.13.0"


# --------------------------------------------------------------------------- #
# _verified_criteria_count — pass_review's '[AC] …' qa_notes render
# --------------------------------------------------------------------------- #


def test_verified_count_counts_ac_lines() -> None:
    notes = (
        "QA passed.\n"
        "[AC] endpoint exists — verified: routes/portal.py:10\n"
        "[AC] sorted desc — verified: dashboard.py:55\n"
    )
    assert _verified_criteria_count(notes) == 2  # noqa: PLR2004


def test_verified_count_ignores_non_ac_lines() -> None:
    notes = "looks good\n[AC-adjacent] not a stamp\n  [AC] indented, not a stamp\n"
    assert _verified_criteria_count(notes) == 0


def test_verified_count_zero_for_empty_notes() -> None:
    assert _verified_criteria_count(None) == 0
    assert _verified_criteria_count("") == 0


# --------------------------------------------------------------------------- #
# _counts_by_severity — the ledger bucket aggregation
# --------------------------------------------------------------------------- #


def _finding(severity: str, status: str) -> TaskReviewFindingTable:
    return TaskReviewFindingTable(
        task_id=uuid4(),
        origin="qa",
        round=1,
        severity=severity,
        expected="fixed",
        actual="broken",
        status=status,
    )


def test_counts_split_by_severity_within_requested_statuses() -> None:
    rows = [
        _finding("blocker", "open"),
        _finding("major", "open"),
        _finding("minor", "open"),
        _finding("nit", "open"),
        _finding("minor", "addressed"),
        _finding("nit", "verified"),
        _finding("major", "waived"),
    ]
    open_counts = _counts_by_severity(rows, frozenset({"open"}))
    assert (
        open_counts.blocker,
        open_counts.major,
        open_counts.minor,
        open_counts.nit,
    ) == (1, 1, 1, 1)
    closed = _counts_by_severity(rows, frozenset({"addressed", "verified"}))
    assert (closed.minor, closed.nit) == (1, 1)
    assert closed.blocker == 0
    waived = _counts_by_severity(rows, frozenset({"waived"}))
    assert waived.major == 1


def test_counts_skip_unknown_severity() -> None:
    rows = [_finding("catastrophic", "open")]
    assert _counts_by_severity(rows, frozenset({"open"})).blocker == 0


def test_counts_empty_for_no_rows() -> None:
    counts = _counts_by_severity([], frozenset({"open"}))
    assert (counts.blocker, counts.major, counts.minor, counts.nit) == (0, 0, 0, 0)


# --------------------------------------------------------------------------- #
# _task_state — per-AC QA pass state per release task
# --------------------------------------------------------------------------- #


def test_task_state_qa_passed_when_all_criteria_verified() -> None:
    task = TaskTable(
        title="Ship the thing",
        acceptance_criteria=["criterion one", "criterion two"],
        status=TaskStatus.COMPLETED,
        qa_notes=(
            "[AC] criterion one — verified: foo.py:1\n"
            "[AC] criterion two — verified: bar.py:2\n"
        ),
    )
    state = ReleaseCertificateService._task_state(task)
    assert (state.criteria_total, state.criteria_verified) == (2, 2)
    assert state.qa_passed is True


def test_task_state_fails_when_criteria_unverified() -> None:
    task = TaskTable(
        title="Ship the thing",
        acceptance_criteria=["one", "two", "three"],
        status=TaskStatus.COMPLETED,
        qa_notes="[AC] one — verified: foo.py:1\n",
    )
    state = ReleaseCertificateService._task_state(task)
    assert (state.criteria_total, state.criteria_verified) == (3, 1)
    assert state.qa_passed is False


def test_task_state_no_criteria_counts_as_passed() -> None:
    task = TaskTable(title="Docs sweep", status=TaskStatus.COMPLETED)
    state = ReleaseCertificateService._task_state(task)
    assert state.criteria_total == 0
    assert state.qa_passed is True
