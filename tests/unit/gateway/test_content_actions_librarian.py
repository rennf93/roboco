"""roboco.services.gateway.content_actions.propose_playbook_drafts —
Auditor-gated Librarian playbook-mining authoring. Mirrors
test_content_actions_sentinel.py for the validation truth table and the
complete-at-propose asymmetry, and test_content_actions_coroner.py for the
direct-PlaybookService-draft precedent (never the draft_playbook do-verb)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import ConflictError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps

TWO = 2


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``propose_playbook_drafts`` touches."""

    def __init__(
        self,
        *,
        assigned_to: Any,
        orchestration_markers: dict[str, Any] | None = None,
        status: Any = TaskStatus.PENDING,
    ) -> None:
        self.id = uuid4()
        self.assigned_to = assigned_to
        self.orchestration_markers = orchestration_markers
        self.status = status


def _actions(role: str, *, notification_delivery: Any = None) -> ContentActions:
    task = MagicMock()
    agent = MagicMock()
    agent.role = role
    task.agent_for = AsyncMock(return_value=agent)
    task.session = MagicMock()
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
        notification_delivery=notification_delivery,
    )
    return ContentActions(deps)


def _valid_draft(idx: int) -> dict[str, Any]:
    return {
        "title": f"Playbook title number {idx}",
        "body": f"A substantive procedure body for draft {idx} " * 3,
        "pattern_evidence": f"Recurred across at least two journal entries {idx}",
    }


def _valid_drafts(n: int) -> list[dict[str, Any]]:
    return [_valid_draft(i) for i in range(n)]


def _no_existing_titles() -> Any:
    """Patch target for LibrarianEngine.existing_playbook_titles_lower —
    empty by default so the field-validation truth table below isolates each
    check without also needing a live DB dedup pass."""
    engine = MagicMock()
    engine.existing_playbook_titles_lower = AsyncMock(return_value=set())
    return patch(
        "roboco.services.librarian_engine.get_librarian_engine",
        return_value=engine,
    )


# --------------------------------------------------------------------------- #
# Role gate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_playbook_drafts_forbidden_for_product_owner() -> None:
    env = await _actions("product_owner").propose_playbook_drafts(
        agent_id=uuid4(), drafts=_valid_drafts(1)
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_forbidden_for_head_marketing() -> None:
    env = await _actions("head_marketing").propose_playbook_drafts(
        agent_id=uuid4(), drafts=_valid_drafts(1)
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_forbidden_for_developer() -> None:
    env = await _actions("developer").propose_playbook_drafts(
        agent_id=uuid4(), drafts=_valid_drafts(1)
    )
    assert env.error == "not_authorized"


# --------------------------------------------------------------------------- #
# Count bounds (1-3)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_empty_drafts() -> None:
    env = await _actions("auditor").propose_playbook_drafts(agent_id=uuid4(), drafts=[])
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_too_many_drafts() -> None:
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=_valid_drafts(4)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_accepts_three_drafts_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=_valid_drafts(3)
    )
    # No open cycle exists — proves the count itself was accepted (3 is the
    # upper bound, not rejected by the count check).
    assert env.error == "invalid_state"
    assert "no open Librarian mining task" in (env.message or "")


# --------------------------------------------------------------------------- #
# Per-draft field validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_non_dict_draft() -> None:
    bad_drafts: list[Any] = ["not a dict"]
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=bad_drafts
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_draft_missing_title() -> None:
    bad = _valid_draft(0)
    del bad["title"]
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=[bad]
    )
    assert env.error == "invalid_state"
    assert "title" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_draft_missing_body() -> None:
    bad = _valid_draft(0)
    del bad["body"]
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=[bad]
    )
    assert env.error == "invalid_state"
    assert "body" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_draft_missing_pattern_evidence() -> None:
    bad = _valid_draft(0)
    del bad["pattern_evidence"]
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=[bad]
    )
    assert env.error == "invalid_state"
    assert "pattern_evidence" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_soup_title() -> None:
    bad = _valid_draft(0)
    bad["title"] = "asdf"
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_oversized_title() -> None:
    bad = _valid_draft(0)
    bad["title"] = "x" * 201
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_oversized_body() -> None:
    bad = _valid_draft(0)
    bad["body"] = "x" * 4001
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_oversized_pattern_evidence() -> None:
    bad = _valid_draft(0)
    bad["pattern_evidence"] = "x" * 501
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_thin_pattern_evidence() -> None:
    bad = _valid_draft(0)
    bad["pattern_evidence"] = "too short"
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=[bad]
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# In-batch duplicate-title dedup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_duplicate_titles_in_batch() -> None:
    first = _valid_draft(0)
    second = _valid_draft(1)
    second["title"] = first["title"].upper()  # case-insensitive collision
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=[first, second]
    )
    assert env.error == "invalid_state"
    assert "duplicates another draft in this batch" in (env.message or "")


# --------------------------------------------------------------------------- #
# Open-cycle lookup
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_playbook_drafts_no_open_cycle_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=_valid_drafts(1)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_ignores_cycle_assigned_to_another_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_agent = uuid4()
    cycle_task = _FakeTask(assigned_to=other_agent)
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=uuid4(), drafts=_valid_drafts(1)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_ignores_already_authored_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    authored = _FakeTask(
        assigned_to=agent_id,
        orchestration_markers={"playbook_drafts": {"drafts": []}},
    )
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[authored])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("auditor").propose_playbook_drafts(
        agent_id=agent_id, drafts=_valid_drafts(1)
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Existing-title dedup (live DB check, past the field-validation stage)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_playbook_drafts_rejects_title_duplicating_existing_playbook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    draft = _valid_draft(0)
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    engine = MagicMock()
    engine.existing_playbook_titles_lower = AsyncMock(
        return_value={draft["title"].lower()}
    )
    with patch(
        "roboco.services.librarian_engine.get_librarian_engine",
        return_value=engine,
    ):
        env = await _actions("auditor").propose_playbook_drafts(
            agent_id=agent_id, drafts=[draft]
        )
    assert env.error == "invalid_state"
    assert "duplicates an existing playbook" in (env.message or "")


# --------------------------------------------------------------------------- #
# Happy path — direct PlaybookService.draft(), never draft_playbook
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_propose_playbook_drafts_creates_playbooks_and_completes_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("auditor")
    actions.task.session.flush = AsyncMock()

    drafted_1, drafted_2 = MagicMock(), MagicMock()
    drafted_1.id, drafted_1.title = uuid4(), "Playbook title number 0"
    drafted_2.id, drafted_2.title = uuid4(), "Playbook title number 1"
    playbook_svc = MagicMock()
    playbook_svc.draft = AsyncMock(side_effect=[drafted_1, drafted_2])

    with (
        _no_existing_titles(),
        patch(
            "roboco.services.playbook.get_playbook_service",
            return_value=playbook_svc,
        ),
    ):
        env = await actions.propose_playbook_drafts(
            agent_id=agent_id, drafts=_valid_drafts(2)
        )

    assert env.error is None
    assert env.status == "playbook_drafts_proposed"
    assert env.task_id == str(cycle_task.id)
    assert playbook_svc.draft.await_count == TWO

    payload = markers.get_playbook_drafts(cycle_task)
    assert payload is not None
    assert len(payload["drafts"]) == TWO
    assert payload["drafts"][0]["title"] == "Playbook title number 0"

    # Complete-at-propose: the mining task completes in THIS call — mirrors
    # the x_feature/periscope/sentinel asymmetry.
    assert cycle_task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_propose_playbook_drafts_calls_playbook_service_draft_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The invariant this whole verb exists to preserve: each playbook is
    created directly via PlaybookService.draft() — the do-verb never routes
    through the draft_playbook content-tool path (which the Auditor doesn't
    carry on its manifest, see test_playbook_verbs.py's invariant)."""
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("auditor")
    actions.task.session.flush = AsyncMock()

    drafted = MagicMock()
    drafted.id, drafted.title = uuid4(), "Playbook title number 0"
    playbook_svc = MagicMock()
    playbook_svc.draft = AsyncMock(return_value=drafted)

    with (
        _no_existing_titles(),
        patch(
            "roboco.services.playbook.get_playbook_service",
            return_value=playbook_svc,
        ) as get_playbook_svc,
    ):
        env = await actions.propose_playbook_drafts(
            agent_id=agent_id, drafts=_valid_drafts(1)
        )

    assert env.error is None
    get_playbook_svc.assert_called_once()
    playbook_svc.draft.assert_awaited_once()
    create_call = playbook_svc.draft.await_args
    assert create_call.kwargs["created_by"] == agent_id
    # DEFECT 1 regression: created_by alone can't discriminate a Librarian
    # draft from a Coroner one (same fixed Auditor identity), the call
    # site must stamp its own program explicitly.
    assert create_call.kwargs["source_program"] == "librarian"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_conflict_error_is_clean_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-tick race past the live pre-check (ConflictError from
    PlaybookService.draft itself) aborts with a clean rejection — mirrors
    test_propose_postmortem_playbook_title_conflict_is_clean_rejection."""
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("auditor")
    actions.task.session.flush = AsyncMock()

    playbook_svc = MagicMock()
    playbook_svc.draft = AsyncMock(
        side_effect=ConflictError("dup slug", resource_type="playbook")
    )

    with (
        _no_existing_titles(),
        patch(
            "roboco.services.playbook.get_playbook_service",
            return_value=playbook_svc,
        ),
    ):
        env = await actions.propose_playbook_drafts(
            agent_id=agent_id, drafts=_valid_drafts(1)
        )

    assert env.error == "invalid_state"
    assert cycle_task.status != TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_propose_playbook_drafts_sends_telegram_push_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = AsyncMock()
    actions = _actions("auditor", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    drafted = MagicMock()
    drafted.id, drafted.title = uuid4(), "Playbook title number 0"
    playbook_svc = MagicMock()
    playbook_svc.draft = AsyncMock(return_value=drafted)

    with (
        _no_existing_titles(),
        patch(
            "roboco.services.playbook.get_playbook_service",
            return_value=playbook_svc,
        ),
    ):
        env = await actions.propose_playbook_drafts(
            agent_id=agent_id, drafts=_valid_drafts(1)
        )
    assert env.error is None

    notify.notify_ceo_of_librarian_drafts.assert_awaited_once()
    call = notify.notify_ceo_of_librarian_drafts.await_args
    assert call.kwargs["task"] is cycle_task
    assert call.kwargs["task_id"] == cycle_task.id
    assert call.kwargs["titles"] == ["Playbook title number 0"]


@pytest.mark.asyncio
async def test_propose_playbook_drafts_survives_telegram_push_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = MagicMock()
    notify.notify_ceo_of_librarian_drafts = AsyncMock(side_effect=RuntimeError("boom"))
    actions = _actions("auditor", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    drafted = MagicMock()
    drafted.id, drafted.title = uuid4(), "Playbook title number 0"
    playbook_svc = MagicMock()
    playbook_svc.draft = AsyncMock(return_value=drafted)

    with (
        _no_existing_titles(),
        patch(
            "roboco.services.playbook.get_playbook_service",
            return_value=playbook_svc,
        ),
    ):
        env = await actions.propose_playbook_drafts(
            agent_id=agent_id, drafts=_valid_drafts(1)
        )

    assert env.error is None
    assert env.status == "playbook_drafts_proposed"


@pytest.mark.asyncio
async def test_propose_playbook_drafts_no_notification_delivery_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_librarian_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("auditor", notification_delivery=None)
    actions.task.session.flush = AsyncMock()

    drafted = MagicMock()
    drafted.id, drafted.title = uuid4(), "Playbook title number 0"
    playbook_svc = MagicMock()
    playbook_svc.draft = AsyncMock(return_value=drafted)

    with (
        _no_existing_titles(),
        patch(
            "roboco.services.playbook.get_playbook_service",
            return_value=playbook_svc,
        ),
    ):
        env = await actions.propose_playbook_drafts(
            agent_id=agent_id, drafts=_valid_drafts(1)
        )
    assert env.error is None
