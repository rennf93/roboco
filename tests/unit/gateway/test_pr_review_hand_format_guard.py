"""post_pr_review refuses a hand-formatted verdict body with no findings.

The tool's contract (flow_server.post_pr_review docstring) is explicit: ``body``
is a one-paragraph summary; when ``findings`` are given the GitHub comment is
GENERATED in the RoboCo format (summary + findings table + verdict) — "do not
hand-format it in body". Nothing enforced that, so a reviewer could pass
``findings=[]`` and dump a self-formatted ``## Summary`` / ``## Issues`` /
``## Verdict`` markdown blob into ``body`` — which the system posts verbatim
(``_resolve_post_body`` returns ``body`` as-is when there are no findings). The
deployed renderer emits ``## Findings`` (never ``## Issues``), so a ``## Issues``
section on the PR is proof the agent hand-formatted. Observed live: the reviewer
posted a body that listed the issues under BOTH ``## Summary`` and ``## Issues``
and then repeated the entire block twice — a duplicated, self-redundant
hand-formatted blob the contributor sees.

The guard: when ``findings`` is empty AND ``body`` carries verdict/section
markdown headers, reject with ``invalid_state`` and a remediation that points the
reviewer at the structured-findings path. The system never posts a hand-formatted
verdict, so the duplication cannot recur. A clean plain-note ``COMMENT`` with no
findings is still allowed (only verdict-shaped bodies are blocked), and a review
with structured findings is unaffected (the system generates the comment).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy import lifecycle as spec_module
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


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
    return Choreographer(ChoreographerDeps(**base))


def _stub_post_path(c: Choreographer, *, reviewer_id: Any, t: Any) -> None:
    """Drive ``post_pr_review`` past preflight + the verdict-consistency gate so
    the hand-format guard is the thing under test. The runner / side-effects are
    stubbed so a passing case does not hit GitHub or the DB transition."""
    agent = MagicMock(role="pr_reviewer", slug="be-pr-reviewer")
    c._post_pr_review_preflight = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            agent,
            "pr_reviewer",
            {},
            spec_module.Context(actor_id=reviewer_id),
        )
    )
    c._verdict_consistency_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
    c._project_slug_for = AsyncMock(return_value="proj")  # type: ignore[method-assign]
    c._resolve_post_body = MagicMock(return_value="generated body")  # type: ignore[method-assign]
    runner = MagicMock()
    runner.run_intent = AsyncMock(return_value=t)
    c._verb_runner = MagicMock(return_value=runner)  # type: ignore[method-assign]
    c._post_review_side_effects = AsyncMock()  # type: ignore[method-assign]


def _task() -> Any:
    return MagicMock(
        id=uuid4(),
        pr_number=200,
        status="in_progress",
        notes_structured=None,
        pr_reviewer_notes="",
    )


@pytest.mark.asyncio
async def test_hand_formatted_verdict_body_with_no_findings_is_rejected() -> None:
    reviewer_id = uuid4()
    task_id = uuid4()
    c = _make_choreographer()
    _stub_post_path(c, reviewer_id=reviewer_id, t=_task())

    hand_formatted = (
        "## Summary\nIssues:\n- [BLOCKER] seam mismatch\n"
        "## Issues\n- [BLOCKER] seam mismatch\n## Verdict\nfailed"
    )
    env = await c.post_pr_review(reviewer_id, task_id, hand_formatted, "COMMENT")

    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "hand-formatted" in body["message"].lower()
    assert "findings" in body["remediate"].lower()
    # Nothing posted / transitioned — the guard fired before any side effect.
    c.git.post_pr_review.assert_not_awaited()
    # ``c._verb_runner()`` is a ``MagicMock`` at runtime (stubbed above) but the
    # declared return is a coroutine — index the spy through an ``Any`` alias so
    # ``assert_not_awaited`` resolves without a ``type: ignore``.
    cc: Any = c
    cc._verb_runner().run_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_hand_formatted_body_rejected_even_for_request_changes() -> None:
    # pr_review_conflict already blocks REQUEST_CHANGES + no findings, but the
    # guard is independent of event — a hand-formatted verdict must never post,
    # whatever event the agent picked.
    reviewer_id = uuid4()
    task_id = uuid4()
    c = _make_choreographer()
    _stub_post_path(c, reviewer_id=reviewer_id, t=_task())

    env = await c.post_pr_review(
        reviewer_id, task_id, "## Verdict\nfailed\nbad", "REQUEST_CHANGES"
    )
    assert env.as_dict()["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_clean_plain_note_with_no_findings_is_allowed() -> None:
    # A genuine plain COMMENT note (no verdict headers, no findings) is a legit
    # use of event=COMMENT — the guard must not block it.
    reviewer_id = uuid4()
    task_id = uuid4()
    t = _task()
    c = _make_choreographer()
    _stub_post_path(c, reviewer_id=reviewer_id, t=t)

    env = await c.post_pr_review(
        reviewer_id,
        task_id,
        "Left a note for the contributor: the CI flake on job X is tracked separately.",
        "COMMENT",
    )
    body = env.as_dict()
    assert body.get("error") is None
    assert body["status"] == "in_progress"


@pytest.mark.asyncio
async def test_structured_findings_with_summary_body_is_allowed() -> None:
    # With structured findings the system generates the comment; the guard
    # (scoped to empty findings) does not fire even if the summary body happens
    # to contain a header-shaped word.
    reviewer_id = uuid4()
    task_id = uuid4()
    t = _task()
    c = _make_choreographer()
    _stub_post_path(c, reviewer_id=reviewer_id, t=t)

    env = await c.post_pr_review(
        reviewer_id,
        task_id,
        "The 422 path is unguarded.",
        "REQUEST_CHANGES",
        findings=[
            {
                "file": "roboco/services/git.py",
                "line": 42,
                "severity": "blocker",
                "expected": "retry as COMMENT",
                "actual": "raises",
            }
        ],
    )
    body = env.as_dict()
    assert body.get("error") is None


@pytest.mark.asyncio
async def test_envelope_invalid_state_has_introspection_role() -> None:
    # The rejection must carry role introspection like the other gates.
    reviewer_id = uuid4()
    task_id = uuid4()
    c = _make_choreographer()
    _stub_post_path(c, reviewer_id=reviewer_id, t=_task())

    env = await c.post_pr_review(
        reviewer_id, task_id, "## Summary\nstuff\n## Verdict\nfailed", "COMMENT"
    )
    assert env.error == "invalid_state"
    # with_introspection(role="pr_reviewer") populated the introspection fields.
    assert env.current_state is not None
    assert env.valid_next_verbs is not None
