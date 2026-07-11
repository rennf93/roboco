"""VaultWriter — pure materializer: layout, idempotency, wikilink shape.

No DB, no flag checks (those are the seam callers' job) — just entity ->
markdown, given a tmp_path vault root.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import yaml
from roboco.services.vault_writer import (
    A2AMessageData,
    AgentNoteData,
    BottleneckRow,
    FindingRow,
    JournalNoteData,
    OrgReportData,
    StageTimingRow,
    TaskLinkRef,
    TaskNoteData,
    TeamReworkRow,
    VaultWriter,
)

if TYPE_CHECKING:
    from pathlib import Path

_PRIORITY = 2


def _task_data(**overrides: Any) -> TaskNoteData:
    base: dict[str, Any] = {
        "id": "11112222-3333-4444-5555-666677778888",
        "title": "Add user authentication endpoint",
        "project_slug": "roboco-api",
        "description": "Implement the login endpoint.",
        "status": "in_progress",
        "team": "backend",
        "priority": _PRIORITY,
        "task_type": "code",
    }
    base.update(overrides)
    return TaskNoteData(**base)


# --- layout ------------------------------------------------------------- #


def test_write_task_layout_and_frontmatter(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    path = writer.write_task(_task_data())

    assert path == (
        tmp_path
        / "RoboCo"
        / "Tasks"
        / "roboco-api"
        / "Add user authentication endpoint (11112222).md"
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fm, _, _ = text.removeprefix("---\n").partition("\n---\n")
    frontmatter = yaml.safe_load(fm)
    assert frontmatter["aliases"] == ["11112222"]
    assert frontmatter["status"] == "in_progress"
    assert frontmatter["team"] == "backend"
    assert frontmatter["priority"] == _PRIORITY
    assert "# Add user authentication endpoint" in text
    assert "Implement the login endpoint." in text
    assert "## Narrative" in text
    assert "_Pending Auditor curation._" in text


def test_write_journal_entry_layout(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    path = writer.write_journal_entry(
        JournalNoteData(
            entry_id="aaaa1111-0000-0000-0000-000000000000",
            agent_slug="be-dev-1",
            scope="learning",
            title="Retry flaky pg connections",
            content="Backoff worked.",
            timestamp=datetime(2026, 7, 10, tzinfo=UTC),
        )
    )
    assert path == (
        tmp_path
        / "RoboCo"
        / "Journals"
        / "be-dev-1"
        / "2026-07-10 Retry flaky pg connections (aaaa1111).md"
    )
    text = path.read_text(encoding="utf-8")
    assert "agent: be-dev-1" in text
    assert "scope: learning" in text
    assert "Backoff worked." in text


def test_write_agent_layout(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    path = writer.write_agent(
        AgentNoteData(
            slug="be-dev-1", name="BE Dev 1", role="developer", team="backend"
        )
    )
    assert path == tmp_path / "RoboCo" / "Agents" / "be-dev-1.md"
    text = path.read_text(encoding="utf-8")
    assert "role: developer" in text
    assert "BE Dev 1" in text


# --- idempotency / rename stability -------------------------------------- #


def test_write_task_idempotent_same_input(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    p1 = writer.write_task(_task_data())
    p2 = writer.write_task(_task_data())
    assert p1 == p2
    assert p1.read_text(encoding="utf-8") == p2.read_text(encoding="utf-8")


def test_write_task_title_rename_keeps_filename(tmp_path: Path) -> None:
    """A rename updates the title line, not the filename (link stability)."""
    writer = VaultWriter(tmp_path)
    original = writer.write_task(_task_data())
    renamed = writer.write_task(_task_data(title="Add auth endpoint (renamed)"))
    assert original == renamed
    text = renamed.read_text(encoding="utf-8")
    assert "# Add auth endpoint (renamed)" in text


def test_append_a2a_message_idempotent_per_message_id(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    data = A2AMessageData(
        conversation_id="cccc0000-0000-0000-0000-000000000000",
        message_id="msg-0001",
        from_agent="be-dev-1",
        to_agent="be-pm",
        content="ping",
        timestamp=datetime(2026, 7, 10, 9, 0, 0, tzinfo=UTC),
    )
    path1 = writer.append_a2a_message(data)
    path2 = writer.append_a2a_message(data)  # same message id — retry
    assert path1 == path2
    text = path1.read_text(encoding="utf-8")
    assert text.count("<!-- msg:msg-0001 -->") == 1


# --- wikilink shape ------------------------------------------------------- #


def test_task_wikilinks_include_parent_subtasks_dependencies(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    parent = TaskLinkRef(id="99998888-0000-0000-0000-000000000000", title="Parent task")
    sub = TaskLinkRef(id="77776666-0000-0000-0000-000000000000", title="Subtask one")
    dep = TaskLinkRef(id="55554444-0000-0000-0000-000000000000", title="Dep task")
    path = writer.write_task(
        _task_data(parent=parent, subtasks=(sub,), dependencies=(dep,))
    )
    text = path.read_text(encoding="utf-8")
    assert "[[99998888|Parent task]]" in text
    assert "[[77776666|Subtask one]]" in text
    assert "[[55554444|Dep task]]" in text


def test_journal_task_ref_link_without_title(tmp_path: Path) -> None:
    """Journal seam links a task without fetching its title (id-only ok)."""
    writer = VaultWriter(tmp_path)
    task_ref = TaskLinkRef(id="12341234-0000-0000-0000-000000000000")
    path = writer.write_journal_entry(
        JournalNoteData(
            entry_id="bbbb2222-0000-0000-0000-000000000000",
            agent_slug="be-dev-1",
            scope="note",
            title="Quick note",
            content="body",
            timestamp=datetime(2026, 7, 10, tzinfo=UTC),
            task_ref=task_ref,
        )
    )
    text = path.read_text(encoding="utf-8")
    assert "[[12341234]]" in text


# --- cheap frontmatter touch ---------------------------------------------- #


def test_touch_task_frontmatter_noop_when_note_missing(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    touched = writer.touch_task_frontmatter(
        task_id="00001111-0000-0000-0000-000000000000",
        status="claimed",
        team="backend",
        pr_number=None,
        pr_url=None,
    )
    assert touched is False


def test_touch_task_frontmatter_patches_existing_note(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    path = writer.write_task(_task_data(status="pending"))
    touched = writer.touch_task_frontmatter(
        task_id="11112222-3333-4444-5555-666677778888",
        status="in_progress",
        team="backend",
        pr_number=42,
        pr_url="https://github.com/x/y/pull/42",
    )
    assert touched is True
    text = path.read_text(encoding="utf-8")
    assert "status: in_progress" in text
    assert "pr: https://github.com/x/y/pull/42" in text
    # body/narrative untouched by the cheap touch
    assert "## Narrative" in text
    assert "Implement the login endpoint." in text


# --- rebuild-preserves-narrative helper ------------------------------------ #


def test_existing_narrative_none_for_placeholder(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    writer.write_task(_task_data())
    assert (
        writer.existing_narrative("roboco-api", "11112222-3333-4444-5555-666677778888")
        is None
    )


def test_existing_narrative_preserved_when_curated(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    writer.write_task(_task_data(narrative="Shipped cleanly, one rework cycle."))
    assert (
        writer.existing_narrative("roboco-api", "11112222-3333-4444-5555-666677778888")
        == "Shipped cleanly, one rework cycle."
    )


# --- archive-awareness ------------------------------------------------------ #

_ARCHIVE_YEAR = 2025


def test_write_task_archived_lands_in_archive_year_dir(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    path = writer.write_task(_task_data(status="completed", archive_year=_ARCHIVE_YEAR))
    assert path == (
        tmp_path
        / "RoboCo"
        / "Archive"
        / str(_ARCHIVE_YEAR)
        / "Tasks"
        / "roboco-api"
        / "Add user authentication endpoint (11112222).md"
    )


def test_write_task_archival_moves_live_note_without_duplicate(
    tmp_path: Path,
) -> None:
    writer = VaultWriter(tmp_path)
    live = writer.write_task(_task_data())
    archived = writer.write_task(
        _task_data(status="completed", archive_year=_ARCHIVE_YEAR)
    )
    assert not live.exists()
    assert archived.exists()
    assert len(list(tmp_path.rglob("*(11112222).md"))) == 1


def test_find_task_note_locates_archived_note(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    path = writer.write_task(_task_data(status="completed", archive_year=_ARCHIVE_YEAR))
    assert writer.find_task_note("11112222-3333-4444-5555-666677778888") == path


def test_existing_narrative_survives_archival(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    writer.write_task(_task_data(narrative="Curated before archival."))
    writer.write_task(
        _task_data(
            status="completed",
            archive_year=_ARCHIVE_YEAR,
            narrative="Curated before archival.",
        )
    )
    assert (
        writer.existing_narrative("roboco-api", "11112222-3333-4444-5555-666677778888")
        == "Curated before archival."
    )


def test_touch_task_frontmatter_reaches_archived_note(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    path = writer.write_task(_task_data(status="completed", archive_year=_ARCHIVE_YEAR))
    touched = writer.touch_task_frontmatter(
        task_id="11112222-3333-4444-5555-666677778888",
        status="cancelled",
        team="backend",
        pr_number=None,
        pr_url=None,
    )
    assert touched is True
    assert "status: cancelled" in path.read_text(encoding="utf-8")


# --- weekly org-report ------------------------------------------------------- #


def _report_data() -> OrgReportData:
    return OrgReportData(
        week="2026-W28",
        tasks_completed=5,
        tasks_created=8,
        completion_rate=0.625,
        avg_cycle_hours=12.5,
        rework_rate=0.2,
        rework_cost_usd=1.23,
        total_cost_usd=42.5,
        total_tokens=123456,
        stages=(StageTimingRow("in_progress", 3600.0, 4),),
        bottlenecks=(BottleneckRow("awaiting_qa", 7200.0, 0.5),),
        by_team_rework=(TeamReworkRow("backend", 0.1),),
    )


def test_write_org_report_layout_and_frontmatter(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    path = writer.write_org_report(_report_data())
    assert path == tmp_path / "RoboCo" / "Reports" / "2026-W28.md"
    text = path.read_text(encoding="utf-8")
    fm, _, body = text.removeprefix("---\n").partition("\n---\n")
    frontmatter = yaml.safe_load(fm)
    assert frontmatter == {
        "week": "2026-W28",
        "tasks_completed": 5,
        "tasks_created": 8,
        "completion_rate": 0.625,
        "avg_cycle_hours": 12.5,
        "rework_rate": 0.2,
        "rework_cost_usd": 1.23,
        "total_cost_usd": 42.5,
        "total_tokens": 123456,
    }
    assert "## Velocity" in body
    assert "## Cycle time by stage" in body
    assert "| in_progress | 1.0 | 4 |" in body
    assert "## Top bottlenecks" in body
    assert "| awaiting_qa | 50% |" in body
    assert "## Rework" in body
    assert "| backend | 10% |" in body
    assert "## Cost" in body
    assert "$42.50" in body


def test_write_org_report_same_week_overwrites(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    p1 = writer.write_org_report(_report_data())
    p2 = writer.write_org_report(_report_data())
    assert p1 == p2
    assert len(list((tmp_path / "RoboCo" / "Reports").glob("*.md"))) == 1


# --- revision-findings ledger section -------------------------------------- #

_FINDINGS_CAP = 20


def test_write_task_omits_findings_section_when_empty(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    text = writer.write_task(_task_data()).read_text(encoding="utf-8")
    assert "## Findings" not in text


def test_write_task_renders_findings_section(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    finding = FindingRow(
        id8="aaaaaaaa",
        severity="blocker",
        file="roboco/services/task.py",
        line=42,
        expected="the endpoint returns 404",
        actual="the endpoint returns 500",
        fix="add a not-found guard",
        status="open",
        round=2,
    )
    text = writer.write_task(_task_data(findings=(finding,))).read_text(
        encoding="utf-8"
    )
    assert "## Findings" in text
    assert "[F-aaaaaaaa] (blocker, round 2, open)" in text
    assert "roboco/services/task.py:42" in text
    assert (
        "the endpoint returns 404 → the endpoint returns 500 → "
        "add a not-found guard" in text
    )


def test_write_task_findings_capped_with_overflow_line(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path)
    many = tuple(
        FindingRow(
            id8=f"{i:08d}",
            severity="minor",
            file=None,
            line=None,
            expected="x",
            actual="y",
            fix=None,
            status="open",
            round=1,
        )
        for i in range(25)
    )
    text = writer.write_task(_task_data(findings=many)).read_text(encoding="utf-8")
    assert text.count("[F-") == _FINDINGS_CAP
    assert "5 more" in text
