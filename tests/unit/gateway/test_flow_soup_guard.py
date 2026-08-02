"""Anti-soup guard on the flow verbs (reason / notes / issues / title / desc).

Two layers are tested:

- the pure helpers ``_free_text_soup`` (skip empty/None, reject filler, walk
  list items) and ``_soup_or_decision_env`` (soup first, then the spec
  decision, else None);
- one end-to-end wiring test per emit style — ``i_am_blocked`` (folds soup into
  the spec-gate return) — proving a soupy ``reason`` is rejected before any
  state transition and a real reason passes through.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer._impl import Choreographer as _Impl
from roboco.services.gateway.envelope import Envelope

# --------------------------------------------------------------------------- #
# _free_text_soup
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("clean", ["a real substantive reason", "rate_limited"])
def test_free_text_soup_passes_substantive(clean: str) -> None:
    assert _Impl._free_text_soup((("reason", clean, 8),)) is None


@pytest.mark.parametrize("skip", [None, "", "   "])
def test_free_text_soup_skips_empty_and_none(skip: str | None) -> None:
    # Empty / None means "not supplied" — presence is gated elsewhere.
    assert _Impl._free_text_soup((("notes", skip, 8),)) is None


@pytest.mark.parametrize("soup", ["wip", "asdf", "tbd", "...", "x", "wip wip"])
def test_free_text_soup_rejects_filler(soup: str) -> None:
    env = _Impl._free_text_soup((("reason", soup, 3),))
    assert env is not None
    assert env.error == "invalid_state"


def test_free_text_soup_walks_list_items() -> None:
    # The second issue is filler — the list form must catch it.
    env = _Impl._free_text_soup(
        (("issues", ["a genuine actionable issue", "asdf"], 8),)
    )
    assert env is not None
    assert env.error == "invalid_state"
    assert "issues[1]" in (env.message or "")


def test_free_text_soup_clean_list_passes() -> None:
    env = _Impl._free_text_soup(
        (("issues", ["first real issue", "second real issue"], 8),)
    )
    assert env is None


# --------------------------------------------------------------------------- #
# _soup_or_decision_env
# --------------------------------------------------------------------------- #


def _allow() -> MagicMock:
    return MagicMock(allowed=True)


def _deny() -> MagicMock:
    return MagicMock(
        allowed=False,
        rejection_kind="invalid_state",
        message="bad state",
        remediate="do X",
    )


def test_soup_or_decision_prefers_soup() -> None:
    soup = Envelope.invalid_state(message="soup", remediate="fix", context_briefing={})
    out = _Impl._soup_or_decision_env(soup, _deny(), {})
    assert out is soup  # soup wins even when the decision also rejects


def test_soup_or_decision_falls_back_to_decision() -> None:
    out = _Impl._soup_or_decision_env(None, _deny(), {})
    assert out is not None
    assert out.error == "invalid_state"
    assert out.message == "bad state"


def test_soup_or_decision_none_when_all_clean() -> None:
    assert _Impl._soup_or_decision_env(None, _allow(), {}) is None


# --------------------------------------------------------------------------- #
# Wiring: i_am_blocked rejects a soupy reason before any transition
# --------------------------------------------------------------------------- #


def _make_deps(agent_id: object, task_id: object) -> ChoreographerDeps:
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        task_type="code",
        team="backend",
        dependency_ids=[],
        acceptance_criteria=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug="be-dev-1"
    )
    # VerbRunner uses task.session.begin_nested() as a savepoint context
    # manager — an unconfigured AsyncMock's `begin_nested()` call returns a
    # raw unawaited coroutine, which `async with` cannot use (real failure,
    # not just a warning: it was silently turning into a masking
    # "verb runner failed" invalid_state envelope instead of exercising the
    # real block path below).
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    evidence_repo = AsyncMock()
    for m in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(evidence_repo, m).return_value = []
    return ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=evidence_repo,
    )


async def test_i_am_blocked_rejects_soup_reason() -> None:
    agent_id, task_id = uuid4(), uuid4()
    deps = _make_deps(agent_id, task_id)
    c = Choreographer(deps)

    env = await c.i_am_blocked(agent_id, task_id, "wip")

    assert env.error == "invalid_state"
    # The block never happened — no struggle journal, no escalate.
    deps.journal.write_struggle.assert_not_awaited()
    deps.task.escalate.assert_not_awaited()


async def test_i_am_blocked_accepts_real_reason() -> None:
    agent_id, task_id = uuid4(), uuid4()
    deps = _make_deps(agent_id, task_id)
    c = Choreographer(deps)

    env = await c.i_am_blocked(
        agent_id, task_id, "Waiting on the upstream auth schema migration."
    )

    # A substantive reason clears the soup guard (then proceeds to the spec
    # gate / block path — which writes the struggle journal).
    assert env.error != "invalid_state" or "placeholder" not in (env.message or "")
    deps.journal.write_struggle.assert_awaited_once()
