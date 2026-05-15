"""Smoke-8: _readiness_check_role_for_status covers dev-owned states.

Original gap: the role-mismatch table only mapped handoff states
(awaiting_qa, awaiting_documentation, awaiting_pm_review,
awaiting_ceo_approval) to required roles. needs_revision/verifying had
no entry, so a QA spawn for a needs_revision task passed the readiness
check and the gateway rejected claim_review afterwards. Defense in depth
layered behind the _check_health fix.
"""

from __future__ import annotations

from roboco.runtime.orchestrator import AgentOrchestrator


def test_qa_on_needs_revision_blocked() -> None:
    """QA cannot be spawned for a needs_revision task."""
    reason = AgentOrchestrator._readiness_check_role_for_status(
        agent_id="be-qa", role="qa", status="needs_revision"
    )
    assert reason is not None
    assert "needs_revision" in reason
    assert "qa" in reason


def test_pm_on_needs_revision_blocked() -> None:
    """PM cannot be spawned for a needs_revision task either."""
    reason = AgentOrchestrator._readiness_check_role_for_status(
        agent_id="be-pm", role="cell_pm", status="needs_revision"
    )
    assert reason is not None


def test_developer_on_needs_revision_allowed() -> None:
    """Developer (and documenter) ARE the right roles for needs_revision."""
    reason = AgentOrchestrator._readiness_check_role_for_status(
        agent_id="be-dev-1", role="developer", status="needs_revision"
    )
    assert reason is None


def test_documenter_on_needs_revision_allowed() -> None:
    """Documenter can rework — same dev/doc-owned set."""
    reason = AgentOrchestrator._readiness_check_role_for_status(
        agent_id="be-doc", role="documenter", status="needs_revision"
    )
    assert reason is None


def test_qa_on_verifying_blocked() -> None:
    """Verifying belongs to the dev/doc roles, not QA."""
    reason = AgentOrchestrator._readiness_check_role_for_status(
        agent_id="be-qa", role="qa", status="verifying"
    )
    assert reason is not None


def test_qa_on_awaiting_qa_allowed() -> None:
    """The original handoff case still works — QA on awaiting_qa is fine."""
    reason = AgentOrchestrator._readiness_check_role_for_status(
        agent_id="be-qa", role="qa", status="awaiting_qa"
    )
    assert reason is None


def test_unmapped_status_allows_any_role() -> None:
    """Statuses with no role lock (pending, paused, blocked, etc.) pass."""
    for status in ("pending", "claimed", "in_progress", "paused", "blocked"):
        for role in ("developer", "qa", "documenter", "cell_pm"):
            assert (
                AgentOrchestrator._readiness_check_role_for_status(
                    agent_id="x", role=role, status=status
                )
                is None
            ), f"role={role} status={status} should not be rejected by this gate"
