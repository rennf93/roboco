"""Cognition-lane tests: the A2A steer gate seam, steering-note rendering,
the briefing block, and the B28 transcript auto-notes pass."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services import decisions
from roboco.services.a2a import A2AService, render_steering_note
from roboco.services.decisions import SteerMode


def _svc() -> A2AService:
    return A2AService(MagicMock())


def _bare_engine() -> Any:
    """A SpawnExitEngine without __init__ (no DB). The class carries a
    TYPE_CHECKING-only Protocol base (abstract at type-check time), so the
    class object is routed through a type[Any] intermediate."""
    from roboco.runtime.engines.spawn_exit import SpawnExitEngine

    cls: type[Any] = SpawnExitEngine
    return object.__new__(cls)


# ---------------------------------------------------------------------------
# Steering note rendering
# ---------------------------------------------------------------------------


def test_switch_consideration_note_carries_the_task_list() -> None:
    note = render_steering_note(
        mode=SteerMode.STEER_SWITCH_CONSIDERATION,
        sender="backend-pm-1",
        content="Pivot to the API redesign first",
        recipient_context={
            "current_task_id": "T1",
            "current_task_title": "Fix nginx mount",
            "current_task_status": "in_progress",
            "other_claimed_or_parked_tasks": {
                "count": 2,
                "titles": ["API redesign", "Docs pass"],
            },
        },
    )
    assert "weigh" in note.lower()
    assert "Pivot to the API redesign first" in note
    assert "Fix nginx mount" in note
    assert "API redesign" in note
    assert "your PM's" in note  # the switch decision is not the verdict's


def test_steer_now_note_is_task_scoped() -> None:
    note = render_steering_note(
        mode=SteerMode.STEER_NOW,
        sender="qa-1",
        content="Use v2 of the API",
        recipient_context={"current_task_id": "T1"},
    )
    assert "current task" in note.lower()
    assert "Use v2 of the API" in note


# ---------------------------------------------------------------------------
# The steer gate seam in send_chat_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steer_mode_skips_ceo_conversations() -> None:
    assert (
        await _svc()._decisions_steer_mode(
            conversation_id=MagicMock(),
            sender="ceo",
            recipient="backend-dev-1",
            content="x",
            requires_response=False,
            purpose=None,
        )
        is None
    )
    assert (
        await _svc()._decisions_steer_mode(
            conversation_id=MagicMock(),
            sender="backend-dev-1",
            recipient="ceo",
            content="x",
            requires_response=False,
            purpose=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_steer_mode_off_when_pilot_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        decisions, "pilot_mode", AsyncMock(return_value=decisions.PilotMode.OFF)
    )
    assert (
        await _svc()._decisions_steer_mode(
            conversation_id=MagicMock(),
            sender="a",
            recipient="b",
            content="x",
            requires_response=False,
            purpose=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_steer_mode_attaches_confident_steering_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _svc()
    monkeypatch.setattr(
        decisions, "pilot_mode", AsyncMock(return_value=decisions.PilotMode.ON)
    )
    monkeypatch.setattr(
        "roboco.services.decisions.context.recipient_work_context",
        AsyncMock(return_value={}),
    )
    # _decisions_steer_mode calls the PACKAGE re-export (roboco.services.
    # decisions.steer_gate), not the submodule attribute.
    monkeypatch.setattr(
        decisions, "steer_gate", AsyncMock(return_value=SteerMode.STEER_NOW)
    )
    mode = await svc._decisions_steer_mode(
        conversation_id=MagicMock(),
        sender="a",
        recipient="b",
        content="x",
        requires_response=False,
        purpose=None,
    )
    assert mode is SteerMode.STEER_NOW


@pytest.mark.asyncio
async def test_steer_mode_fail_open_on_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _svc()
    monkeypatch.setattr(
        decisions, "pilot_mode", AsyncMock(side_effect=RuntimeError("db down"))
    )
    mode = await svc._decisions_steer_mode(
        conversation_id=MagicMock(),
        sender="a",
        recipient="b",
        content="x",
        requires_response=False,
        purpose=None,
    )
    assert mode is None


# ---------------------------------------------------------------------------
# B28 transcript auto-notes
# ---------------------------------------------------------------------------


def _write_fake_transcript(home: Path, agent_id: str, segments: list[str]) -> Path:
    project_dir = home / ".claude" / "projects" / f"-app-{agent_id}"
    project_dir.mkdir(parents=True)
    lines = []
    for text in segments:
        lines.append(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                    }
                }
            )
        )
    (project_dir / "session-abc.jsonl").write_text("\n".join(lines))
    return project_dir / "session-abc.jsonl"


def test_assistant_segments_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roboco.runtime.engines.spawn_exit import SpawnExitEngine

    _write_fake_transcript(
        tmp_path,
        "backend-dev-1",
        [
            "x" * 250,  # long enough
            "too short",
        ],
    )
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    segments = SpawnExitEngine._assistant_segments_from_transcript("backend-dev-1")
    assert len(segments) == 1
    assert segments[0] == "x" * 250


@pytest.mark.asyncio
async def test_capture_transcript_notes_off_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = _bare_engine()
    engine._instances = {}
    _write_fake_transcript(tmp_path, "backend-dev-1", ["x" * 250])
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

    import roboco.db.base as db_base

    def boom() -> None:
        raise RuntimeError("should not open a session when off")

    monkeypatch.setattr(db_base, "get_session_factory", boom)
    # Must not raise: off mode returns before any DB work.
    await engine._capture_transcript_notes("backend-dev-1")


@pytest.mark.asyncio
async def test_capture_transcript_notes_writes_journal_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = _bare_engine()
    instance = MagicMock()
    instance.current_task_id = None
    engine._instances = {"backend-dev-1": instance}
    _write_fake_transcript(
        tmp_path,
        "backend-dev-1",
        [
            "The API contract requires lowercase slugs throughout the "
            "gateway: the commit validator rejects uppercase, the envelope "
            "builder normalizes them, and the PR template must keep the "
            "convention. This constraint came up repeatedly this session."
        ],
    )
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

    import roboco.db.base as db_base

    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value="agent-uuid"))
    )
    written: list[Any] = []

    class _FakeJournalSvc:
        async def add_general_entry(self, agent_uuid: Any, params: Any) -> None:
            written.append(params)

        # get_or_create_journal is called inside add_general_entry; bypass by
        # having add_general_entry fully fake above.

    class _FakeFactory:
        def __call__(self) -> MagicMock:
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=db)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

    monkeypatch.setattr(db_base, "get_session_factory", _FakeFactory())
    import roboco.services.decisions.pilots as pilots_mod

    monkeypatch.setattr(
        pilots_mod, "pilot_mode", AsyncMock(return_value=pilots_mod.PilotMode.ON)
    )
    monkeypatch.setattr(
        pilots_mod,
        "transcript_note_worthy",
        AsyncMock(return_value=[True]),
    )
    import roboco.services.journal as journal_mod

    monkeypatch.setattr(
        journal_mod, "get_journal_service", lambda db: _FakeJournalSvc()
    )
    await engine._capture_transcript_notes("backend-dev-1")
    assert len(written) == 1
    assert written[0].tags == ["auto-note"]
    assert "lowercase slugs" in written[0].content
