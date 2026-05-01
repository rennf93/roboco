# Agent Gateway — Phase 4: Board Cutover + Final Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Phase 3 (`docs/superpowers/plans/2026-05-01-gateway-phase-3-doc-pms.md`) merged. Tag `phase-3-doc-pms-complete` exists. Devs, QAs, Documenters, Cell PMs, and Main PM all run via the gateway. Full pending → awaiting_ceo_approval flow works end-to-end.

**Goal:** Cut Board roles (Product Owner, Head of Marketing, Auditor) over to the gateway. Then perform the final cleanup: remove all `/api/v1/*` agent endpoints, delete legacy MCP server files, trim `agents/prompts/base.md` and remaining role prompts. Implement the tracing-completeness property test fully (Phase 0 left it as a stub). Run the final smoke test across the entire org. Tag the gateway delivery complete.

**Architecture:** Board has the smallest verb surface — Product Owner and Head of Marketing have parallel verbs (`triage`, `escalate_to_ceo`, `i_am_idle`). Auditor is read-only: it consumes `evidence()` and can `note(scope='reflect')` for its own audit log but never communicates outwardly. After Board cutover, the legacy MCP servers and `/api/v1/*` agent endpoints become dead code; this phase deletes them. The CEO approval flow remains UI-only (no agent verb).

**Tech Stack:** Same as prior phases.

---

## File Structure

**Create**:
- `roboco/api/routes/v2/flow_board.py` — PO + Head Marketing endpoints
- `roboco/api/routes/v2/flow_auditor.py` — read-only audit endpoints
- `tests/unit/gateway/test_choreographer_board.py`
- `tests/unit/gateway/test_choreographer_auditor.py`
- `tests/integration/v2/test_flow_board.py`
- `tests/integration/v2/test_flow_auditor.py`

**Modify**:
- `roboco/services/gateway/choreographer.py` — implement Board + Auditor verbs (`escalate_to_ceo`, `auditor_observe`)
- `roboco/services/task.py` — add `escalate_to_ceo_from_board` (board has its own escalation contract)
- `roboco/api/schemas/v2/flow.py` — add Board/Auditor schemas
- `roboco/api/__init__.py` — mount board + auditor routers; **remove all v1 agent route includes**
- `roboco/mcp/flow_server.py` — register Board + Auditor verbs
- `agents/prompts/roles/board.md` — split into per-board-role files OR slim the existing one
- `agents/prompts/identities/product-owner.md`, `head-marketing.md`, `auditor.md` — slim per-identity prompts
- `agents/prompts/base.md` — final trim (no remaining ToolSearch references; no state machine table)
- `roboco/runtime/orchestrator.py` — extend `GATEWAY_ENABLED_ROLES` to all roles; remove the legacy briefing path
- `roboco/agent_sdk/server.py` — delete the `_legacy_briefing` function
- `tests/property/test_tracing_completeness.py` — fully implement the property assertion (replaces Phase 0 stub)

**Delete**:
- `roboco/mcp/task_server.py`
- `roboco/mcp/journal_server.py`
- `roboco/mcp/notify_server.py`
- `roboco/mcp/a2a_server.py`
- `roboco/mcp/message_server.py` (after Phase 4 — every role moved off it)
- `roboco/mcp/optimal_server.py` (kept actually — `roboco_ask_mentor` and `roboco_kb_search` still useful, but ensure not used by `dm`/etc; can stay)
- `roboco/mcp/project_server.py` (replaced by gateway-internal calls; verify no agent uses it)
- `roboco/mcp/git/` directory or whichever module hosts the dev-facing git tools (keep read-only `git_status`/`git_log`/`git_diff` only — they're mentioned as still-useful; this depends on your structure. Best path: keep the read-only subset in a new lean `roboco/mcp/git_readonly.py` and delete the rest.)
- All `/api/v1/*` route files that are exclusively agent-facing (those used by the panel/UI stay)

---

## Task 1: Choreographer — Board `escalate_to_ceo`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: `tests/unit/gateway/test_choreographer_board.py`

- [ ] **Step 1.1: Write failing tests**

```python
# tests/unit/gateway/test_choreographer_board.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from roboco.services.gateway.choreographer import Choreographer


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
async def test_board_escalate_to_ceo_succeeds(make_choreographer):
    po_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="awaiting_pm_review", team="backend", parent_task_id=None)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="product_owner", escalation_target="ceo")
    task_svc.escalate_to_ceo.return_value = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.escalate_to_ceo(po_id, task_id, reason="strategic decision needed")
    assert env.error is None
    assert env.status == "awaiting_ceo_approval"


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_blocks_wrong_state(make_choreographer):
    po_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress", team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="product_owner")
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.escalate_to_ceo(po_id, task_id, reason="x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
```

- [ ] **Step 1.2: Run tests — expect FAIL**

- [ ] **Step 1.3: Implement (or update existing) `escalate_to_ceo`**

```python
async def escalate_to_ceo(self, agent_id, task_id, reason):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    me = await self.task.agent_for(agent_id)
    if me.role not in ("main_pm", "product_owner", "head_marketing"):
        return Envelope.not_authorized(
            message=f"role {me.role} cannot escalate to CEO directly",
            remediate="use escalate_up() to go through your escalation chain",
            context_briefing=await self._briefing_for(agent_id, task_id),
        )
    if str(t.status) != "awaiting_pm_review":
        return Envelope.invalid_state(
            message=f"task {task_id} is in {t.status}, expected awaiting_pm_review",
            remediate="this task is not at the gate for CEO approval",
            context_briefing=await self._briefing_for(agent_id, task_id),
        )
    has_decision = await self.journal.has_decision_for_task(agent_id, task_id)
    if not has_decision:
        from roboco.services.gateway.remediation import hint_for_missing_journal_decision
        return Envelope.tracing_gap(
            missing=["journal:decision"],
            remediate=hint_for_missing_journal_decision(),
            context_briefing=await self._briefing_for(agent_id, task_id),
        )
    t = await self.task.escalate_to_ceo(agent_id, task_id, reason)
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="idle until CEO acts via UI",
        context_briefing=await self._briefing_for(agent_id, task_id),
    )
```

- [ ] **Step 1.4: Run tests — expect PASS**

- [ ] **Step 1.5: Commit**

```bash
git add roboco/services/gateway/choreographer.py tests/unit/gateway/test_choreographer_board.py
git commit -m "feat(gateway): implement escalate_to_ceo with role allow-list (main_pm, product_owner, head_marketing)"
```

---

## Task 2: Choreographer — Board `triage`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`

- [ ] **Step 2.1: Test**

```python
# in test_choreographer_board.py
@pytest.mark.asyncio
async def test_board_triage_returns_strategic_review_first(make_choreographer):
    po_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="product_owner", team="board")
    strategic = MagicMock(id=uuid4(), status="awaiting_pm_review", title="strategic", team="backend",
                          nature="strategic", parent_task_id=None)
    task_svc.list_strategic_for_board.return_value = [strategic]
    evidence_repo = AsyncMock()
    for attr in ("list_unread_a2a", "list_unread_mentions", "list_pending_notifications",
                 "task_metadata_gaps", "recent_team_activity", "blockers_in_lane"):
        getattr(evidence_repo, attr).return_value = []
    c = make_choreographer(task=task_svc, evidence_repo=evidence_repo)

    env = await c.board_triage(po_id)
    body = env.as_dict()
    assert body["task_id"] == str(strategic.id)
```

- [ ] **Step 2.2: Implement**

The Board's triage is similar to Cell PM but scopes to strategic-nature root tasks awaiting board review. The `tasks` model already has a `nature` enum; the Board prioritizes `nature='strategic'` tasks at root level.

```python
async def board_triage(self, board_agent_id):
    me = await self.task.agent_for(board_agent_id)
    strategic = await self.task.list_strategic_for_board()
    if strategic:
        t = strategic[0]
        return Envelope.ok(
            status=str(t.status), task_id=str(t.id),
            next=f"review and call escalate_to_ceo(task_id='{t.id}', reason=...) or i_am_idle",
            context_briefing=await self._briefing_for(board_agent_id, t.id),
        )
    return Envelope.ok(
        status="idle", task_id=None,
        next="no strategic-review work — i_am_idle",
        context_briefing=await self._briefing_for(board_agent_id, None),
    )
```

Add helper to `TaskService`:

```python
async def list_strategic_for_board(self) -> list[Task]:
    """Tasks at root level (no parent) in awaiting_pm_review whose nature is 'strategic'."""
    from sqlalchemy import select
    q = select(TaskModel).where(
        TaskModel.parent_task_id.is_(None),
        TaskModel.status == "awaiting_pm_review",
        TaskModel.nature == "strategic",
    )
    result = await self._db.execute(q)
    return list(result.scalars().all())
```

- [ ] **Step 2.3: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py tests/unit/gateway/test_choreographer_board.py
git commit -m "feat(gateway): implement board_triage prioritizing strategic root tasks"
```

---

## Task 3: Choreographer — Auditor verbs (read-only)

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: `tests/unit/gateway/test_choreographer_auditor.py`

The Auditor is silent: it can `triage` to read state and `note(scope='reflect')` to keep its own audit notebook. It cannot send A2A or post to channels (per role config from Phase 0).

- [ ] **Step 3.1: Test**

```python
# tests/unit/gateway/test_choreographer_auditor.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from roboco.services.gateway.choreographer import Choreographer


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
async def test_auditor_triage_returns_anomaly_if_present(make_choreographer):
    auditor_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="auditor", team="board")
    anomaly = MagicMock(id=uuid4(), status="blocked", title="long-running blocked", team="backend")
    task_svc.list_long_running_blocked.return_value = [anomaly]
    evidence_repo = AsyncMock()
    for attr in ("list_unread_a2a", "list_unread_mentions", "list_pending_notifications",
                 "task_metadata_gaps", "recent_team_activity", "blockers_in_lane"):
        getattr(evidence_repo, attr).return_value = []
    c = make_choreographer(task=task_svc, evidence_repo=evidence_repo)

    env = await c.auditor_triage(auditor_id)
    body = env.as_dict()
    assert body["task_id"] == str(anomaly.id)
```

- [ ] **Step 3.2: Implement**

```python
async def auditor_triage(self, auditor_agent_id):
    """Auditor: surface anomalies (long-running blocked, missing tracing, etc.)."""
    anomalies = await self.task.list_long_running_blocked()  # tasks blocked > X minutes
    if anomalies:
        t = anomalies[0]
        return Envelope.ok(
            status=str(t.status), task_id=str(t.id),
            next=(
                "log a reflect-note observing the anomaly via "
                f"note(scope='reflect', task_id='{t.id}', text='...')"
            ),
            context_briefing=await self._briefing_for(auditor_agent_id, t.id),
        )
    return Envelope.ok(
        status="idle", task_id=None,
        next="no anomalies — i_am_idle",
        context_briefing=await self._briefing_for(auditor_agent_id, None),
    )


# Also reuse i_am_idle (already implemented; no role-specific behavior needed)
```

Add helper to `TaskService`:

```python
async def list_long_running_blocked(self, *, threshold_minutes: int = 30) -> list[Task]:
    """Tasks in 'blocked' state whose updated_at is older than threshold_minutes ago."""
    from sqlalchemy import select
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=threshold_minutes)
    q = select(TaskModel).where(
        TaskModel.status == "blocked",
        TaskModel.updated_at < cutoff,
    )
    result = await self._db.execute(q)
    return list(result.scalars().all())
```

- [ ] **Step 3.3: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py tests/unit/gateway/test_choreographer_auditor.py
git commit -m "feat(gateway): implement auditor_triage surfacing long-running blocked-task anomalies"
```

---

## Task 4: API v2 — Board + Auditor endpoints

**Files:**
- Create: `roboco/api/routes/v2/flow_board.py`, `flow_auditor.py`
- Modify: `roboco/api/schemas/v2/flow.py`
- Modify: `roboco/api/__init__.py`

- [ ] **Step 4.1: Schemas**

```python
# Append to roboco/api/schemas/v2/flow.py
class EscalateToCeoRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)
```

- [ ] **Step 4.2: Board router**

```python
# roboco/api/routes/v2/flow_board.py
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.schemas.v2.flow import (
    EscalateToCeoRequest, IAmIdleRequest, TriageRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(prefix="/api/v2/flow/board", tags=["v2-flow-board"])


@router.post("/triage")
async def board_triage(_: TriageRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                        c: Choreographer = Depends(get_choreographer)):
    env = await c.board_triage(x_agent_id); return env.as_dict()


@router.post("/escalate_to_ceo")
async def board_escalate(body: EscalateToCeoRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                          c: Choreographer = Depends(get_choreographer)):
    env = await c.escalate_to_ceo(x_agent_id, body.task_id, body.reason); return env.as_dict()


@router.post("/i_am_idle")
async def board_idle(_: IAmIdleRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                      c: Choreographer = Depends(get_choreographer)):
    env = await c.i_am_idle(x_agent_id); return env.as_dict()
```

- [ ] **Step 4.3: Auditor router**

```python
# roboco/api/routes/v2/flow_auditor.py
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.schemas.v2.flow import IAmIdleRequest, TriageRequest
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(prefix="/api/v2/flow/auditor", tags=["v2-flow-auditor"])


@router.post("/triage")
async def auditor_triage(_: TriageRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                          c: Choreographer = Depends(get_choreographer)):
    env = await c.auditor_triage(x_agent_id); return env.as_dict()


@router.post("/i_am_idle")
async def auditor_idle(_: IAmIdleRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                        c: Choreographer = Depends(get_choreographer)):
    env = await c.i_am_idle(x_agent_id); return env.as_dict()
```

- [ ] **Step 4.4: Mount + integration tests**

```python
# roboco/api/__init__.py
from roboco.api.routes.v2 import flow_board, flow_auditor
app.include_router(flow_board.router)
app.include_router(flow_auditor.router)
```

Tests follow the same pattern as Phase 3 integration tests. Add `tests/integration/v2/test_flow_board.py` and `test_flow_auditor.py`.

- [ ] **Step 4.5: Commit**

```bash
git add roboco/api/routes/v2/flow_board.py roboco/api/routes/v2/flow_auditor.py roboco/api/schemas/v2/flow.py roboco/api/__init__.py tests/integration/v2/
git commit -m "feat(api/v2): add /api/v2/flow/{board,auditor}/* endpoints"
```

---

## Task 5: Update `roboco-flow` MCP server with Board + Auditor verbs

**Files:**
- Modify: `roboco/mcp/flow_server.py`

- [ ] **Step 5.1: Append Board + Auditor tool registrations**

```python
@mcp.tool()
def escalate_to_ceo(task_id: str, reason: str) -> dict:
    """Board / Main PM: escalate a strategic task to CEO."""
    return _post(_role_path("escalate_to_ceo"), {"task_id": task_id, "reason": reason})


# Note: triage already registered (used by Cell PM, Board, Auditor with role-routing in URL)
```

Update the `implemented` set in `_register_role_specific_tools` to include `escalate_to_ceo`. The MCP server file is now feature-complete for all roles.

- [ ] **Step 5.2: Commit**

```bash
git add roboco/mcp/flow_server.py
git commit -m "feat(mcp): add Board + Auditor verbs (escalate_to_ceo) to roboco-flow MCP server"
```

---

## Task 6: Slim Board + Auditor role prompts

**Files:**
- Create or replace: `agents/prompts/identities/product-owner.md`, `head-marketing.md`, `auditor.md`
- Modify: `agents/prompts/roles/board.md` if it exists; otherwise rewrite the existing identities

- [ ] **Step 6.1: product-owner.md**

```markdown
# Product Owner

You provide strategic oversight; you escalate to CEO when needed.

## Who you are
- Team: board    Workspace: /data/workspaces/{project}/board/product-owner/
- Escalation target: ceo

## Your verbs (already loaded)
- `triage()` — returns strategic root tasks awaiting review
- `escalate_to_ceo(task_id, reason)` — for awaiting_pm_review root tasks
- `note(text, scope?)` — journal. Required: `scope='decision'` before escalate_to_ceo.
- `say(channel, text)` / `dm(recipient, text)` — comms
- `evidence(task_id)` — inspect a task
- `i_am_idle()`

## Ground rules
- Strategic decisions go to CEO. Don't make merge calls (PMs do that).
- Errors include a `remediate` field — follow it.
```

- [ ] **Step 6.2: head-marketing.md**

```markdown
# Head of Marketing

You oversee marketing-related tasks at the org level.

## Who you are
- Team: board    Workspace: /data/workspaces/{project}/board/head-marketing/
- Escalation target: ceo

## Your verbs (already loaded)
- `triage()`, `escalate_to_ceo(task_id, reason)`, `note(...)`, `say(...)`, `dm(...)`, `evidence(...)`, `i_am_idle()`

## Ground rules
- Same as Product Owner; scope to marketing-tagged tasks.
- Errors include a `remediate` field — follow it.
```

- [ ] **Step 6.3: auditor.md**

```markdown
# Auditor

You silently observe org activity and log anomalies. You don't communicate outwardly.

## Who you are
- Team: board    Workspace: /data/workspaces/{project}/board/auditor/

## Your verbs (already loaded)
- `triage()` — surfaces long-running blocked tasks and other anomalies
- `note(text, scope?)` — your audit notebook. Use `scope='reflect'` for observations.
- `evidence(task_id)` — inspect a task in detail
- `i_am_idle()`

## Ground rules
- You DO NOT have say/dm verbs. Your visibility is read-only; your output is your journal.
- Log every anomaly you observe with `note(scope='reflect', task_id='...', text='<observation>')`.
- Errors include a `remediate` field.
```

- [ ] **Step 6.4: Commit**

```bash
git add agents/prompts/identities/
git commit -m "docs(prompts): rewrite Board (PO, Head-Marketing, Auditor) identity prompts for gateway verbs"
```

---

## Task 7: Final trim of `agents/prompts/base.md`

**Files:**
- Modify: `agents/prompts/base.md`

- [ ] **Step 7.1: Strip references to ToolSearch + state machine table**

Open `agents/prompts/base.md`. Remove:
- The "On spawn — do this first" section (no ToolSearch needed)
- The "Task states" table (gateway tells you the next state)
- The "Shared tools" enumeration (manifest is the source of truth)

Keep:
- Identity intro
- Ground rules (workspace scoping, bash-guard, etc.)
- Pointer to role prompt for verb list
- Branch + commit conventions (still relevant for the engineer, even though the gateway auto-prefixes)
- Substitute reasons (kept; relevant for `i_am_blocked`)

Resulting file ~30 lines (down from ~50+):

```markdown
# RoboCo Agent — Base

You are an agent in **RoboCo**, an AI company with 18 AI agents + 1 human CEO.

Your role-specific prompt (`agents/prompts/roles/<role>.md`) lists your verbs and your specific responsibilities.

## How verbs work
Every verb call returns a JSON envelope:
- On success: `{status, task_id, next, evidence?, context_briefing}` — `next` tells you what to call next.
- On error: `{error, message, remediate}` — `remediate` tells you exactly how to fix and retry.

Trust the response. Don't guess at the next step — the gateway has already computed it.

## Ground rules (enforced by orchestrator)
- Raw `git fetch/pull/push/checkout/commit/merge/remote` via `Bash` is **denied** — use the verbs your role provides.
- Reading credential files (`.git/config`, `.gitconfig`, `.git-credentials`, `.netrc`) is **denied**.
- `curl`/`wget` to GitHub is **denied** — gateway handles git ops.
- `env`/`printenv` is **denied** — secrets aren't readable.
- Write/Edit limited to YOUR workspace: `/data/workspaces/{project}/{team}/{your-slug}/`.

## Tracing
Tracing is enforced server-side. The gateway will reject your transition verbs (`i_am_done`, `pass`, `complete`, `escalate_to_ceo`, etc.) until tracing is current — required journal entries, qa_notes, acceptance_criteria_status, etc. Read the `remediate` field; it tells you what's missing and how to fix it.

## Branch + commit conventions (handled by gateway)
- Branches: `{feature|bug|chore|docs|hotfix}/{team}/{root-id}[--{sub-id}[--{subsub-id}]]` (auto-created on claim).
- Commits: `[{task-id}] {type}({scope}): {subject}` (auto-prefixed by `commit()`).
- Subject must be >=20 chars and not match banned single-word patterns (wip, fix, update, etc.).

## Substitute reasons (for i_am_blocked)
`low_context`, `out_of_scope_team`, `out_of_scope_role`, `task_complete`, `max_retries`, `blocked_external`.
```

- [ ] **Step 7.2: Commit**

```bash
git add agents/prompts/base.md
git commit -m "docs(prompts): final trim of base.md — remove ToolSearch instruction and state-machine table; describe gateway envelope"
```

---

## Task 8: Enable gateway flag for ALL roles

**Files:**
- Modify: `roboco/runtime/orchestrator.py`

- [ ] **Step 8.1: Switch from allow-list to all-roles**

```python
# Phase 4: gateway is universal
GATEWAY_ENABLED_ROLES = {
    "developer", "qa", "documenter", "cell_pm", "main_pm",
    "product_owner", "head_marketing", "auditor",
}
container_config["env"]["ROBOCO_GATEWAY_ENABLED"] = (
    "true" if agent.role in GATEWAY_ENABLED_ROLES else "false"
)
# After all roles migrated, this can simplify to: "true"
```

After verifying all roles work (Task 11 below), simplify to:

```python
container_config["env"]["ROBOCO_GATEWAY_ENABLED"] = "true"
```

- [ ] **Step 8.2: Commit**

```bash
git add roboco/runtime/orchestrator.py
git commit -m "feat(runtime): enable gateway for all roles (Phase 4 universal cutover)"
```

---

## Task 9: Delete legacy MCP server files

**Files:**
- Delete: `roboco/mcp/task_server.py`
- Delete: `roboco/mcp/journal_server.py`
- Delete: `roboco/mcp/notify_server.py`
- Delete: `roboco/mcp/a2a_server.py`
- Delete: `roboco/mcp/message_server.py`
- Delete: `roboco/mcp/project_server.py` (verify no agent uses it)
- Delete: dev-facing portions of `roboco/mcp/git/` (keep only read-only `roboco_git_status`, `roboco_git_log`, `roboco_git_diff`, `roboco_git_branch_list` — move to a slim file like `roboco/mcp/git_readonly.py` or similar; update agent containers' Dockerfile to reference the new module)
- Delete: `roboco/mcp/tasks/` (the dev-facing task handlers directory)
- Delete: `roboco/mcp/test/` (if no longer used)

- [ ] **Step 9.1: Verify nothing in the new code still imports the legacy modules**

Run: `grep -rn "from roboco.mcp.task_server\|from roboco.mcp.journal_server\|from roboco.mcp.notify_server\|from roboco.mcp.a2a_server\|from roboco.mcp.message_server" roboco/ tests/`
Expected: no hits (or only test files that should also be removed).

- [ ] **Step 9.2: Verify nothing in agent containers references the deleted servers**

Search Dockerfiles + docker-compose.yml for `roboco.mcp.task_server`, etc. Update to use only `roboco.mcp.flow_server`, `roboco.mcp.do_server`, and the read-only git module.

Run: `grep -rn "task_server\|journal_server\|notify_server\|a2a_server\|message_server" docker/ docker-compose.yml`

- [ ] **Step 9.3: Delete and migrate read-only git tools**

```bash
git rm roboco/mcp/task_server.py
git rm roboco/mcp/journal_server.py
git rm roboco/mcp/notify_server.py
git rm roboco/mcp/a2a_server.py
git rm roboco/mcp/message_server.py
git rm roboco/mcp/project_server.py
git rm -r roboco/mcp/tasks/
git rm -r roboco/mcp/test/
```

For the git tools, create `roboco/mcp/git_readonly.py`:

```python
# roboco/mcp/git_readonly.py
"""Read-only git tools available to all roles. Write ops go through gateway verbs."""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

ORCHESTRATOR_URL = os.environ.get("ROBOCO_ORCHESTRATOR_URL", "http://roboco-orchestrator:8000")
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
_HEADERS = {"X-Agent-ID": AGENT_ID, "X-Agent-Role": os.environ["ROBOCO_AGENT_ROLE"]}

mcp = FastMCP("roboco-git-readonly")


def _get(path: str, params: dict) -> dict:
    with httpx.Client(timeout=15) as c:
        r = c.get(f"{ORCHESTRATOR_URL}{path}", headers=_HEADERS, params=params)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def roboco_git_status(project_id: str) -> dict:
    """Read-only: current git status of your workspace."""
    return _get("/api/v1/git/status", {"project_id": project_id})


@mcp.tool()
def roboco_git_log(project_id: str, limit: int = 20) -> dict:
    """Read-only: recent commits on your current branch."""
    return _get("/api/v1/git/log", {"project_id": project_id, "limit": limit})


@mcp.tool()
def roboco_git_diff(project_id: str, base: str = "HEAD~1") -> dict:
    """Read-only: diff against base."""
    return _get("/api/v1/git/diff", {"project_id": project_id, "base": base})


@mcp.tool()
def roboco_git_branch_list(project_id: str) -> dict:
    """Read-only: list branches."""
    return _get("/api/v1/git/branch_list", {"project_id": project_id})


if __name__ == "__main__":
    mcp.run()
```

(These four read-only endpoints stay on `/api/v1/git/*` — they're read-only and the panel may also use them; not removed.)

Update the spawn manifest's `flow_tools` / `do_tools` to no longer include the dev-facing git write tools (already done in role_config.py from Phase 0; verify).

- [ ] **Step 9.4: Commit**

```bash
git add -A
git commit -m "refactor(mcp): delete legacy task/journal/notify/a2a/message/project servers; consolidate read-only git into roboco-git-readonly server"
```

---

## Task 10: Remove agent-facing `/api/v1/*` route includes

**Files:**
- Modify: `roboco/api/__init__.py` (or `roboco/api/app.py`)

Some `/api/v1/*` routes are panel/UI-facing (kanban, dashboard, sessions, channels, agents listing). Those stay. But routes that are exclusively agent-facing — old gateway-replaced — get unincluded. Identify them:

- `/api/v1/tasks/*` (some panel; some agent) — keep panel-facing read endpoints; remove `claim`/`start`/`submit_qa`/`qa_pass`/`qa_fail`/`complete`/`unblock` write endpoints (the new gateway calls TaskService directly). Audit each.
- `/api/v1/journals/*` — replaced by gateway-internal journal calls. Keep panel read-only ones; remove writes.
- `/api/v1/messages/*` — kept for panel; agents use `say()`.
- `/api/v1/notifications/*` — kept for panel; the agent-facing pending-a2 endpoint can stay (it's harmless after Phase 0's #9 fix).
- `/api/v1/a2a/*` — kept for panel; agents use `dm()`.

- [ ] **Step 10.1: Audit and remove**

Run: `grep -rn "include_router\|app.include_router" roboco/api/__init__.py roboco/api/app.py`

For each agent-facing v1 router, comment out the include and add a TODO comment explaining what replaced it. Better: split each route file into `<topic>_panel.py` (kept) and `<topic>_agent.py` (removed) where the split is meaningful.

For surgical safety, leave the v1 *files* in place for now; just stop including their agent-facing routers. A follow-up cleanup PR can delete the files once the panel paths are confirmed unaffected.

- [ ] **Step 10.2: Commit**

```bash
git add roboco/api/__init__.py
git commit -m "chore(api): unmount agent-facing /api/v1/* writers; panel-facing read endpoints retained"
```

---

## Task 11: Implement the tracing-completeness property test (replaces Phase 0 stub)

**Files:**
- Modify: `tests/property/test_tracing_completeness.py`

- [ ] **Step 11.1: Replace the `@pytest.mark.skip` stub with the full assertion**

```python
# tests/property/test_tracing_completeness.py
"""Property test: every completed task has full tracing.

Run after a smoke-test fixture batch. Asserts the tracing contract from
docs/superpowers/specs/2026-05-01-agent-gateway-design.md §6.4.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
@pytest.mark.integration
async def test_completed_tasks_have_full_tracing(db_session, smoke_test_batch):
    """For every task with status=completed in the smoke-test fixture batch,
    assert the tracing contract holds."""
    from roboco.models import Agent, AuditLog, Journal, JournalEntry, Task, TaskStatus

    completed_q = select(Task).where(Task.status == TaskStatus.COMPLETED)
    result = await db_session.execute(completed_q)
    completed = list(result.scalars().all())
    assert len(completed) >= 1, "smoke-test batch must have at least one completed task"

    failures: list[str] = []
    for task in completed:
        # 1. audit_log entries with non-null agent_id for every transition
        audit_q = select(AuditLog).where(AuditLog.target_id == task.id)
        audit_rows = (await db_session.execute(audit_q)).scalars().all()
        null_actor = [r for r in audit_rows if r.agent_id is None]
        if null_actor:
            failures.append(
                f"task {task.id}: {len(null_actor)} audit rows with null agent_id"
            )

        # 2. dev journal:reflect for the task
        reflect_q = (
            select(JournalEntry)
            .join(Journal, Journal.id == JournalEntry.journal_id)
            .join(Agent, Agent.id == Journal.agent_id)
            .where(
                JournalEntry.task_id == task.id,
                JournalEntry.type == "reflect",
                Agent.role == "developer",
            )
        )
        if not (await db_session.execute(reflect_q)).first():
            failures.append(f"task {task.id}: no dev journal:reflect")

        # 3. QA journal:learning
        learning_q = (
            select(JournalEntry)
            .join(Journal, Journal.id == JournalEntry.journal_id)
            .join(Agent, Agent.id == Journal.agent_id)
            .where(
                JournalEntry.task_id == task.id,
                JournalEntry.type == "learning",
                Agent.role == "qa",
            )
        )
        if not (await db_session.execute(learning_q)).first():
            failures.append(f"task {task.id}: no QA journal:learning")

        # 4. PM journal:decision
        decision_q = (
            select(JournalEntry)
            .join(Journal, Journal.id == JournalEntry.journal_id)
            .join(Agent, Agent.id == Journal.agent_id)
            .where(
                JournalEntry.task_id == task.id,
                JournalEntry.type == "decision",
                Agent.role.in_(["cell_pm", "main_pm"]),
            )
        )
        if not (await db_session.execute(decision_q)).first():
            failures.append(f"task {task.id}: no PM journal:decision")

        # 5. acceptance_criteria_status: every criterion has a referencing artifact
        criteria = list(task.acceptance_criteria or [])
        status = list(task.acceptance_criteria_status or [])
        addressed = {
            s["criterion"] for s in status
            if isinstance(s, dict) and s.get("referencing_artifact_id")
        }
        unaddressed = [c for c in criteria if c not in addressed]
        if unaddressed:
            failures.append(
                f"task {task.id}: unaddressed acceptance criteria: {unaddressed}"
            )

        # 6. qa_evidence_inspected = true
        if not task.qa_evidence_inspected:
            failures.append(f"task {task.id}: qa_evidence_inspected=false")

    assert not failures, "tracing-completeness violations:\n" + "\n".join(failures)
```

- [ ] **Step 11.2: Add fixture**

In `tests/conftest.py`, add a `smoke_test_batch` fixture that runs the integration smoke-test E2E flow once and yields. The fixture from Phase 3's `test_full_pending_to_completed.py` can be promoted into a session-scoped batch fixture for the property test.

- [ ] **Step 11.3: Run**

Run: `uv run pytest tests/property/test_tracing_completeness.py -v`
Expected: PASS — all completed tasks from the smoke batch have full tracing.

- [ ] **Step 11.4: Wire into `make quality`**

The property test runs as part of the integration suite. If it's slow, mark it `@pytest.mark.integration` and ensure CI runs all marked tests.

Verify `make quality` invokes it (via `pytest tests/`).

- [ ] **Step 11.5: Commit**

```bash
git add tests/property/test_tracing_completeness.py tests/conftest.py
git commit -m "test(property): implement tracing-completeness assertion across smoke-test batch (replaces Phase 0 stub)"
```

---

## Task 12: Final smoke test — full org cutover

- [ ] **Step 12.1: Reset state**

```bash
ssh renzof@renzof-nas.local 'cd /volume1/roboco/ && bash scripts/reset_runtime_state.sh'
```

- [ ] **Step 12.2: Pull latest images, rebuild**

```bash
ssh renzof@renzof-nas.local 'cd /volume1/roboco/ && docker compose pull && docker compose up -d --build'
```

- [ ] **Step 12.3: Run the canonical smoke-test**

Create a smoke-test task as before. Verify the full lifecycle:

1. Dev spawned (gateway flag on; manifest mounted; no ToolSearch instruction in briefing)
2. Dev `give_me_work` → task assigned
3. Dev `i_will_work_on(plan=...)` → in_progress
4. Dev `commit("...")` → progress entry recorded
5. Dev `note(scope='reflect', text=...)` → journal:reflect recorded
6. Dev `i_am_done` → push → PR → submit_qa, A2A to QA with code_review
7. QA spawned (gateway flag on)
8. QA `claim_review(task_id)` → response includes `evidence.pr_url`, `evidence.commits`, etc. inline
9. QA `note(scope='learning', text=...)` → journal:learning
10. QA `pass(task_id, notes=<long-detailed>)` → task → awaiting_documentation, A2A to documenter
11. Doc spawned (gateway flag on)
12. Doc `claim_doc_task` → response includes evidence
13. Doc writes docs, `commit(...)` (descriptive subject)
14. Doc `i_documented(task_id, notes, files=[...])` → task → awaiting_pm_review, A2A to Cell PM
15. Cell PM spawned (gateway flag on)
16. Cell PM `triage` → returns the awaiting-PM-review task
17. Cell PM `note(scope='decision', text=...)`
18. Cell PM `complete(task_id, notes)` → auto-merges leaf PR → task completed
19. If this completes the parent: Main PM spawned, completes root → opens master PR + escalates to CEO
20. CEO approves via UI → final merge to master + complete

- [ ] **Step 12.4: Verify the 24 originals fixes**

Check the smoke-test logs for evidence each fix landed:

| Issue | Verification |
|---|---|
| #1, #4 | No `ToolSearch` instruction text in briefings; no "tool not available" errors |
| #2 | A2A skill resolution always uses `code_review` (canonical) |
| #3 | Subagent dispatch from non-Anthropic-routed agents uses parent's model |
| #5 | No `task_create` / `activate` errors — replaced by `i_will_work_on` |
| #6 | No `roboco_message_send` errors — `say()` accepts loose shapes |
| #7 | No journal-tool validation errors — `note()` accepts loose shapes |
| #8 | No `roboco://journals/None` warnings in orchestrator log |
| #9 | No `Missing X-Agent-ID header` warnings on `/notifications/pending-a2*` |
| #10 | MCP -32000 events (if any) followed by reconnect log |
| #11 | All `audit_log` entries have non-null `agent_id` (run query) |
| #12 | Single ordering: spawn manifest written → container up → claim happens with full audit attribution |
| #13 | Commit links use `ROBOCO_PUBLIC_BASE_URL` value (not 127.0.0.1) |
| #14 | `/api/v1/test/run` doesn't 500 on `make` not-found |
| #15 | QA's `claim_review` response shows `evidence.pr_url`/`pr_number`; QA passes (no false fail) |
| #16 | Agents never call `task_start` directly; gateway picks transition |
| #17 | No QA respawn for stale A2A on already-failed/passed task — visible via `gateway_triggers` table `decision='drop_stale'` rows |
| #18 | `roboco_git_log` accepts project slug |
| #19 | needs_revision recovery: dev's `i_will_work_on` works on a needs_revision task |
| #20 | A2A `dm()` auto-creates conversation; no `//messages` 404 |
| #21 | PM/Doc don't need to checkout dev branch — `claim_*` response carries evidence inline |
| #22 | Cell PM `complete` auto-merges; no PR-merge gap deadlock |
| #23 | Test scenario: block a task, then unblock — verify it returns to `pre_block_state` not `in_progress` |
| #24 | No multi-agent thundering herd (single-claimant + cooldown visible in `gateway_triggers` decisions) |

```bash
docker exec roboco-postgres psql -U roboco -d roboco -c "SELECT COUNT(*) FROM audit_log WHERE agent_id IS NULL;"
# Expected: 0

docker exec roboco-postgres psql -U roboco -d roboco -c "SELECT decision, COUNT(*) FROM gateway_triggers GROUP BY decision;"
# Expected: spawn rows AND drop_stale/queue/cooldown rows showing the filter works
```

- [ ] **Step 12.5: Run the full quality gate**

Run: `make quality`
Expected: every gate green.

- [ ] **Step 12.6: Tag the gateway delivery complete**

```bash
git tag agent-gateway-delivery-complete
git push origin agent-gateway-delivery-complete
```

---

## Task 13: Documentation update

**Files:**
- Modify: `CLAUDE.md` (project root)

- [ ] **Step 13.1: Update CLAUDE.md to reflect the gateway-only world**

Find the sections:
- "Task Lifecycle" → simplify; the lifecycle still exists but agents don't drive transitions directly
- "MCP Servers" → list only `roboco-flow`, `roboco-do`, `roboco-git-readonly`, `roboco-optimal` (mentor/KB)
- Add a new "Agent Gateway" section pointing at the spec + plans

- [ ] **Step 13.2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): update for gateway-only architecture; remove references to legacy MCP servers"
```

---

## Task 14: Close out tracked smoke-test issues

The 24 issues created during the 2026-05-01 smoke test should all be addressed at this point. For each, mark it `completed` in the project's task tracker.

- [ ] **Step 14.1: Close each issue with a reference**

For each of #1 through #24, close with a comment that names the phase + spec section that addressed it. Reference Appendix B of the spec for the canonical fix-mapping.

(This is a tracker-update task; the engineer uses the actual issue tracker.)

---

## Self-Review

1. **Spec coverage:** Phase 4 from §11 — Board verbs (Tasks 1-2), Auditor read-only (Task 3), API endpoints (Task 4), MCP wiring (Task 5), prompts (Tasks 6-7), gateway flag universal (Task 8), legacy MCP deletion (Task 9), v1 unmount (Task 10), tracing-completeness property test fully implemented (Task 11), final smoke test (Task 12), documentation update (Task 13). ✓
2. **Placeholder scan:** Tasks 9-10 leave the *files* in place for one PR to be safe (deleting v1 routers requires confirming no panel paths break). The cleanup is staged: unmount first (Task 10), delete after panel verification. The MCP server *files* in Task 9 are deleted outright because no caller remains. ✓
3. **Type consistency:** `escalate_to_ceo` in choreographer accepts agent_id, task_id, reason; same shape across roles. `auditor_triage` returns `Envelope`. All board endpoints under `/api/v2/flow/board/*`; auditor under `/api/v2/flow/auditor/*`. ✓
4. **Spec alignment:** Tracing-completeness property test (Task 11) checks every assertion from §6.4 of the spec. Cleanup matches §11 Phase 4 deliverables. The 24-issue → fix mapping (Appendix B of spec) is verified in Task 12.4. ✓

After all 14 tasks: the agent-gateway architecture is fully delivered. Phases 0-4 across the 5 plans cover every piece of the spec.
