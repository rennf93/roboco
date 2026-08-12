"""roboco.services.gateway.content_actions.propose_messaging_fixes — HoM-gated
Mirror audit authoring. Mirrors test_content_actions_spackle.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``markers`` and ``propose_messaging_fixes`` touch."""

    def __init__(
        self,
        *,
        assigned_to: Any,
        orchestration_markers: dict[str, Any] | None = None,
    ) -> None:
        self.id = uuid4()
        self.assigned_to = assigned_to
        self.orchestration_markers = orchestration_markers


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


def _valid_item(idx: int) -> dict[str, Any]:
    return {
        "title": f"Item {idx}",
        "description": f"A substantive description of item {idx}",
        "acceptance_criteria": ["it does the thing", "it is tested"],
        "project_slug": "backend-svc",
        "team": "backend",
        "priority": 2,
        "evidence": (
            f"README.md:4{idx} claims real-time sync; "
            f"roboco/services/sync.py:8{idx} polls every 30s"
        ),
    }


def _valid_items(n: int) -> list[dict[str, Any]]:
    return [_valid_item(i) for i in range(n)]


@pytest.fixture(autouse=True)
def _default_project_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-in participation check looks up ``project_slug`` on every
    propose_messaging_fixes call. Every test predating that check builds its
    ``ContentActions`` with a bare ``MagicMock`` session, so default the
    lookup to "unresolvable" (None -> not rejected, same as an unknown slug
    always behaves) instead of making every one of them mock a project
    service it isn't testing. Tests exercising the gate override this."""
    stub = MagicMock()
    stub.get_by_slug = AsyncMock(return_value=None)
    monkeypatch.setattr("roboco.services.project.get_project_service", lambda _s: stub)


@pytest.mark.asyncio
async def test_propose_messaging_fixes_forbidden_for_non_hom() -> None:
    env = await _actions("product_owner").propose_messaging_fixes(
        agent_id=uuid4(), items=_valid_items(2)
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_forbidden_for_developer() -> None:
    env = await _actions("developer").propose_messaging_fixes(
        agent_id=uuid4(), items=_valid_items(2)
    )
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_rejects_empty_items() -> None:
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=[]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_rejects_too_many_items() -> None:
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=_valid_items(6)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_rejects_missing_evidence() -> None:
    bad = _valid_item(0)
    del bad["evidence"]
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "evidence" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_messaging_fixes_rejects_oversized_evidence() -> None:
    bad = _valid_item(0)
    bad["evidence"] = "x" * 2001
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_rejects_too_short_evidence() -> None:
    """Below the 20-char anti-soup floor (``_MESSAGING_FIX_ITEM_TEXT_FIELDS``)
    — distinct from the missing-field and oversized-evidence checks above."""
    bad = _valid_item(0)
    bad["evidence"] = "too short"  # 9 chars, under the 20-char floor
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "item 0" in (env.message or "")
    assert "evidence" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_messaging_fixes_rejects_placeholder_soup_evidence() -> None:
    """Past the 20-char length floor but every token is a banned placeholder
    — the soup check, not the length check, must be what rejects it."""
    bad = _valid_item(0)
    bad["evidence"] = "tbd tbd tbd tbd tbd tbd"  # 23 chars, all filler tokens
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"
    assert "item 0" in (env.message or "")
    assert "evidence" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_messaging_fixes_rejects_missing_field() -> None:
    bad = _valid_item(0)
    del bad["description"]
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_rejects_unknown_team() -> None:
    bad = _valid_item(0)
    bad["team"] = "board"  # not a cell team
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=[bad]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_no_open_cycle_is_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_svc = MagicMock()
    task_svc.list_open_mirror_cycles = AsyncMock(return_value=[])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=_valid_items(2)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_persists_audit_onto_open_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_mirror_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    actions = _actions("head_marketing")
    actions.task.session.flush = AsyncMock()

    items = _valid_items(3)
    env = await actions.propose_messaging_fixes(agent_id=agent_id, items=items)
    assert env.error is None
    assert env.status == "messaging_fixes_proposed"
    assert env.task_id == str(cycle_task.id)

    payload = markers.get_messaging_fixes(cycle_task)
    assert payload is not None
    assert len(payload["items"]) == len(items)
    assert all(it["status"] == "proposed" for it in payload["items"])
    assert payload["items"][0]["id"] == "item-0"
    assert payload["items"][0]["evidence"]
    actions.task.session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_propose_messaging_fixes_sends_telegram_push_per_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_mirror_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = AsyncMock()
    actions = _actions("head_marketing", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    items = _valid_items(2)
    env = await actions.propose_messaging_fixes(agent_id=agent_id, items=items)
    assert env.error is None

    assert notify.notify_ceo_of_queue_item.await_count == len(items)
    id8 = str(cycle_task.id)[:8]
    for i, call in enumerate(notify.notify_ceo_of_queue_item.await_args_list):
        assert call.kwargs["kind"] == "mirror"
        assert call.kwargs["id8"] == id8
        assert call.kwargs["extra"] == f"item-{i}"
        assert call.kwargs["title"] == f"Item {i}"
        # DEFECT 2 regression: without related_task_id the row can never
        # auto-resolve once every item on the cycle is decided.
        assert call.kwargs["related_task_id"] == cycle_task.id


@pytest.mark.asyncio
async def test_propose_messaging_fixes_survives_telegram_push_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_mirror_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    notify = MagicMock()
    notify.notify_ceo_of_queue_item = AsyncMock(side_effect=RuntimeError("boom"))
    actions = _actions("head_marketing", notification_delivery=notify)
    actions.task.session.flush = AsyncMock()

    env = await actions.propose_messaging_fixes(
        agent_id=agent_id, items=_valid_items(1)
    )

    assert env.error is None
    assert env.status == "messaging_fixes_proposed"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_ignores_cycle_assigned_to_another_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_agent = uuid4()
    cycle_task = _FakeTask(assigned_to=other_agent)
    task_svc = MagicMock()
    task_svc.list_open_mirror_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=uuid4(), items=_valid_items(2)
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_propose_messaging_fixes_rejects_item_targeting_unopted_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive-gate mirror of propose_gap_fill's exclusion check: an
    item targeting a project that has NOT opted into mirror is refused at
    propose time, naming the project."""
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_mirror_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    unopted_project = MagicMock(board_programs=None)
    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=unopted_project)
    monkeypatch.setattr(
        "roboco.services.project.get_project_service", lambda _s: project_svc
    )

    bad = _valid_item(0)
    bad["project_slug"] = "unopted-proj"
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=agent_id, items=[bad]
    )
    assert env.error == "invalid_state"
    assert "unopted-proj" in (env.message or "")


@pytest.mark.asyncio
async def test_propose_messaging_fixes_allows_opted_project_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_mirror_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    opted_project = MagicMock(board_programs=["mirror"])
    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=opted_project)
    monkeypatch.setattr(
        "roboco.services.project.get_project_service", lambda _s: project_svc
    )

    actions = _actions("head_marketing")
    actions.task.session.flush = AsyncMock()
    bad = _valid_item(0)
    bad["project_slug"] = "opted-proj"
    env = await actions.propose_messaging_fixes(agent_id=agent_id, items=[bad])
    assert env.error is None


@pytest.mark.asyncio
async def test_propose_messaging_fixes_allows_unresolvable_project_slug_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown project_slug is not this check's job — it surfaces
    downstream at approve/materialize time."""
    agent_id = uuid4()
    cycle_task = _FakeTask(assigned_to=agent_id)
    task_svc = MagicMock()
    task_svc.list_open_mirror_cycles = AsyncMock(return_value=[cycle_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)

    project_svc = MagicMock()
    project_svc.get_by_slug = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "roboco.services.project.get_project_service", lambda _s: project_svc
    )

    actions = _actions("head_marketing")
    actions.task.session.flush = AsyncMock()
    bad = _valid_item(0)
    bad["project_slug"] = "no-such-project"
    env = await actions.propose_messaging_fixes(agent_id=agent_id, items=[bad])
    assert env.error is None


@pytest.mark.asyncio
async def test_propose_messaging_fixes_ignores_already_authored_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    authored_task = _FakeTask(
        assigned_to=agent_id,
        orchestration_markers={"messaging_fixes": {"items": []}},
    )
    task_svc = MagicMock()
    task_svc.list_open_mirror_cycles = AsyncMock(return_value=[authored_task])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _s: task_svc)
    env = await _actions("head_marketing").propose_messaging_fixes(
        agent_id=agent_id, items=_valid_items(2)
    )
    assert env.error == "invalid_state"
