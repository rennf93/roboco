"""Tests for note(scope='handoff') — the role note-section write-path.

``note()`` only ever wrote the JOURNAL; ``scope='handoff'`` is how an agent
authors its dedicated SECTION (dev_notes / quick_context / auditor_notes …)
through the ``apply_structured_note`` chokepoint. These cover the routing,
ownership, validation→remediation, and journal-trail behaviours.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import ContentValidationError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _make_deps(**overrides: object) -> ContentActionsDeps:
    task = overrides.get("task") or AsyncMock()
    return ContentActionsDeps(
        task=task,
        git=overrides.get("git") or AsyncMock(),
        messaging=overrides.get("messaging") or AsyncMock(),
        a2a=overrides.get("a2a") or AsyncMock(),
        journal=overrides.get("journal") or AsyncMock(),
        workspace=overrides.get("workspace") or AsyncMock(),
        notifications=overrides.get("notifications") or AsyncMock(),
        notification_delivery=overrides.get("notification_delivery") or AsyncMock(),
        evidence_repo=overrides.get("evidence_repo") or AsyncMock(),
    )


def _dev_task_svc(task_id: object, role: str = "developer") -> AsyncMock:
    """A task service whose active/context task is owned by the caller."""
    svc = AsyncMock()
    svc.agent_for.return_value = MagicMock(role=role)
    svc.get_journal_context_task_for_agent.return_value = MagicMock(id=task_id)
    svc.record_section_note.return_value = None
    return svc


@pytest.mark.asyncio
async def test_handoff_developer_writes_dev_notes_from_text() -> None:
    """A developer handoff routes to the 'developer' content type, defaulting
    the payload to {'summary': text} when no explicit section is given."""
    agent_id, task_id = uuid4(), uuid4()
    svc = _dev_task_svc(task_id)
    ca = ContentActions(_make_deps(task=svc))

    summary = "Implemented the endpoint and added happy-path tests."
    env = await ca.note(agent_id=agent_id, text=summary, scope="handoff")

    assert env.as_dict()["error"] is None
    svc.record_section_note.assert_awaited_once()
    called_task_id, content_type, payload = svc.record_section_note.call_args.args
    assert called_task_id == task_id
    assert content_type == "developer"
    assert payload == {"summary": summary}


@pytest.mark.asyncio
async def test_handoff_passes_explicit_section_through() -> None:
    """An explicit ``section`` dict is the payload (e.g. PM resumption)."""
    agent_id, task_id = uuid4(), uuid4()
    svc = _dev_task_svc(task_id, role="cell_pm")
    ca = ContentActions(_make_deps(task=svc))

    section = {"done": "Planned the decomposition.", "next": "Cells implement."}
    env = await ca.note(
        agent_id=agent_id, text="handoff", scope="handoff", section=section
    )

    assert env.as_dict()["error"] is None
    _tid, content_type, payload = svc.record_section_note.call_args.args
    assert content_type == "resumption"
    assert payload == section


@pytest.mark.asyncio
async def test_handoff_also_writes_journal_trail_entry() -> None:
    """The section write drops a journal trail entry so it shows in the log."""
    agent_id, task_id = uuid4(), uuid4()
    svc = _dev_task_svc(task_id)
    journal = AsyncMock()
    ca = ContentActions(_make_deps(task=svc, journal=journal))

    await ca.note(agent_id=agent_id, text="Did the thing thoroughly.", scope="handoff")

    journal.write_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_handoff_role_without_section_is_rejected() -> None:
    """A role with no dedicated section (board/advisory) cannot handoff."""
    agent_id = uuid4()
    svc = AsyncMock()
    svc.agent_for.return_value = MagicMock(role="product_owner")
    ca = ContentActions(_make_deps(task=svc))

    env = await ca.note(agent_id=agent_id, text="observation", scope="handoff")
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "no dedicated note section" in body["message"]
    svc.record_section_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_validation_error_returns_remediation_not_422() -> None:
    """A malformed section payload becomes a remediation Envelope, never a 422
    (a raw 422 would trip the do-server circuit breaker)."""
    agent_id, task_id = uuid4(), uuid4()
    svc = _dev_task_svc(task_id, role="auditor")
    svc.record_section_note.side_effect = ContentValidationError(
        "severity", "field required"
    )
    ca = ContentActions(_make_deps(task=svc))

    env = await ca.note(
        agent_id=agent_id,
        text="risk spotted",
        scope="handoff",
        section={"summary": "x"},
    )
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "severity" in body["message"]
    assert "auditor" in body["remediate"]


@pytest.mark.asyncio
async def test_handoff_pm_resumption_from_top_level_done_next() -> None:
    """A PM authors the resumption section from TOP-LEVEL ``done``/``next``
    fields, not the nested ``section`` dict.

    Background (2026-06-27 live meltdown): minimax-m3:cloud running the PM
    roles emitted ``section={}`` (an empty object) for the handoff note 3
    times identically, ignoring the prose remediate. ``ResumptionNote`` requires
    ``done``+``next`` so the gate rejected ``done — Field required``, the
    do-server circuit breaker tripped after 3-4 rejections, and the tracing
    gate (which obligates a handoff note before ``delegate``) deadlocked the
    PM into a respawn loop. Root cause was structural: ``section: dict[str,
    Any]`` renders a tool schema with NO visible sub-fields, so a weak model
    emits ``{}`` while the SAME model fills the top-level ``string`` decision
    fields fine (proven live — those succeeded). The fix follows the
    precedent already in ``NoteRequest`` (the decision fields typed ``str =
    ""`` so the schema declares ``string``): promote ``done``/``next`` to
    top-level typed string params so the tool schema shows them
    machine-visible. Passing ``done``+``next`` with no ``section`` must write
    the resumption section successfully.
    """
    agent_id, task_id = uuid4(), uuid4()
    svc = _dev_task_svc(task_id, role="cell_pm")
    ca = ContentActions(_make_deps(task=svc))

    env = await ca.note(
        agent_id=agent_id,
        text="handoff",
        scope="handoff",
        done="Planned the decomposition into 3 cell tasks.",
        next="Cells implement their slices in wave order.",
        where_to_look=["panel/tasks — wave column", "the decomposition note"],
    )

    body = env.as_dict()
    assert body["error"] is None
    _tid, content_type, payload = svc.record_section_note.call_args.args
    assert content_type == "resumption"
    assert payload["done"] == "Planned the decomposition into 3 cell tasks."
    assert payload["next"] == "Cells implement their slices in wave order."
    assert payload["where_to_look"] == [
        "panel/tasks — wave column",
        "the decomposition note",
    ]


@pytest.mark.asyncio
async def test_handoff_top_level_done_next_merge_into_explicit_section() -> None:
    """Top-level ``done``/``next`` fill any keys the explicit ``section`` omits
    without overwriting keys the agent already supplied — backward compatible
    with a capable model that passes ``section`` directly."""
    agent_id, task_id = uuid4(), uuid4()
    svc = _dev_task_svc(task_id, role="cell_pm")
    ca = ContentActions(_make_deps(task=svc))

    env = await ca.note(
        agent_id=agent_id,
        text="handoff",
        scope="handoff",
        section={"done": "Already-specified done."},
        next="Top-level next fills the omitted next.",
    )

    assert env.as_dict()["error"] is None
    _tid, _ctype, payload = svc.record_section_note.call_args.args
    assert payload["done"] == "Already-specified done."
    assert payload["next"] == "Top-level next fills the omitted next."


@pytest.mark.asyncio
async def test_handoff_empty_done_next_empty_section_still_remediates() -> None:
    """The backstop holds: with no top-level ``done``/``next`` AND an empty
    ``section``, the resumption gate still rejects with the remediation
    envelope (never a raw 422) — so a model that ignores both paths still
    gets the actionable message rather than tripping a silent crash."""
    agent_id, task_id = uuid4(), uuid4()
    svc = _dev_task_svc(task_id, role="cell_pm")
    svc.record_section_note.side_effect = ContentValidationError(
        "done", "field required"
    )
    ca = ContentActions(_make_deps(task=svc))

    env = await ca.note(agent_id=agent_id, text="handoff", scope="handoff", section={})
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "done" in body["message"]
    assert "resumption" in body["remediate"]


@pytest.mark.asyncio
async def test_handoff_no_task_to_attach_is_rejected() -> None:
    """With no active/context task and no explicit task_id, handoff refuses."""
    agent_id = uuid4()
    svc = AsyncMock()
    svc.agent_for.return_value = MagicMock(role="developer")
    svc.get_journal_context_task_for_agent.return_value = None
    ca = ContentActions(_make_deps(task=svc))

    env = await ca.note(agent_id=agent_id, text="orphan note", scope="handoff")
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "task_id" in body["remediate"]
    svc.record_section_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_explicit_task_not_owned_is_rejected() -> None:
    """An explicit task_id the caller does not own is an ownership violation."""
    agent_id, task_id = uuid4(), uuid4()
    svc = AsyncMock()
    svc.agent_for.return_value = MagicMock(role="developer")
    svc.get.return_value = MagicMock(
        id=task_id, assigned_to=uuid4(), project_id=uuid4(), product_id=None
    )
    ca = ContentActions(_make_deps(task=svc))

    env = await ca.note(
        agent_id=agent_id, text="poking another task", scope="handoff", task_id=task_id
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    svc.record_section_note.assert_not_awaited()
