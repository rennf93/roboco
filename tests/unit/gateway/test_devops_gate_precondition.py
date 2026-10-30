"""The DevOps verdict precondition on pr_pass (the Stage-3 infra review gate).

When the predicate applies (flag on + infra globs hit + not devops-authored,
stubbed here via ``_devops_gate_applies`` and covered in
test_devops_gate_predicate.py), the PRIMARY reviewer's pr_pass composes only
once a ``passed`` devops verdict exists for the PR's CURRENT head SHA (the
re-arm: a head advance expires the verdict). The DevOps caller itself is
exempt - its own pr_pass IS its verdict. pr_fail is untouched by the
precondition, and flag-off pr_pass is byte-for-byte today.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.foundation.policy import lifecycle as spec_module
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
)
from roboco.services.gateway.choreographer import devops_gate as devops_gate_lib


def _make_choreographer() -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base["task"].session = MagicMock()
    # pr_fail inserts its findings into the ledger before the transition;
    # the repository needs an awaitable flush() on the mock session.
    base["task"].session.add = MagicMock()
    base["task"].session.flush = AsyncMock()
    base["task"].session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    base["task"].session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return Choreographer(ChoreographerDeps(**base))


def _t(
    *,
    status: str = "awaiting_pr_review",
    notes_structured: dict[str, Any] | None = None,
) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        assigned_to=None,
        pr_number=42,
        parent_task_id=uuid4(),
        status=status,
        notes_structured=notes_structured or {},
    )


def _stub_gate_path(
    c: Choreographer,
    *,
    reviewer_id: Any,
    t_before: Any,
    t_after: Any,
    role: str = "pr_reviewer",
) -> MagicMock:
    """Drive _gate_decision past preflight/tracing into the real pr_pass
    preflights. Mirrors test_pr_pass_ci_status_guard._stub_gate_path."""
    agent = MagicMock(
        role=role, slug="devops-1" if role == "devops" else "be-pr-reviewer"
    )
    cc: Any = c
    cc._gate_preflight = AsyncMock(
        return_value=(
            t_before,
            agent,
            role,
            {},
            spec_module.Context(actor_id=reviewer_id),
        )
    )
    cc._gate_tracing = AsyncMock(return_value=None)
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    record_spy = MagicMock()
    cc._record_gate_verdict = record_spy
    cc._post_gate_review_to_pr = AsyncMock()
    runner = MagicMock()
    runner.run_intent = AsyncMock(return_value=t_after)
    cc._verb_runner = MagicMock(return_value=runner)
    return record_spy


def _green_ci(c: Choreographer) -> None:
    c.git.get_pr_ci_status = AsyncMock(return_value={"state": "success"})


# ---------------------------------------------------------------------------
# The precondition through pr_pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_pr_pass_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off: the precondition is inert - the predicate is never even
    evaluated, so no git/conventions work happens on the pr_pass path."""
    monkeypatch.setattr(settings, "devops_enabled", False)
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    applies = AsyncMock(return_value=True)
    cc: Any = c
    cc._devops_gate_applies = applies
    _green_ci(c)

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"
    applies.assert_not_awaited()


@pytest.mark.asyncio
async def test_predicate_false_pr_pass_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    cc: Any = c
    cc._devops_gate_applies = AsyncMock(return_value=False)
    _green_ci(c)

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"


@pytest.mark.asyncio
async def test_primary_pr_pass_blocked_until_devops_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    reviewer_id = uuid4()
    t_before = _t()
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    cc: Any = c
    cc._devops_gate_applies = AsyncMock(return_value=True)
    _green_ci(c)

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "infra" in (env.message or "")
    assert "wait" in (env.remediate or "").lower()
    assert "devops" in (env.remediate or "").lower()
    # The verdict note was never authored and the gate never transitioned.
    cc._record_gate_verdict.assert_not_called()


@pytest.mark.asyncio
async def test_primary_pr_pass_composes_after_matching_devops_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    reviewer_id = uuid4()
    t_before = _t(
        notes_structured={
            "devops_review": {"verdict": "passed", "head_sha": "h1", "summary": "ok"}
        }
    )
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    cc: Any = c
    cc._devops_gate_applies = AsyncMock(return_value=True)
    c.git.get_pr_head_sha = AsyncMock(return_value="h1")
    _green_ci(c)

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"


@pytest.mark.asyncio
async def test_precondition_re_arms_when_head_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored verdict stamped against an older head no longer satisfies:
    new commits on the PR demand a fresh DevOps review."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    reviewer_id = uuid4()
    t_before = _t(
        notes_structured={
            "devops_review": {"verdict": "passed", "head_sha": "h0", "summary": "ok"}
        }
    )
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    cc: Any = c
    cc._devops_gate_applies = AsyncMock(return_value=True)
    c.git.get_pr_head_sha = AsyncMock(return_value="h1")
    _green_ci(c)

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "infra" in (env.message or "")


@pytest.mark.asyncio
async def test_precondition_fails_open_on_head_capture_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable head SHA is a lookup failure, not a head advance -
    the precondition must not wedge the gate on a git blip."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    reviewer_id = uuid4()
    t_before = _t(
        notes_structured={
            "devops_review": {"verdict": "passed", "head_sha": "h0", "summary": "ok"}
        }
    )
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    cc: Any = c
    cc._devops_gate_applies = AsyncMock(return_value=True)
    c.git.get_pr_head_sha = AsyncMock(return_value=None)
    _green_ci(c)

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()


@pytest.mark.asyncio
async def test_devops_own_pr_pass_is_its_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DevOps caller is exempt from the precondition - requiring a prior
    record would wedge it in a circular block. Its pr_pass both decides and
    records, into the devops_review slot (never the primary's pr_review)."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    devops_id = uuid4()
    t_before = _t()
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    record_spy = _stub_gate_path(
        c, reviewer_id=devops_id, t_before=t_before, t_after=t_after, role="devops"
    )
    cc: Any = c
    cc._devops_gate_applies = AsyncMock(return_value=True)
    _green_ci(c)

    env = await c.pr_pass(devops_id, t_before.id, "Infra surface verified sound.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"
    record_spy.assert_called_once()
    assert record_spy.call_args.kwargs.get("reviewer_role") == "devops"


@pytest.mark.asyncio
async def test_devops_verdict_never_posts_to_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DEVOPS decision records in the DB only - no GitHub review post from
    the second reviewer at Stage 3 (the primary's post path is unchanged)."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    devops_id = uuid4()
    t_before = _t()
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    _stub_gate_path(
        c, reviewer_id=devops_id, t_before=t_before, t_after=t_after, role="devops"
    )
    cc: Any = c
    cc._devops_gate_applies = AsyncMock(return_value=True)
    post_spy = AsyncMock()
    cc._post_gate_review = post_spy
    _green_ci(c)

    env = await c.pr_pass(devops_id, t_before.id, "Infra surface verified sound.")

    assert env.error is None, env.as_dict()
    post_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_fail_unaffected_by_precondition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pr_fail has no devops precondition - the predicate lookup never runs
    on the fail path."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="needs_revision")
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    cc: Any = c
    cc._devops_gate_applies = AsyncMock()
    _green_ci(c)

    env = await c.pr_fail(reviewer_id, t_before.id, ["a concrete actionable issue"])

    assert env.error is None, env.as_dict()
    assert env.status == "needs_revision"
    cc._devops_gate_applies.assert_not_awaited()


# ---------------------------------------------------------------------------
# Findings origin + verified stamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devops_pr_fail_ledgers_under_devops_review_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    devops_id = uuid4()
    t_before = _t()
    t_after = _t(status="needs_revision")
    c = _make_choreographer()
    _stub_gate_path(
        c, reviewer_id=devops_id, t_before=t_before, t_after=t_after, role="devops"
    )
    insert_mock = AsyncMock(return_value=([], "summary"))
    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.pr_gate.findings_lib.insert_and_render",
        insert_mock,
    )

    env = await c.pr_fail(devops_id, t_before.id, ["compose exposes an unbound port"])

    assert env.error is None, env.as_dict()
    assert env.status == "needs_revision"
    insert_mock.assert_awaited_once()
    assert (
        insert_mock.call_args.kwargs.get("origin")
        == devops_gate_lib.DEVOPS_REVIEW_ORIGIN
    )


@pytest.mark.asyncio
async def test_primary_pr_fail_keeps_pr_gate_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", False)
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="needs_revision")
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    insert_mock = AsyncMock(return_value=([], "summary"))
    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.pr_gate.findings_lib.insert_and_render",
        insert_mock,
    )

    env = await c.pr_fail(reviewer_id, t_before.id, ["a concrete actionable issue"])

    assert env.error is None, env.as_dict()
    assert insert_mock.call_args.kwargs.get("origin") == "pr_gate"


@pytest.mark.asyncio
async def test_pr_pass_stamp_verifies_both_origins_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same-transaction verified-stamp bulk-verifies the addressed rows of
    BOTH origins once the devops flag is armed (devops_review rows cannot
    exist with the flag off, so the flag-off stamp stays singular)."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    reviewer_id = uuid4()
    t_before = _t(
        notes_structured={
            "devops_review": {"verdict": "passed", "head_sha": "h1", "summary": "ok"}
        }
    )
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    cc: Any = c
    cc._devops_gate_applies = AsyncMock(return_value=True)
    c.git.get_pr_head_sha = AsyncMock(return_value="h1")
    _green_ci(c)
    stamp_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.pr_gate.findings_lib.stamp_addressed_verified",
        stamp_mock,
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    origins = [call.kwargs.get("origin") for call in stamp_mock.await_args_list]
    assert origins == ["pr_gate", devops_gate_lib.DEVOPS_REVIEW_ORIGIN]
