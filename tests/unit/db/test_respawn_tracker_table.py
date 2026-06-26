"""The respawn_tracker table — durable backing for the PM-respawn counter.

Mirrors WaitingRecordTable: a composite-PK row per (agent_slug, task_id) the
orchestrator's loop-breaker counter is keyed on, so it survives a restart
instead of resetting to count=1 and re-burning the strike threshold.
"""

from __future__ import annotations

from roboco.db.tables import RespawnTrackerTable


def test_table_name() -> None:
    assert RespawnTrackerTable.__tablename__ == "respawn_tracker"


def test_composite_primary_key_is_agent_slug_and_task_id() -> None:
    pk_cols = {col.name for col in RespawnTrackerTable.__table__.primary_key.columns}
    assert pk_cols == {"agent_slug", "task_id"}


def test_payload_columns_present() -> None:
    cols = set(RespawnTrackerTable.__table__.columns.keys())
    assert {
        "agent_slug",
        "task_id",
        "count",
        "last_status",
        "last_check",
        "tracing_resets",
        "notified",
        "updated_at",
    } <= cols


def test_task_id_has_no_foreign_key() -> None:
    # Deliberately NOT a FK to tasks: the startup loader validates against live
    # tasks instead, so a cascade can never silently resurrect/erase a counter.
    task_id = RespawnTrackerTable.__table__.columns["task_id"]
    assert task_id.foreign_keys == set()


def test_last_check_index_present() -> None:
    index_names = {idx.name for idx in RespawnTrackerTable.__table__.indexes}
    assert "ix_respawn_tracker_last_check" in index_names
