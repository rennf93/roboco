# Agent Gateway — Phase 2: QA Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Phase 1 (`docs/superpowers/plans/2026-05-01-gateway-phase-1-developer.md`) merged. Tag `phase-1-developer-complete` exists. Developers are running through the gateway end-to-end. QA agents still use legacy MCP tools (`roboco_task_qa_pass`/`qa_fail`/etc).

**Goal:** Cut QA agents over to the new gateway. Implement QA intent verbs (`claim_review`, `pass`, `fail`) and the inline-evidence model that kills the false-PR-fail bug (#15). Update `roboco-flow` MCP server to register QA verbs based on role manifest. Slim the QA role prompt. Switch on `ROBOCO_GATEWAY_ENABLED=true` for QA agents at spawn. Verify the full dev → QA cycle through the new infra.

**Architecture:** QA path is the most consequential remaining lifecycle change because of #15 (QA failed by grepping git history despite `task.pr_number` being set). The gateway's `claim_review` returns evidence inline so the QA agent literally cannot miss the PR data. `pass` and `fail` enforce the tracing gate (qa_notes ≥80 chars, journal:learning, evidence_inspected) before transitioning state. State transitions still go through the same `TaskService` / `task_lifecycle` machinery — gateway is composing, not replacing.

**Tech Stack:** Same as Phase 1.

---

## File Structure

**Create**:
- `roboco/api/routes/v2/flow_qa.py` — QA intent-verb endpoints
- `tests/unit/gateway/test_choreographer_qa.py` — QA-verb test suite
- `tests/integration/v2/test_flow_qa.py` — end-to-end QA HTTP tests

**Modify**:
- `roboco/services/gateway/choreographer.py` — implement Phase 2 verb bodies (claim_review, pass_review, fail_review)
- `roboco/services/gateway/evidence_builder.py` — add `build_for_review` helper that includes `pr_diff_summary` (calling git service)
- `roboco/services/gateway/evidence_repo.py` (created in Phase 1) — add `mark_evidence_inspected` write
- `roboco/services/journal.py` — add `has_learning_for_task` if absent
- `roboco/services/task.py` — add helpers for QA path: `qa_pass_with_audit`, `qa_fail_with_audit`, `mark_evidence_inspected`
- `roboco/api/__init__.py` — mount the QA router
- `roboco/mcp/flow_server.py` — register QA verbs when manifest role is `qa`
- `agents/prompts/roles/qa.md` — slim rewrite (~15 lines)
- `roboco/runtime/orchestrator.py` — set `ROBOCO_GATEWAY_ENABLED=true` for QA agents (extending Phase 1's dev-only flag-set)

---

## Task 1: Choreographer — `claim_review`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: `tests/unit/gateway/test_choreographer_qa.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/unit/gateway/test_choreographer_qa.py
"""Tests for QA choreographer methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from roboco.services.gateway.choreographer import Choreographer
from roboco.services.gateway.envelope import Envelope


@pytest.fixture
def make_choreographer():
    def _make(**overrides):
        return Choreographer(
            task=overrides.get("task", AsyncMock()),
            work_session=overrides.get("work_session", AsyncMock()),
            git=overrides.get("git", AsyncMock()),
            a2a=overrides.get("a2a", AsyncMock()),
            journal=overrides.get("journal", AsyncMock()),
            audit=overrides.get("audit", AsyncMock()),
            evidence_repo=overrides.get("evidence_repo", AsyncMock()),
        )
    return _make


@pytest.mark.asyncio
async def test_claim_review_returns_evidence_inline(make_choreographer):
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="awaiting_qa", assigned_to=None,
        pr_number=8, pr_url="https://github.com/x/y/pull/8",
        commits=[{"sha": "abc123", "message": "feat: x"}],
        team="backend", branch_name="feature/backend/abc--def",
        documents=[], dev_notes="implemented x",
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[{"criterion": "AC1", "referencing_artifact_id": "abc123"}],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.qa_claim.return_value = MagicMock(**{**t.__dict__, "assigned_to": qa_id, "status": "claimed"})
    git_svc = AsyncMock()
    git_svc.diff.return_value = "+++ diff content"
    work_svc = AsyncMock()
    work_svc.files_changed.return_value = ["README.md"]
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []
    evidence_repo.list_unread_a2a.return_value = []
    evidence_repo.list_unread_mentions.return_value = []
    evidence_repo.list_pending_notifications.return_value = []
    evidence_repo.task_metadata_gaps.return_value = []
    evidence_repo.recent_team_activity.return_value = []
    evidence_repo.blockers_in_lane.return_value = []

    c = make_choreographer(task=task_svc, git=git_svc, work_session=work_svc, evidence_repo=evidence_repo)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] is None
    assert body["evidence"]["pr_url"] == "https://github.com/x/y/pull/8"
    assert body["evidence"]["pr_number"] == 8
    assert body["evidence"]["commits"][0]["sha"] == "abc123"
    assert "README.md" in body["evidence"]["files_changed"]


@pytest.mark.asyncio
async def test_claim_review_blocks_if_task_not_awaiting_qa(make_choreographer):
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    c = make_choreographer(task=task_svc)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "awaiting_qa" in body["message"]


@pytest.mark.asyncio
async def test_claim_review_marks_evidence_inspected(make_choreographer):
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="awaiting_qa", pr_number=8, pr_url="x",
        commits=[], team="backend", branch_name="feature/backend/abc",
        documents=[], dev_notes="", acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.qa_claim.return_value = MagicMock(**{**t.__dict__, "assigned_to": qa_id})
    task_svc.mark_evidence_inspected = AsyncMock()
    work_svc = AsyncMock()
    work_svc.files_changed.return_value = []
    git_svc = AsyncMock()
    git_svc.diff.return_value = ""
    evidence_repo = AsyncMock()
    for attr in ("journal_highlights_for_task", "list_unread_a2a", "list_unread_mentions",
                 "list_pending_notifications", "task_metadata_gaps",
                 "recent_team_activity", "blockers_in_lane"):
        getattr(evidence_repo, attr).return_value = []
    c = make_choreographer(task=task_svc, work_session=work_svc, git=git_svc, evidence_repo=evidence_repo)

    await c.claim_review(qa_id, task_id)
    task_svc.mark_evidence_inspected.assert_awaited_once_with(task_id)
```

- [ ] **Step 1.2: Run tests — expect FAIL (NotImplementedError)**

Run: `uv run pytest tests/unit/gateway/test_choreographer_qa.py -v -k claim_review`

- [ ] **Step 1.3: Implement `claim_review`**

In `roboco/services/gateway/choreographer.py`:

```python
async def claim_review(self, qa_agent_id, task_id):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    if str(t.status) != "awaiting_qa":
        return Envelope.invalid_state(
            message=f"task {task_id} is in {t.status}, expected awaiting_qa for review",
            remediate="call give_me_work() to find an actionable QA task",
            context_briefing=await self._briefing_for(qa_agent_id, task_id),
        )
    t = await self.task.qa_claim(qa_agent_id, task_id)

    # Auto-mark evidence as inspected (we're surfacing it inline in this response)
    await self.task.mark_evidence_inspected(task_id)

    # Build evidence inline so QA cannot miss pr_url / pr_number
    files_changed = await self.work_session.files_changed(t.work_session_id) if t.work_session_id else []
    diff_summary = ""
    if t.branch_name:
        diff_summary = await self.git.diff(branch_name=t.branch_name)
    journal_highlights = await self.evidence_repo.journal_highlights_for_task(task_id)
    from roboco.services.gateway.evidence_builder import build_evidence_for_task
    ev = build_evidence_for_task(
        t,
        journal_highlights=journal_highlights,
        files_changed=files_changed,
        pr_diff_summary=diff_summary,
    )
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="review the diff. Then call pass(notes) to accept or fail(issues) to request changes.",
        evidence=ev.as_dict(),
        context_briefing=await self._briefing_for(qa_agent_id, task_id),
    )
```

- [ ] **Step 1.4: Add `mark_evidence_inspected` to TaskService**

In `roboco/services/task.py`:

```python
async def mark_evidence_inspected(self, task_id) -> None:
    """Set tasks.qa_evidence_inspected = True. Idempotent."""
    from sqlalchemy import update
    from roboco.models import Task as TaskModel
    await self._session.execute(
        update(TaskModel)
        .where(TaskModel.id == task_id)
        .values(qa_evidence_inspected=True)
    )
    await self._session.flush()
```

- [ ] **Step 1.5: Add `qa_claim` to TaskService**

If not already present from Phase 0, add a method that:
1. Validates the task is in `awaiting_qa`
2. Sets `assigned_to=qa_agent_id`, `claimed_at=now()`, `status='claimed'` (per existing lifecycle rules)
3. Writes audit entry with actor=qa_agent_id

```python
async def qa_claim(self, qa_agent_id, task_id):
    """Claim a task for QA review. Validates state and assignee role.

    Composes existing TaskService.claim() — no new lifecycle rules here.
    """
    return await self.claim(qa_agent_id, task_id)
```

- [ ] **Step 1.6: Run tests — expect PASS**

Run: `uv run pytest tests/unit/gateway/test_choreographer_qa.py -v -k claim_review`

- [ ] **Step 1.7: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py tests/unit/gateway/test_choreographer_qa.py
git commit -m "feat(gateway): implement claim_review with inline evidence (kills #15) and qa_evidence_inspected tracking"
```

---

## Task 2: Choreographer — `pass_review`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: append to `tests/unit/gateway/test_choreographer_qa.py`

- [ ] **Step 2.1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_pass_review_requires_qa_notes_min_chars(make_choreographer):
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="claimed", assigned_to=qa_id,
        qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.pass_review(qa_id, task_id, notes="too short")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "qa_notes" in str(body["missing"])


@pytest.mark.asyncio
async def test_pass_review_requires_journal_learning(make_choreographer):
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="claimed", assigned_to=qa_id,
        qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = False
    c = make_choreographer(task=task_svc, journal=journal_svc)

    notes = "x" * 100  # long enough
    env = await c.pass_review(qa_id, task_id, notes=notes)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:learning" in body["missing"]


@pytest.mark.asyncio
async def test_pass_review_requires_evidence_inspected(make_choreographer):
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="claimed", assigned_to=qa_id,
        qa_evidence_inspected=False,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    notes = "x" * 100
    env = await c.pass_review(qa_id, task_id, notes=notes)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "qa_evidence_inspected" in body["missing"]


@pytest.mark.asyncio
async def test_pass_review_succeeds_and_transitions(make_choreographer):
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="claimed", assigned_to=qa_id,
        qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.qa_pass.return_value = MagicMock(**{**t.__dict__, "status": "awaiting_documentation"})
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    notes = "Reviewed PR #8: branch matches conv, commit prefix correct, README diff is the timestamp comment as specified, all acceptance criteria met."
    env = await c.pass_review(qa_id, task_id, notes=notes)
    assert env.error is None
    assert env.status == "awaiting_documentation"
    task_svc.qa_pass.assert_awaited_once()
```

- [ ] **Step 2.2: Run tests — expect FAIL**

- [ ] **Step 2.3: Implement `pass_review`**

```python
from roboco.config import settings


async def pass_review(self, qa_agent_id, task_id, notes):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    if t.assigned_to != qa_agent_id:
        return Envelope.not_authorized(
            message="not assigned to you",
            remediate="claim it via claim_review(task_id) first",
            context_briefing=await self._briefing_for(qa_agent_id, task_id),
        )
    has_learning = await self.journal.has_learning_for_task(qa_agent_id, task_id)
    missing: list[str] = []
    if not notes or len(notes) < settings.qa_notes_min_chars:
        missing.append("qa_notes>=min")
    if not has_learning:
        missing.append("journal:learning")
    if not t.qa_evidence_inspected:
        missing.append("qa_evidence_inspected")
    if missing:
        from roboco.services.gateway.remediation import (
            hint_for_missing_qa_notes,
            hint_for_missing_journal_learning,
            hint_for_evidence_not_inspected,
        )
        hints = []
        for m in missing:
            if m == "qa_notes>=min":
                hints.append(hint_for_missing_qa_notes())
            elif m == "journal:learning":
                hints.append(hint_for_missing_journal_learning())
            elif m == "qa_evidence_inspected":
                hints.append(hint_for_evidence_not_inspected(task_id=str(task_id)))
        return Envelope.tracing_gap(
            missing=missing,
            remediate=" ; ".join(hints),
            context_briefing=await self._briefing_for(qa_agent_id, task_id),
        )

    t = await self.task.qa_pass(qa_agent_id, task_id, notes)
    # Auto-A2A to documenter
    doc_agent = await self.task.documenter_for_team(t.team)
    if doc_agent is not None:
        await self.a2a.send(
            from_agent=qa_agent_id,
            to_agent=doc_agent.id,
            skill="documentation",
            task_id=task_id,
            body=f"QA passed task {t.id}. PR: {t.pr_url}. Please document.",
        )
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="idle until next QA work arrives",
        context_briefing=await self._briefing_for(qa_agent_id, task_id),
    )
```

- [ ] **Step 2.4: Add `has_learning_for_task` to JournalService**

In `roboco/services/journal.py`:

```python
async def has_learning_for_task(self, agent_id, task_id) -> bool:
    """Return True if the agent has at least one type='learning' entry for this task."""
    from sqlalchemy import select, func
    from roboco.models import JournalEntry as J, Journal
    q = (
        select(func.count(J.id))
        .join(Journal, Journal.id == J.journal_id)
        .where(
            Journal.agent_id == agent_id,
            J.task_id == task_id,
            J.type == "learning",
        )
    )
    result = await self._session.execute(q)
    return (result.scalar() or 0) > 0


# Same pattern for has_reflect_for_task and has_decision_for_task if not already present.
```

- [ ] **Step 2.5: Add `qa_pass` to TaskService**

```python
async def qa_pass(self, qa_agent_id, task_id, notes):
    """QA passes the task. claimed → awaiting_documentation.

    Composes existing pass-qa transition; sets qa_notes; writes audit.
    """
    # Existing implementation pattern in your codebase — fall back to direct
    # status update if no specific method exists. Audit entry uses qa_agent_id
    # as the actor (kills #11).
    ...
```

(Implementation note: depends on your existing `TaskService` shape. The key is: actor on the audit entry is `qa_agent_id`, never NULL.)

- [ ] **Step 2.6: Run tests — expect PASS**

Run: `uv run pytest tests/unit/gateway/test_choreographer_qa.py -v -k pass_review`

- [ ] **Step 2.7: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/journal.py roboco/services/task.py tests/unit/gateway/test_choreographer_qa.py
git commit -m "feat(gateway): implement pass_review with qa_notes/learning/evidence tracing gates"
```

---

## Task 3: Choreographer — `fail_review`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: append to `tests/unit/gateway/test_choreographer_qa.py`

- [ ] **Step 3.1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_fail_review_succeeds(make_choreographer):
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="claimed", assigned_to=qa_id, qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.qa_fail.return_value = MagicMock(**{**t.__dict__, "status": "needs_revision", "assigned_to": uuid4()})
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    issues = ["Missing test for /healthz", "Lint errors in /api/foo.py"]
    env = await c.fail_review(qa_id, task_id, issues)
    assert env.error is None
    assert env.status == "needs_revision"
    task_svc.qa_fail.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_review_requires_at_least_one_issue(make_choreographer):
    qa_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="claimed", assigned_to=qa_id, qa_evidence_inspected=True)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.fail_review(qa_id, task_id, issues=[])
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "issue" in body["message"].lower()
```

- [ ] **Step 3.2: Run tests — expect FAIL**

- [ ] **Step 3.3: Implement `fail_review`**

```python
async def fail_review(self, qa_agent_id, task_id, issues):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    if t.assigned_to != qa_agent_id:
        return Envelope.not_authorized(
            message="not assigned to you",
            remediate="claim it via claim_review(task_id) first",
            context_briefing=await self._briefing_for(qa_agent_id, task_id),
        )
    if not issues or len(issues) == 0:
        return Envelope.invalid_state(
            message="fail_review requires at least one issue",
            remediate="pass issues=['<concrete actionable issue>', ...]",
            context_briefing=await self._briefing_for(qa_agent_id, task_id),
        )
    has_learning = await self.journal.has_learning_for_task(qa_agent_id, task_id)
    notes = "Issues:\n" + "\n".join(f"- {issue}" for issue in issues)
    missing: list[str] = []
    if len(notes) < settings.qa_notes_min_chars:
        missing.append("qa_notes>=min")
    if not has_learning:
        missing.append("journal:learning")
    if not t.qa_evidence_inspected:
        missing.append("qa_evidence_inspected")
    if missing:
        from roboco.services.gateway.remediation import (
            hint_for_missing_qa_notes,
            hint_for_missing_journal_learning,
            hint_for_evidence_not_inspected,
        )
        hints = []
        for m in missing:
            if m == "qa_notes>=min":
                hints.append(hint_for_missing_qa_notes())
            elif m == "journal:learning":
                hints.append(hint_for_missing_journal_learning())
            elif m == "qa_evidence_inspected":
                hints.append(hint_for_evidence_not_inspected(task_id=str(task_id)))
        return Envelope.tracing_gap(
            missing=missing,
            remediate=" ; ".join(hints),
            context_briefing=await self._briefing_for(qa_agent_id, task_id),
        )

    t = await self.task.qa_fail(qa_agent_id, task_id, notes, issues)
    # A2A back to original developer
    if t.assigned_to is not None:
        await self.a2a.send(
            from_agent=qa_agent_id,
            to_agent=t.assigned_to,
            skill="code_review",
            task_id=task_id,
            body=f"QA needs changes. Issues:\n{notes}",
        )
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="idle — dev will revise and re-submit",
        context_briefing=await self._briefing_for(qa_agent_id, task_id),
    )
```

- [ ] **Step 3.4: Add `qa_fail` to TaskService**

```python
async def qa_fail(self, qa_agent_id, task_id, notes, issues):
    """QA fails the task. claimed → needs_revision; reassigns to original dev.

    Records issues in qa_notes and reassigns to the dev who last had this task.
    Audit actor = qa_agent_id.
    """
    ...
```

- [ ] **Step 3.5: Run tests — expect PASS**

- [ ] **Step 3.6: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py tests/unit/gateway/test_choreographer_qa.py
git commit -m "feat(gateway): implement fail_review with issue list, tracing gates, and dev A2A handoff"
```

---

## Task 4: API v2 — `/api/v2/flow/qa/*` endpoints

**Files:**
- Create: `roboco/api/routes/v2/flow_qa.py`
- Modify: `roboco/api/schemas/v2/flow.py` — add QA request models
- Modify: `roboco/api/__init__.py` — mount router

- [ ] **Step 4.1: Write QA schemas**

Append to `roboco/api/schemas/v2/flow.py`:

```python
class ClaimReviewRequest(BaseModel):
    task_id: UUID


class PassReviewRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)


class FailReviewRequest(BaseModel):
    task_id: UUID
    issues: list[str] = Field(..., min_length=1)
```

- [ ] **Step 4.2: Write the QA router**

```python
# roboco/api/routes/v2/flow_qa.py
"""QA intent-verb HTTP endpoints. Delegates to Choreographer."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.schemas.v2.flow import (
    ClaimReviewRequest, FailReviewRequest, GiveMeWorkRequest,
    IAmIdleRequest, PassReviewRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(prefix="/api/v2/flow/qa", tags=["v2-flow-qa"])


@router.post("/give_me_work")
async def qa_give_me_work(
    _: GiveMeWorkRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.give_me_work(x_agent_id)
    return env.as_dict()


@router.post("/claim_review")
async def qa_claim_review(
    body: ClaimReviewRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.claim_review(x_agent_id, body.task_id)
    return env.as_dict()


@router.post("/pass")
async def qa_pass(
    body: PassReviewRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.pass_review(x_agent_id, body.task_id, body.notes)
    return env.as_dict()


@router.post("/fail")
async def qa_fail(
    body: FailReviewRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.fail_review(x_agent_id, body.task_id, body.issues)
    return env.as_dict()


@router.post("/i_am_idle")
async def qa_i_am_idle(
    _: IAmIdleRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.i_am_idle(x_agent_id)
    return env.as_dict()
```

- [ ] **Step 4.3: Mount router**

In `roboco/api/__init__.py`:

```python
from roboco.api.routes.v2 import flow_qa
app.include_router(flow_qa.router)
```

- [ ] **Step 4.4: Integration test**

```python
# tests/integration/v2/test_flow_qa.py
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_claim_review_returns_pr_inline(client: AsyncClient, qa_agent, awaiting_qa_task_with_pr):
    r = await client.post(
        "/api/v2/flow/qa/claim_review",
        headers={"X-Agent-ID": str(qa_agent.id)},
        json={"task_id": str(awaiting_qa_task_with_pr.id)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert body["evidence"]["pr_number"] == awaiting_qa_task_with_pr.pr_number


@pytest.mark.asyncio
async def test_pass_rejects_short_notes(client, qa_agent, claimed_qa_task):
    r = await client.post(
        "/api/v2/flow/qa/pass",
        headers={"X-Agent-ID": str(qa_agent.id)},
        json={"task_id": str(claimed_qa_task.id), "notes": "ok"},
    )
    body = r.json()
    assert body["error"] == "tracing_gap"
    assert "qa_notes>=min" in body["missing"]
```

(Add fixtures `qa_agent`, `awaiting_qa_task_with_pr`, `claimed_qa_task` to `tests/conftest.py`.)

- [ ] **Step 4.5: Run tests — expect PASS**

Run: `uv run pytest tests/integration/v2/test_flow_qa.py -v`

- [ ] **Step 4.6: Commit**

```bash
git add roboco/api/routes/v2/flow_qa.py roboco/api/schemas/v2/flow.py roboco/api/__init__.py tests/integration/v2/test_flow_qa.py
git commit -m "feat(api/v2): add /api/v2/flow/qa/* endpoints (claim_review, pass, fail, give_me_work, i_am_idle)"
```

---

## Task 5: Update `roboco-flow` MCP server for QA role

**Files:**
- Modify: `roboco/mcp/flow_server.py`

- [ ] **Step 5.1: Register QA verbs conditionally**

Update `flow_server.py`:

```python
# In flow_server.py — adjust _register_role_specific_tools()

def _register_role_specific_tools() -> None:
    manifest = _load_manifest()
    role = manifest["role"]
    flow_tools = set(manifest["flow_tools"])

    # Each role gets its own set; at runtime FastMCP registers all decorated
    # tools. The HTTP handler validates the agent's role against the verb path.
    # Here we mainly verify the manifest matches what's implemented.

    implemented = {
        # dev
        "give_me_work", "i_will_work_on", "i_have_committed",
        "i_am_done", "i_am_blocked", "i_am_idle",
        # qa
        "claim_review", "pass", "fail",
    }
    missing = flow_tools - implemented
    if missing:
        import structlog
        log = structlog.get_logger()
        log.warning("flow_server: unimplemented verbs", role=role, missing=sorted(missing))
```

Add the QA verbs to the FastMCP instance (the role of the agent is checked server-side; the same MCP exposes verbs, the API rejects on role mismatch):

```python
@mcp.tool()
def claim_review(task_id: str) -> dict:
    """QA: claim a task for review. Returns PR diff + evidence inline."""
    return _post(_role_path("claim_review"), {"task_id": task_id})


@mcp.tool(name="pass")
def pass_review(task_id: str, notes: str) -> dict:
    """QA: accept the work. notes >= 80 chars; journal:learning required."""
    return _post(_role_path("pass"), {"task_id": task_id, "notes": notes})


@mcp.tool(name="fail")
def fail_review(task_id: str, issues: list[str]) -> dict:
    """QA: reject the work with issues. Each issue should be concrete and actionable."""
    return _post(_role_path("fail"), {"task_id": task_id, "issues": issues})
```

(`_role_path` already returns `/api/v2/flow/<role>/<verb>` — for QA agents, this becomes `/api/v2/flow/qa/<verb>`.)

- [ ] **Step 5.2: Smoke test**

Spawn a QA agent (set `ROBOCO_AGENT_ROLE=qa` and provide a manifest). Verify the flow server registers `claim_review`/`pass`/`fail` and the calls reach `/api/v2/flow/qa/*`.

- [ ] **Step 5.3: Commit**

```bash
git add roboco/mcp/flow_server.py
git commit -m "feat(mcp): add QA verbs (claim_review, pass, fail) to roboco-flow MCP server"
```

---

## Task 6: Slim QA role prompt

**Files:**
- Modify: `agents/prompts/roles/qa.md`

- [ ] **Step 6.1: Replace with the slim version**

```markdown
# QA

You review code changes via PR diff and structured evidence.

## Who you are
- Team: {team}    Workspace: /data/workspaces/{project}/{team}/{your-slug}/
- You pass or fail. You don't merge. PMs merge after you pass + docs are done.

## Your verbs (already loaded — no ToolSearch needed)
- `give_me_work()` — returns a QA task in awaiting_qa or `idle`
- `claim_review(task_id)` — claim and review. **Response includes pr_url, pr_number, commits, files_changed, dev_summary inline.**
- `pass(task_id, notes)` — accept. notes >= 80 chars describing what you reviewed.
- `fail(task_id, issues)` — reject with concrete actionable issues.
- `note(text, scope?)` — journal. Required: `scope='learning'` before pass/fail.
- `say(channel, text)` / `dm(recipient, text)` — comms
- `evidence(task_id)` — fetch full diff if you need to inspect file contents
- `i_am_idle()` — done for now

## Ground rules
- The PR data is already in `claim_review`'s response. Read `evidence.pr_url`, `evidence.commits`, `evidence.files_changed`, `evidence.acceptance_criteria_status`. Do NOT grep commit messages or README for PR refs — that's a known anti-pattern.
- Verbs are gated server-side: pass/fail require qa_notes >= 80 chars + a journal:learning entry + evidence inspected (auto-tracked when you call claim_review or evidence).
- Verb errors include a `remediate` field — follow it.
- Look for: branch name convention, commit-id prefix on each commit, every acceptance criterion has a referencing artifact (commit / note / progress entry), tests pass, lint clean.
```

- [ ] **Step 6.2: Commit**

```bash
git add agents/prompts/roles/qa.md
git commit -m "docs(prompts): rewrite QA role prompt for gateway verbs; explicitly warn against grep-the-commit anti-pattern"
```

---

## Task 7: Enable gateway flag for QA agents at spawn

**Files:**
- Modify: `roboco/runtime/orchestrator.py`

- [ ] **Step 7.1: Extend the per-role enablement check from Phase 1**

In the spawn function, replace:

```python
container_config["env"]["ROBOCO_GATEWAY_ENABLED"] = "true" if agent.role == "developer" else "false"
```

with:

```python
GATEWAY_ENABLED_ROLES = {"developer", "qa"}  # phase 2: add qa
container_config["env"]["ROBOCO_GATEWAY_ENABLED"] = (
    "true" if agent.role in GATEWAY_ENABLED_ROLES else "false"
)
```

(Phases 3 and 4 will extend this set.)

- [ ] **Step 7.2: Verify QA agents get the manifest**

Spawn a QA agent in the dev stack. Verify `/app/tool-manifest.json` is mounted and the new MCP server starts.

```bash
docker exec roboco-agent-be-qa cat /app/tool-manifest.json | jq '.flow_tools'
# Expected: ["give_me_work","claim_review","pass","fail","i_am_idle"]
```

- [ ] **Step 7.3: Commit**

```bash
git add roboco/runtime/orchestrator.py
git commit -m "feat(runtime): enable gateway flag for QA-role spawns (Phase 2 cutover)"
```

---

## Task 8: Smoke test — full dev → QA cycle through gateway

- [ ] **Step 8.1: Reset state**

```bash
ssh renzof@renzof-nas.local 'cd /volume1/roboco/ && bash scripts/reset_runtime_state.sh'
```

- [ ] **Step 8.2: Run the smoke test (CEO creates a task, dev + QA execute via gateway)**

Verify end-to-end:
1. Dev spawned with manifest, no ToolSearch failures
2. Dev `give_me_work` → task assigned
3. Dev `i_will_work_on(task_id, plan=...)` → in_progress
4. Dev `commit("feat(api): ...")` → progress entry auto-recorded
5. Dev `note(scope='reflect', text=...)` → journal:reflect recorded
6. Dev `i_am_done(task_id, notes=...)` → catch-up runs (push, PR, submit_qa). Auto-A2A to QA with `code_review` skill.
7. QA spawned, gateway flag on
8. QA `give_me_work` → task in awaiting_qa
9. QA `claim_review(task_id)` → response carries `evidence.pr_url`, `evidence.commits`, `evidence.files_changed` inline
10. QA `note(scope='learning', text=...)` → journal:learning recorded
11. QA `pass(task_id, notes=<long-detailed-review>)` → task → awaiting_documentation
12. Auto-A2A to documenter (still old workflow in Phase 2)

- [ ] **Step 8.3: Verify no false-fail regression (#15)**

In smoke test logs, confirm: QA's response payload to `claim_review` contains `pr_url` and `pr_number` set; QA's `pass` succeeds (no spurious "no PR" failures). The `roboco_task_qa_fail` legacy tool is not used.

- [ ] **Step 8.4: Verify tracing**

```bash
docker exec roboco-postgres psql -U roboco -d roboco -c "SELECT type, COUNT(*) FROM journal_entries GROUP BY type;"
# Expect: reflect (dev) >=1, learning (qa) >=1, decision (pm) eventually
```

```bash
docker exec roboco-postgres psql -U roboco -d roboco -c "SELECT event_type, COUNT(*) FROM audit_log WHERE agent_id IS NULL;"
# Expect: 0 NULL agent_id rows (kills #11 for QA path)
```

- [ ] **Step 8.5: Tag the phase**

```bash
git tag phase-2-qa-complete
git push origin phase-2-qa-complete
```

---

## Self-Review

1. **Spec coverage:** QA verbs (claim_review, pass, fail) implemented (Tasks 1-3); inline evidence response (Task 1) kills #15; tracing gates (qa_notes/learning/evidence_inspected) enforced (Task 2-3); MCP server updated (Task 5); slim prompt (Task 6); gateway flag enabled (Task 7); smoke test (Task 8). ✓
2. **Placeholder scan:** All implementations include the actual code. `qa_pass` / `qa_fail` in TaskService noted as "depends on existing shape" — engineer must complete with the codebase's existing transition pattern. Acceptable because the pattern is already established by the existing `qa_pass`/`qa_fail` API endpoints — it's a refactor + audit-actor fix, not a new lifecycle. ✓
3. **Type consistency:** Choreographer methods all return `Envelope`. `pass_review` is the choreographer method name (aliased to `pass` in MCP because `pass` is a Python keyword). ✓
4. **Spec alignment:** Inline-evidence response prevents grep-the-commit anti-pattern. Skill resolution to `code_review` (canonical) handled by Phase-0 alignment migration + dev's `i_am_done` skill resolver. ✓
