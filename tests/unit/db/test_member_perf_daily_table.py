"""The member_performance_daily rollup table — schema shape."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from roboco.db.tables import MemberPerformanceDailyTable
from sqlalchemy import UniqueConstraint

if TYPE_CHECKING:
    from sqlalchemy import Table

_TABLE = cast("Table", MemberPerformanceDailyTable.__table__)


def test_table_name() -> None:
    assert MemberPerformanceDailyTable.__tablename__ == "member_performance_daily"


def test_has_all_metric_columns_including_extras() -> None:
    cols = set(_TABLE.columns.keys())
    assert {
        # core
        "date",
        "member_kind",
        "agent_slug",
        "team",
        "role",
        "tasks_completed",
        "tasks_first_pass",
        "revisions_caused",
        "revisions_received",
        "active_runtime_seconds",
        "turns",
        "tool_calls",
        "tokens",
        "cost_usd",
        "ceo_approval_dwell_seconds",
        "ceo_unblock_dwell_seconds",
        "godmode_actions",
        # the 4 CEO-approved extras + blocked_seconds
        "qa_reviews_total",
        "qa_reviews_passed",
        "escalations",
        "blocked_others",
        "idle_seconds",
        "blocked_seconds",
    } <= cols


def test_natural_key_is_unique() -> None:
    uniques = [c for c in _TABLE.constraints if isinstance(c, UniqueConstraint)]
    key_sets = [{col.name for col in u.columns} for u in uniques]
    assert {"date", "member_kind", "agent_slug"} in key_sets


def test_agent_slug_not_nullable() -> None:
    # NOT NULL DEFAULT '' — else the CEO row (agent_slug NULL) would duplicate
    # under Postgres' NULL-distinct UNIQUE semantics.
    assert _TABLE.columns["agent_slug"].nullable is False
