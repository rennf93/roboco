"""VaultJanitor — state-file due-logic, drift repair, archival, weekly report.

No DB: task/project services are stubbed; the writer runs against a real
tmp_path vault (the vault_writer test style).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.services import vault_assembly
from roboco.services.vault_assembly import assemble_task_note_data
from roboco.services.vault_janitor import VaultJanitor, _iso_week
from roboco.services.vault_writer import TaskNoteData, VaultWriter

_TASK_ID = "11112222-3333-4444-5555-666677778888"
_TEST_CAP = 2


def _task_stub(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": _TASK_ID,
        "title": "Add login endpoint",
        "description": "Implement it.",
        "status": "in_progress",
        "team": "backend",
        "priority": 2,
        "task_type": "code",
        "acceptance_criteria": [],
        "pr_number": None,
        "pr_url": None,
        "project_id": None,
        "parent_task_id": None,
        "dependency_ids": None,
        "batch_id": None,
        "completed_at": None,
        "updated_at": None,
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _touched(task: Any) -> datetime:
    return cast("datetime", task.updated_at or task.created_at)


class _TaskSvcStub:
    """Duck-typed TaskService mirroring the real queries' filter + ascending
    order, so the janitor's capped resume-marker logic is exercised for real."""

    def __init__(
        self,
        changed: list[Any] | None = None,
        sample: list[Any] | None = None,
        archive: list[Any] | None = None,
    ) -> None:
        self.changed = changed or []
        self.sample = sample or []
        self.archive = archive or []
        self.calls: list[str] = []

    async def list_updated_since(
        self, since: datetime, limit: int = 100, offset: int = 0
    ) -> list[Any]:
        assert since.tzinfo is not None
        self.calls.append("list_updated_since")
        eligible = sorted(
            (t for t in self.changed if _touched(t) >= since), key=_touched
        )
        return eligible[offset : offset + limit]

    async def sample_stale_tasks(self, before: datetime, limit: int = 20) -> list[Any]:
        assert before.tzinfo is not None
        self.calls.append("sample_stale_tasks")
        return self.sample[:limit]

    async def list_archive_candidates(
        self, after: datetime, before: datetime, limit: int = 100, offset: int = 0
    ) -> list[Any]:
        assert after < before
        self.calls.append("list_archive_candidates")
        eligible = sorted(
            (
                t
                for t in self.archive
                if after <= (t.completed_at or _touched(t)) < before
            ),
            key=lambda t: t.completed_at or _touched(t),
        )
        return eligible[offset : offset + limit]

    async def get(self, task_id: Any) -> Any:
        assert task_id is not None
        return None

    async def get_subtasks(self, task_id: Any) -> list[Any]:
        assert task_id is not None
        return []


def _janitor(
    monkeypatch: pytest.MonkeyPatch, vault: Any, task_svc: _TaskSvcStub
) -> VaultJanitor:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", True)
    monkeypatch.setattr(settings, "vault_path", str(vault))
    monkeypatch.setattr(
        "roboco.services.vault_janitor.get_task_service", lambda _s: task_svc
    )
    monkeypatch.setattr(
        "roboco.services.vault_janitor.get_project_service", lambda _s: MagicMock()
    )
    return VaultJanitor(MagicMock())


def _state_file(vault: Any) -> Any:
    return vault / "RoboCo" / "_meta" / ".janitor_state.json"


def _write_state(vault: Any, state: dict[str, Any]) -> None:
    path = _state_file(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _note_data(**overrides: Any) -> TaskNoteData:
    base: dict[str, Any] = {
        "id": _TASK_ID,
        "title": "Add login endpoint",
        "project_slug": "unassigned",
        "description": "Implement it.",
        "status": "in_progress",
        "team": "backend",
        "priority": 2,
        "task_type": "code",
    }
    base.update(overrides)
    return TaskNoteData(**base)


# --- gating / due-logic ---------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_cycle_noop_when_vault_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", False)
    monkeypatch.setattr(settings, "vault_path", str(tmp_path))
    result = await VaultJanitor(MagicMock()).run_cycle()
    assert result == {}
    assert not _state_file(tmp_path).exists()


@pytest.mark.asyncio
async def test_fresh_vault_sweep_is_due_and_state_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    task_svc = _TaskSvcStub()
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    await janitor.run_cycle()
    assert "list_updated_since" in task_svc.calls
    state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert "last_sweep" in state


@pytest.mark.asyncio
async def test_recent_sweep_not_due(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    _write_state(tmp_path, {"last_sweep": datetime.now(UTC).isoformat()})
    task_svc = _TaskSvcStub()
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    result = await janitor.run_cycle()
    assert task_svc.calls == []
    assert result == {"repaired": 0, "archived": 0, "failed": 0}


@pytest.mark.asyncio
async def test_corrupt_state_value_degrades_to_no_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Valid JSON with a non-string value must not wedge the janitor: the
    sweep runs as if unswept and the state file is repaired in place."""
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    _write_state(tmp_path, {"last_sweep": 12345, "archive_watermark": True})
    task_svc = _TaskSvcStub()
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    result = await janitor.run_cycle()
    assert result == {"repaired": 0, "archived": 0, "failed": 0}
    assert "list_updated_since" in task_svc.calls
    state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert datetime.fromisoformat(state["last_sweep"]).tzinfo is not None


# --- drift repair ----------------------------------------------------------- #


@pytest.mark.asyncio
async def test_changed_task_reprojection_preserves_narrative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    writer = VaultWriter(tmp_path)
    writer.write_task(_note_data(narrative="Auditor prose survives."))
    task_svc = _TaskSvcStub(changed=[_task_stub(status="awaiting_qa")])
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    result = await janitor.run_cycle()
    assert result["repaired"] == 1
    note = writer.find_task_note(_TASK_ID)
    assert note is not None
    text = note.read_text(encoding="utf-8")
    assert "Auditor prose survives." in text
    assert "status: awaiting_qa" in text


@pytest.mark.asyncio
async def test_sample_verification_recreates_deleted_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    task_svc = _TaskSvcStub(sample=[_task_stub()])
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    result = await janitor.run_cycle()
    assert result["repaired"] == 1
    assert VaultWriter(tmp_path).find_task_note(_TASK_ID) is not None


@pytest.mark.asyncio
async def test_sample_verification_touches_stale_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    writer = VaultWriter(tmp_path)
    writer.write_task(_note_data(status="pending"))
    task_svc = _TaskSvcStub(sample=[_task_stub(status="in_progress")])
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    result = await janitor.run_cycle()
    assert result["repaired"] == 1
    note = writer.find_task_note(_TASK_ID)
    assert note is not None
    assert "status: in_progress" in note.read_text(encoding="utf-8")


# --- per-cycle cap + per-item isolation -------------------------------------- #


def _stub_id(n: int) -> str:
    return f"aaaa{n:04d}-0000-0000-0000-000000000000"


@pytest.mark.asyncio
async def test_capped_cycle_resumes_tail_next_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A capped tick advances last_sweep only to the max processed stamp, so
    the next (immediately due again) tick drains the tail — no misses."""
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    monkeypatch.setattr(
        "roboco.services.vault_janitor._MAX_REPROJECT_PER_CYCLE", _TEST_CAP
    )
    old = datetime.now(UTC) - timedelta(days=3)
    tasks = [
        _task_stub(
            id=_stub_id(i), title=f"Task {i}", updated_at=old + timedelta(minutes=i)
        )
        for i in range(_TEST_CAP + 1)
    ]
    task_svc = _TaskSvcStub(changed=tasks)
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    writer = VaultWriter(tmp_path)

    first = await janitor.run_cycle()
    assert first["repaired"] == _TEST_CAP
    assert writer.find_task_note(tasks[2].id) is None
    state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert state["last_sweep"] == tasks[1].updated_at.isoformat()

    await janitor.run_cycle()
    assert writer.find_task_note(tasks[2].id) is not None


@pytest.mark.asyncio
async def test_raising_item_is_skipped_counted_and_state_advances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    old = datetime.now(UTC) - timedelta(days=2)
    bad = _task_stub(id=_stub_id(1), title="Bad", updated_at=old)
    good = _task_stub(
        id=_stub_id(2), title="Good", updated_at=old + timedelta(minutes=1)
    )
    task_svc = _TaskSvcStub(changed=[bad, good])
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    real = vault_assembly.reproject_task

    async def flaky(writer: Any, tsvc: Any, psvc: Any, task: Any) -> Any:
        if task.id == bad.id:
            raise OSError("boom")
        return await real(writer, tsvc, psvc, task)

    with patch("roboco.services.vault_assembly.reproject_task", side_effect=flaky):
        result = await janitor.run_cycle()

    assert result["repaired"] == 1
    assert result["failed"] == 1
    writer = VaultWriter(tmp_path)
    assert writer.find_task_note(good.id) is not None
    assert writer.find_task_note(bad.id) is None
    state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert datetime.fromisoformat(state["last_sweep"]) > _touched(good)


@pytest.mark.asyncio
async def test_capped_archive_advances_watermark_to_processed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A capped archive pass advances the watermark only to the last processed
    candidate's terminal stamp (not the full cutoff) — the tail drains on the
    next due sweep with no gap."""
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    monkeypatch.setattr(settings, "vault_archive_days", 30)
    monkeypatch.setattr("roboco.services.vault_janitor._MAX_ARCHIVE_PER_CYCLE", 1)
    oldest = datetime.now(UTC) - timedelta(days=100)
    older = datetime.now(UTC) - timedelta(days=90)
    tasks = [
        _task_stub(
            id=_stub_id(1), title="Oldest", status="completed", completed_at=oldest
        ),
        _task_stub(
            id=_stub_id(2), title="Older", status="completed", completed_at=older
        ),
    ]
    task_svc = _TaskSvcStub(archive=tasks)
    janitor = _janitor(monkeypatch, tmp_path, task_svc)

    result = await janitor.run_cycle()
    assert result["archived"] == 1
    writer = VaultWriter(tmp_path)
    assert writer.find_task_note(tasks[0].id) is not None  # oldest-first
    assert writer.find_task_note(tasks[1].id) is None
    state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert state["archive_watermark"] == oldest.isoformat()


# --- archival ---------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_archival_moves_old_terminal_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    monkeypatch.setattr(settings, "vault_archive_days", 30)
    writer = VaultWriter(tmp_path)
    live = writer.write_task(_note_data(status="completed"))
    completed_at = datetime.now(UTC) - timedelta(days=90)
    task = _task_stub(status="completed", completed_at=completed_at)
    task_svc = _TaskSvcStub(archive=[task])
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    result = await janitor.run_cycle()
    assert result["archived"] == 1
    assert not live.exists()
    note = writer.find_task_note(_TASK_ID)
    assert note is not None
    expected_prefix = tmp_path / "RoboCo" / "Archive" / str(completed_at.year) / "Tasks"
    assert str(note).startswith(str(expected_prefix))
    # A later re-projection finds the archived note — no duplicate appears.
    data = await assemble_task_note_data(task_svc, MagicMock(), task)
    assert writer.write_task(data) == note
    assert writer.find_task_note(_TASK_ID) == note


@pytest.mark.asyncio
async def test_archive_days_zero_disables_archival(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    monkeypatch.setattr(settings, "vault_archive_days", 0)
    task_svc = _TaskSvcStub(archive=[_task_stub(status="completed")])
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    result = await janitor.run_cycle()
    assert result["archived"] == 0
    assert "list_archive_candidates" not in task_svc.calls


# --- weekly report ------------------------------------------------------------ #


def _metrics_stub() -> MagicMock:
    svc = MagicMock()
    svc.get_velocity = AsyncMock(
        return_value=SimpleNamespace(
            tasks_completed=5,
            tasks_created=8,
            avg_completion_hours=12.5,
            completion_rate=0.625,
        )
    )
    svc.get_cycle_time_by_stage = AsyncMock(
        return_value=[
            SimpleNamespace(status="in_progress", avg_seconds=3600.0, sample_size=4)
        ]
    )
    svc.get_bottleneck_distribution = AsyncMock(
        return_value=SimpleNamespace(
            by_stage=[
                SimpleNamespace(
                    status="awaiting_qa", cumulative_seconds=7200.0, pct_of_total=0.5
                )
            ]
        )
    )
    svc.get_rework_metrics = AsyncMock(
        return_value=SimpleNamespace(
            rate=0.2,
            rework_cost_usd=1.23,
            by_team=[SimpleNamespace(team="backend", rate=0.1)],
        )
    )
    return svc


@pytest.mark.asyncio
async def test_weekly_report_written_once_per_week_and_notifies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", True)
    usage_svc = MagicMock()
    usage_svc.get_summary = AsyncMock(
        return_value={"total_cost_usd": 42.5, "total_tokens": 123456}
    )
    notifier = MagicMock()
    notifier.send_weekly_report_notification = AsyncMock()
    task_svc = _TaskSvcStub()
    janitor = _janitor(monkeypatch, tmp_path, task_svc)
    with (
        patch(
            "roboco.services.metrics.get_metrics_service",
            return_value=_metrics_stub(),
        ),
        patch("roboco.services.usage.get_usage_service", return_value=usage_svc),
        patch(
            "roboco.services.notification.NotificationService", return_value=notifier
        ),
    ):
        await janitor.run_cycle()
        await janitor.run_cycle()  # same ISO week — no second report

    week = _iso_week(datetime.now(UTC))
    report = tmp_path / "RoboCo" / "Reports" / f"{week}.md"
    assert report.exists()
    notifier.send_weekly_report_notification.assert_awaited_once()
    kwargs = notifier.send_weekly_report_notification.await_args.kwargs
    assert kwargs["week"] == week
    assert kwargs["note_path"] == str(report)


@pytest.mark.asyncio
async def test_weekly_report_skipped_when_report_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", False)
    janitor = _janitor(monkeypatch, tmp_path, _TaskSvcStub())
    await janitor.run_cycle()
    assert not (tmp_path / "RoboCo" / "Reports").exists()


@pytest.mark.asyncio
async def test_weekly_report_notification_failure_never_fails_sweep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(settings, "vault_report_enabled", True)
    usage_svc = MagicMock()
    usage_svc.get_summary = AsyncMock(
        return_value={"total_cost_usd": 0.0, "total_tokens": 0}
    )
    notifier = MagicMock()
    notifier.send_weekly_report_notification = AsyncMock(
        side_effect=RuntimeError("smtp down")
    )
    janitor = _janitor(monkeypatch, tmp_path, _TaskSvcStub())
    with (
        patch(
            "roboco.services.metrics.get_metrics_service",
            return_value=_metrics_stub(),
        ),
        patch("roboco.services.usage.get_usage_service", return_value=usage_svc),
        patch(
            "roboco.services.notification.NotificationService", return_value=notifier
        ),
    ):
        await janitor.run_cycle()
    week = _iso_week(datetime.now(UTC))
    assert (tmp_path / "RoboCo" / "Reports" / f"{week}.md").exists()
