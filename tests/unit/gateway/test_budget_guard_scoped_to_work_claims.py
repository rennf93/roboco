"""The project monthly-budget claim guard is scoped to work-STARTING claims.

A project at (or over) its monthly cap still has non-negotiable in-flight
work to finish: QA's ``claim_review``, the PR gate's ``claim_gate_review``,
``claim_doc_task``, and inbound ``claim_pr_review`` never even reach the
spend query (``check_project_budget`` defaults False at those four call
sites) — reviewing/documenting/merging what's already been paid for must
never wedge behind an exhausted cap. Only ``i_will_work_on`` / ``i_will_plan``
(``check_project_budget=True``) — genuinely starting new spend — refuse.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

_STEPS = [
    {
        "title": "Implement the change",
        "description": (
            "edit the target file, add tests, run them, and stage the "
            "change for commit on the task branch"
        ),
    }
]


@pytest.fixture(autouse=True)
def _budgets_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_budgets_enabled", True)


def _over_cap_project() -> MagicMock:
    """A project with a $10 cap — the spend stub below always reports $999."""
    return MagicMock(id=uuid4(), monthly_budget_usd=10.0)


def _make_deps(task_svc: AsyncMock, **overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": task_svc,
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    return ChoreographerDeps(**base)


# ---------------------------------------------------------------------------
# Review-ish claims: guard skipped — each succeeds despite an over-cap project.
# project_month_spend_usd is asserted NOT awaited: the guard is skipped
# entirely, not merely lucky (e.g. a cache hit).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_review_succeeds_at_cap() -> None:
    task_svc = AsyncMock()
    t = MagicMock(
        id=uuid4(),
        status="awaiting_qa",
        assigned_to=uuid4(),
        parent_task_id=uuid4(),
        task_type="code",
        dependency_ids=[],
        team="backend",
        pr_number=10,
        pr_url="https://example/pr/10",
        branch_name="feature/backend/abc",
        batch_id=None,
        project=_over_cap_project(),
    )
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="qa", slug="be-qa")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    task_svc.qa_claim = AsyncMock(return_value=t)
    task_svc.project_month_spend_usd = AsyncMock(return_value=999.0)
    c = Choreographer(_make_deps(task_svc))
    cc: Any = c
    cc._build_qa_review_evidence = AsyncMock(return_value={})

    env = await c.claim_review(uuid4(), t.id)
    body = env.as_dict()
    assert body.get("error") is None, body
    task_svc.project_month_spend_usd.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_gate_review_succeeds_at_cap() -> None:
    task_svc = AsyncMock()
    t = MagicMock(
        id=uuid4(),
        status="awaiting_pr_review",
        assigned_to=uuid4(),
        parent_task_id=uuid4(),
        task_type="planning",
        dependency_ids=[],
        team="main_pm",
        pr_number=139,
        pr_url="https://example/pr/139",
        branch_name="feature/main_pm/root",
        batch_id=None,
        project=_over_cap_project(),
    )
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        role="pr_reviewer", slug="be-pr-reviewer"
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.pr_gate_claim = AsyncMock(return_value=t)
    task_svc.project_month_spend_usd = AsyncMock(return_value=999.0)
    c = Choreographer(_make_deps(task_svc))
    cc: Any = c
    cc._build_gate_review_evidence = AsyncMock(return_value={"pr_number": 139})

    env = await c.claim_gate_review(uuid4(), t.id)
    body = env.as_dict()
    assert body.get("error") is None, body
    task_svc.project_month_spend_usd.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_doc_task_succeeds_at_cap() -> None:
    task_svc = AsyncMock()
    t = MagicMock(
        id=uuid4(),
        status="awaiting_documentation",
        assigned_to=None,
        parent_task_id=None,
        task_type="documentation",
        team="backend",
        branch_name="feature/backend/abc",
        quick_context=None,
        documents=[],
        commits=[{"sha": "abc123", "message": "[x] work"}],
        pr_number=7,
        pr_url="https://github.com/x/y/pull/7",
        dev_notes="done",
        acceptance_criteria_status=[],
        work_session_id=uuid4(),
        dependency_ids=[],
        batch_id=None,
        project=_over_cap_project(),
    )
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="documenter", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.doc_claim.return_value = t
    task_svc.project_month_spend_usd = AsyncMock(return_value=999.0)
    git_svc = AsyncMock()
    git_svc.diff.return_value = "diff"
    git_svc.list_changed_files.return_value = ["README.md"]
    c = Choreographer(_make_deps(task_svc, git=git_svc))

    env = await c.claim_doc_task(uuid4(), t.id)
    body = env.as_dict()
    assert body["error"] is None, body
    task_svc.project_month_spend_usd.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_pr_review_succeeds_at_cap() -> None:
    task_svc = AsyncMock()
    t = MagicMock(
        id=uuid4(),
        status="pending",
        assigned_to=None,
        parent_task_id=None,
        task_type="code",
        dependency_ids=[],
        team="system",
        batch_id=None,
        project=_over_cap_project(),
    )
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        role="pr_reviewer", slug="be-pr-reviewer"
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    task_svc.pr_review_claim = AsyncMock(return_value=t)
    task_svc.project_month_spend_usd = AsyncMock(return_value=999.0)
    c = Choreographer(_make_deps(task_svc))
    cc: Any = c
    cc._build_pr_review_evidence = AsyncMock(return_value={})

    env = await c.claim_pr_review(uuid4(), t.id)
    body = env.as_dict()
    assert body.get("error") is None, body
    task_svc.project_month_spend_usd.assert_not_awaited()


# ---------------------------------------------------------------------------
# Work-starting claims: guard ON — both refuse at cap.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_will_work_on_refuses_at_cap() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="code",
        team="backend",
        dependency_ids=[],
        project=_over_cap_project(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = target
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.project_month_spend_usd = AsyncMock(return_value=999.0)
    c = Choreographer(_make_deps(task_svc))

    env = await c.i_will_work_on(agent_id, task_id, plan="x", steps=_STEPS)
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    assert "10.00" in body["message"] and "999.00" in body["message"]
    task_svc.claim.assert_not_awaited()
    task_svc.project_month_spend_usd.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_will_plan_refuses_at_cap() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    target = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        parent_task_id=None,
        sequence=0,
        task_type="planning",
        team="backend",
        dependency_ids=[],
        project=_over_cap_project(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = target
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.project_month_spend_usd = AsyncMock(return_value=999.0)
    c = Choreographer(_make_deps(task_svc))

    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="break it down",
        rich_plan={
            "approach": (
                "Single-cell decomposition: backend handles the full scope; "
                "no frontend or ux work required for this planning task. "
                "be-dev-1 owns the change end to end; QA reviews after the "
                "PR opens, documentation follows, then be-pm completes and "
                "submits up. Strict sequencing, no cross-cell dependencies."
            ),
            "sub_tasks": [
                {
                    "title": "Backend planning slice",
                    "description": (
                        "scope the change, assign be-dev-1, who implements "
                        "with tests and opens the leaf PR for QA review."
                    ),
                }
            ],
        },
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    task_svc.claim.assert_not_awaited()
    task_svc.project_month_spend_usd.assert_awaited_once()


# ---------------------------------------------------------------------------
# Plumbing contract on _run_claim_guards itself.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_claim_guards_only_checks_budget_when_asked() -> None:
    task_svc = AsyncMock()
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    c = Choreographer(_make_deps(task_svc))
    task = MagicMock(id=uuid4(), dependency_ids=[])
    calls = {"n": 0}

    async def _fake_guard(_t: Any) -> None:
        calls["n"] += 1

    cc: Any = c
    cc._project_budget_claim_guard = _fake_guard

    await c._run_claim_guards(agent_id=uuid4(), task=task, skip_dev_guards=True)
    assert calls["n"] == 0, (
        "budget guard must not run without check_project_budget=True"
    )

    await c._run_claim_guards(
        agent_id=uuid4(), task=task, skip_dev_guards=True, check_project_budget=True
    )
    assert calls["n"] == 1
