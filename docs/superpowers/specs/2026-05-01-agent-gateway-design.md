# Agent Gateway — Design Spec

**Date:** 2026-05-01
**Author:** Renzo (with Claude as scribe)
**Status:** Draft, pending user review

---

## 1. Problem Statement

The 2026-05-01 smoke test produced 24 distinct failures across 7 themes. They share a single root cause from the agent's perspective: **agents are forced to know too much about the system** — exact tool names, field shapes, lifecycle gates, which verb fits which state, which agent owns which thing, which skill name means what, where workspaces live, when to merge a PR. Every gap between the agent's mental model and the API's reality becomes a runtime failure.

Representative breaks observed end-to-end:

- `be-qa` interpreted `ToolSearch(query="...")` in the session briefing as a literal `Bash` command (#1) — and so did `be-doc` later (#4).
- `be-qa` repeatedly false-failed task `59142daa` with "no PR" while `task.pr_number=8` and `task.pr_url=https://github.com/rennf93/roboco/pull/8` sat in the response payload it had received (#15).
- `be-qa` had to call `task_start` to satisfy the lifecycle (`claimed → in_progress`) before it could `qa_fail`, conflating dev work and QA review under the same state (#16).
- The lifecycle deadlocked because `complete` requires the PR merged but no role had a `merge_pr` verb that fit (#22). PMs/PO/Doc bounced off `complete`, `submit_pm_review`, `escalate_to_ceo`, all gated on different states.
- Multi-agent thundering herd: 4+ agents simultaneously holding stale views of one task, each escalating to the next, each spawn triggering more spawns (#17, #24).

The remaining issues fall into schema friction (#5, #6, #7, missing `notes` on `docs_complete`, `doc_type` enum), workspace isolation (#21 — PMs/Docs can't see dev's branch), skill registry mismatches (#2), and infrastructure-level bugs (#3 model routing, #8 RAG None, #9 missing header, #10 MCP drops, #13 link config, #14 missing `make`, #18 git project lookup, #20 a2a URL builder).

---

## 2. Goals & Non-Goals

**Goals**

1. Agents call **intent verbs** that match how they think, not API choreography.
2. **Tracing** (journals, channel/A2A communications, task metadata) is enforced server-side as workflow gates, not best-effort agent behavior.
3. **Ground rules cannot be bypassed.** Single-claimant invariant, PR merge chain, role-appropriate transitions are enforced in code, not in prompts.
4. **Slim role prompts** — under 20 lines per role. Workflow knowledge moves into the gateway.
5. **No ToolSearch.** Tools pre-loaded at container spawn.
6. **Recycle existing code aggressively.** The gateway composes existing services and enforcement modules; it does not reimplement them.
7. **Hard quality gates.** ruff, mypy, pytest, coverage, vulture, bandit, pip-audit, deptry, radon, xenon all wired into a single gating script.

**Non-Goals**

- Replacing the underlying task-state machine. State semantics stay; only the surface and the choreography change.
- Multi-tenant isolation, SaaS, external customers. Single-tenant homelab continues.
- Removing roles or restructuring the org. The 18+1 organizational model stays.
- Replacing Claude Code SDK / minimax / Ollama Cloud. Provider-routing fixes are tracked separately (#3).

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         agent container                              │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │ Claude Code SDK (loop, model)                              │     │
│  │   ↓ tool call: roboco_<verb>(...)                          │     │
│  │ MCP server: roboco-flow  — intent verbs (per-role subset)  │     │
│  │ MCP server: roboco-do    — smart-wrapped content tools     │     │
│  │ Tools pre-registered at startup (no ToolSearch)            │     │
│  └────────────────────────────────────────────────────────────┘     │
│                       ↓ HTTP (X-Agent-ID auto-injected by SDK shim) │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      orchestrator (FastAPI)                          │
│   /api/v2/flow/*    intent-verb endpoints                            │
│   /api/v2/do/*      smart-wrapped content endpoints                  │
│                                                                      │
│   roboco/services/gateway/                                           │
│     ├─ choreographer.py      smart catch-up sequences                │
│     ├─ claimant_lock.py      single-active-agent invariant           │
│     ├─ trigger_filter.py     stale-trigger cleanup + cooldown        │
│     ├─ evidence_builder.py   verb-response payload assembly          │
│     ├─ merge_chain.py        PR merge chain (Cell PM → Main PM → CEO)│
│     ├─ tracing_gate.py       precondition checks for tracing         │
│     └─ remediation.py        structured error responses with hints   │
│                                                                      │
│   roboco/services/  (existing, composed by gateway — see §4)         │
│   roboco/enforcement/ (existing, composed by gateway — see §4)       │
│   /api/v1/*  — removed phase-by-phase as roles migrate               │
└─────────────────────────────────────────────────────────────────────┘
```

**Key choices**

- **Logic lives in `roboco/services/gateway/`** (orchestrator-side). MCP servers (`roboco-flow`, `roboco-do`) are thin protocol shims. Single source of truth for state, easier to test, leverages existing FastAPI infrastructure and DB session patterns.
- **No backwards compatibility shims.** Each role's migration deletes the old endpoints/MCP tools in the same PR. Cross-role coordination during transition happens through the shared `tasks` table — both old and new code paths read/write the same rows.
- Two new MCP servers, role-scoped at registration time:
  - `roboco-flow` — intent verbs (`give_me_work`, `i_will_work_on`, `i_am_done`, `pass`, `complete`, etc.)
  - `roboco-do` — smart-wrapped content tools (`commit`, `note`, `say`, `dm`, `evidence`)
- The current `Agent` (subagent) tool stays available only for roles with a legitimate need for parallel research (PM/Board); for other roles it's omitted from the manifest.

---

## 4. Code Reuse Map

The gateway composes existing modules. It does not reimplement state transitions, ownership checks, git operations, or messaging primitives.

### 4.1 Services recycled (`roboco/services/`)

| Module | Used by gateway for |
|---|---|
| `task.py` (TaskService) | All lifecycle transitions (`claim`, `start`, `submit_qa`, `qa_pass`, `qa_fail`, `unblock`, `complete`, `escalate`, `escalate_to_ceo`). Gateway composes these into intent verbs; never reimplements transition logic. |
| `work_session.py` (WorkSessionService) | Branch/commit/PR session tracking. `i_have_committed`, `i_am_done`, `complete` (PM merge) call into it. |
| `workspace.py` (WorkspaceService) | Resolving + ensuring agent workspace clones. `evidence_builder` uses it for the optional auto-fetch of dev branches. |
| `git.py` (GitService) | Git operations — `git_status`, `git_log`, `git_diff`, `git_branch_list`, `git_commit`, `git_push`, `git_create_pr`, `git_pr_merge` (new). `merge_chain` composes these per scope. |
| `messaging.py` (MessagingService) | Channel + message operations behind `say()`. Existing schema (channels, sessions, messages) reused as-is. |
| `a2a.py` (A2AService) | Agent-to-agent conversations + messages behind `dm()`. Existing schema reused. |
| `journal.py` (JournalService) | Entries, decisions, learnings, struggles, reflections behind `note()` and behind auto-capture. |
| `notification.py` + `notification_delivery.py` | Notify list/ack + delivery routing. `context_briefing` builder reads the agent's pending notifications. |
| `audit.py` (AuditService) | Audit log writes for every gateway-driven transition. Fixes #11 (NULL agent_id) by ensuring the gateway always knows the actor before delegating. |
| `permissions.py` (PermissionsService) | Role-based access — gateway calls it before any state-changing operation. |
| `agent.py` (AgentService) | Agent metadata (skills, role, team, escalation target). Used by spawn manifest builder + skill resolution. |
| `learning.py` (LearningService) | Read by `give_me_work` to surface relevant past learnings in the context briefing. |
| `optimal.py` (OptimalService) | RAG queries — `evidence_builder` includes top-3 relevant past resolutions for the task in the context briefing (when an agent is stuck or in `needs_revision`). |
| `provider.py` (ProviderService) | Multi-provider LLM routing. Gateway uses it to determine the agent's model when constructing default subagent model (#3 fix). |
| `repositories/` | Data access layer. All DB writes go through it. Gateway adds new repository methods for `claimant_lock` and `pre_block_state` snapshot. |

### 4.2 Enforcement recycled (`roboco/enforcement/`)

| Module | Used by gateway for |
|---|---|
| `task_lifecycle.py` | The state machine itself. Gateway calls into the existing transition validator before any `tasks.status` write. The set of allowed transitions does not change; the gateway just ensures the right transitions are called for each verb. |
| `task_ownership.py` | Ownership checks (assignee, role permissions, PM-only verbs). The gateway calls these before delegating to TaskService. |
| `channel_access.py` | Channel write permissions for `say()`. |
| `a2a_access.py` | A2A send permissions for `dm()`. |
| `journal_perms.py` | Journal write permissions for `note()`. |
| `notification_perms.py` | Notification create permissions for PM-level escalations. |

### 4.3 Models extended (`roboco/models/`)

Schema additions (Alembic migrations, hand-written per project policy):

```python
# tasks table additions
active_claimant_id: UUID | None  # FK -> agents.id
last_heartbeat_at: datetime | None
pre_block_state: TaskStatus | None
pre_block_assignee: UUID | None
pre_block_metadata: JSON | None  # snapshot of fields restored on unblock

# new table: gateway_triggers (records dispatcher decisions for observability)
id: UUID
trigger_kind: str           # 'a2a' | 'notification' | 'scan' | 'escalation'
task_id: UUID | None
target_role: str
decision: str               # 'spawn' | 'queue' | 'drop_stale' | 'cooldown'
decision_reason: str
created_at: datetime
```

The state-machine enum (`TaskStatus`) does not change. The gateway uses it as-is.

### 4.4 Routes recycled (`roboco/api/routes/`)

Existing routes are removed phase-by-phase as their corresponding role migrates. New `/api/v2/flow/*` and `/api/v2/do/*` routes are thin handlers — they parse the request, call into `roboco/services/gateway/`, and return the standardized response envelope (see §5.4).

### 4.5 MCP recycled (`roboco/mcp/`)

The current MCP servers (`task_server.py`, `message_server.py`, `journal_server.py`, `notify_server.py`, `a2a_server.py`, `optimal_server.py`, `project_server.py`, `docs_server.py`, `git/`, `tasks/`, `test/`) get **deleted as their roles migrate**. New servers:

```
roboco/mcp/
  flow_server.py        # roboco-flow — intent verbs
  do_server.py          # roboco-do — smart-wrapped content tools
  schemas/              # existing — extended with v2 schemas
  utils.py              # existing — reused for SDK helpers
```

The MCP server pattern (FastMCP with handler dispatch) is kept; only the tool set changes.

### 4.6 Runtime (`roboco/runtime/`)

`orchestrator.py` extended with:
- `spawn_manifest_builder` — composes per-role tool list from role config
- Pre-spawn `trigger_filter` consultation (no spawn if stale or cooldown)
- Pre-spawn `claimant_lock` consultation (no spawn if active claimant on target task)

`streaming.py` reused as-is for SSE push of context-briefing updates if we need them later (post-MVP).

---

## 5. Agent Surface

### 5.1 Intent verbs (`roboco-flow`)

Per-role manifest. Verbs not in your manifest are not registered at spawn — calling them returns "tool not available".

| Role | Verbs |
|---|---|
| **Dev** | `give_me_work`, `i_will_work_on(task_id)`, `i_have_committed(message)`, `i_am_done(notes)`, `i_am_blocked(reason)`, `i_am_idle` |
| **QA** | `give_me_work`, `claim_review(task_id)`, `pass(notes)`, `fail(issues)`, `i_am_idle` |
| **Doc** | `give_me_work`, `claim_doc_task(task_id)`, `i_documented(notes, files)`, `i_am_idle` |
| **Cell PM** | `triage`, `unblock(task_id)`, `complete(task_id)`, `escalate_up(task_id, reason)`, `i_am_idle` |
| **Main PM** | `triage_all`, `complete(task_id)` (opens master PR + escalates to CEO), `escalate_up(task_id, reason)`, `i_am_idle` |
| **Board (PO, Head-Marketing, Auditor)** | `triage`, `escalate_to_ceo(task_id, reason)`, `i_am_idle`. (Auditor: read-only verbs only.) |
| **CEO** | UI-driven; agents do not impersonate CEO. |

### 5.2 Smart-wrapped content tools (`roboco-do`)

Available to all working roles (Dev, QA, Doc, PMs).

| Tool | Behavior |
|---|---|
| `commit(message, files=None)` | Auto-prefixes `[task-id]`; auto-derives `progress` entry; rejects if not on task branch with hint. **Descriptive-message gate**: rejects messages shorter than 20 chars, single-word messages, or low-content patterns (`wip`, `fix`, `update`, `oops`, `tmp`, `asdf`) with `remediate` showing a Conventional-Commits template like `feat(scope): describe what changed and why`. The auto-prefix `[task-id]` is added on top of the descriptive subject; the agent supplies the meaningful part. |
| `note(text, scope='note', task_id=None)` | One tool replacing all journal verbs. `scope ∈ {note, decision, learning, struggle, reflect}`. Auto-fills `task_id` from active claim if not provided; auto-derives `title` from first line. |
| `say(channel, text, task_id=None)` | Channel message. `task_id` auto-injected when agent has active task; explicit `null` allowed for announcements. Replaces `roboco_message_send`. |
| `dm(agent, text, task_id=None, skill=None)` | A2A send. **Skill auto-resolution**: if `skill` is `None`, gateway picks the first skill the recipient has from a role-aware preference list (e.g., when sender is dev contacting a QA role: `[code_review, qa_review, test_validation]` — picks the first the recipient agent's `agent.skills` set actually contains). If `skill` is explicit but the recipient doesn't have it, gateway substitutes the closest match from the same preference list and includes a header `note: substituted skill 'qa_review' → 'code_review'` in the audit log. Caller-side fallback for #2; the canonical fix is the Phase 0 skill registry migration. **Conversation auto-create**: if no conversation exists between sender and recipient for the given `task_id`, gateway creates one before sending (fixes #20). |
| `evidence(task_id)` | The opt-in deep inspection. Fetches dev branch into caller's workspace (read-only ref), returns full PR diff + commits + files + journal. |

### 5.3 Removed from agent surface

These existing tools are deleted as their roles migrate. The gateway calls into the underlying services internally:

```
roboco_task_claim         roboco_task_unclaim
roboco_task_plan          roboco_task_start
roboco_task_progress      roboco_task_pause
roboco_task_block         roboco_task_unblock
roboco_task_submit_verification
roboco_task_submit_qa     roboco_task_submit_pm_review
roboco_task_qa_pass       roboco_task_qa_fail
roboco_task_complete      roboco_task_escalate
roboco_task_escalate_to_ceo
roboco_task_substitute    roboco_task_docs_complete
roboco_task_create        roboco_task_activate
roboco_task_get           roboco_task_scan       (replaced by give_me_work, claim_*)
roboco_journal_entry      roboco_journal_decision
roboco_journal_learning   roboco_journal_struggle
roboco_journal_reflect    roboco_journal_search
roboco_journal_recent
roboco_message_send       roboco_channel_history
roboco_notify_list        roboco_notify_ack       (gateway auto-acks via context_briefing)
roboco_agent_request      roboco_a2a_check
roboco_git_create_pr      roboco_git_pr_merge     (gateway internal)
roboco_workspace_ensure   roboco_workspace_status (gateway internal)
roboco_agent_idle         (replaced by i_am_idle)
ToolSearch                (no longer needed, see §9)
Agent                     (omitted for Dev/QA/Doc; available to PMs/Board)
```

Read-only git tools stay: `roboco_git_status`, `roboco_git_log`, `roboco_git_diff`, `roboco_git_branch_list`. These are useful debugging primitives and don't violate the gateway contract.

The mentor + KB tools stay: `roboco_ask_mentor`, `roboco_kb_search`, `roboco_search_error`. Self-help is a feature.

### 5.4 Standardized response envelope

Every intent-verb response uses the same shape:

```json
{
  "status": "<resulting task status>",
  "task_id": "<uuid>",
  "next": "<one-line guidance: what the agent should do next>",
  "evidence": {
    "pr_url": "...", "pr_diff_summary": "...",
    "commits": [...], "files_changed": [...],
    "dev_summary": "...", "journal_highlights": [...],
    "acceptance_criteria_status": [...]
  },
  "context_briefing": {
    "unread_a2a": [...],
    "unread_mentions": [...],
    "pending_notifications": [...],
    "task_metadata_gaps": [...],
    "recent_team_activity": [...],
    "blockers_in_my_lane": [...]
  },
  "remediate": null         // populated only on tracing-gate failures, see §6
}
```

For error responses:

```json
{
  "error": "<machine-readable code>",
  "message": "<one-line human description>",
  "remediate": "<concrete next-step instruction>",
  "missing": [...],         // when error is tracing_gap or schema_gap
  "context_briefing": {...} // even on errors, the briefing comes back
}
```

This single envelope is the surface contract; everything else is internal.

---

## 6. Tracing Enforcement

Tracing (journals, chat/A2A, task metadata) is treated as **workflow gates**, not best-effort agent behavior. Three mechanisms.

### 6.1 Pre-conditions block transitions

Verbs reject when tracing isn't current. The `roboco/services/gateway/tracing_gate.py` module is the single source of truth for these rules.

| Verb | Required tracing |
|---|---|
| `i_will_work_on(task_id)` | Plan field set on task (gateway prompts for plan if empty before claim succeeds — returns `remediate: "Submit plan via i_will_work_on(task_id, plan='...')"`); all unread A2As to me about this task acked (auto-acked when verb returns successfully). |
| `i_have_committed(message)` | Plan exists; commit message non-empty; auto-creates progress entry from message. |
| `i_am_done(notes)` | ≥1 `progress` entry; `journal:reflect` for this task; **every acceptance criterion has at least one referencing artifact** (commit sha / progress entry id / note id); no unread A2A about this task. |
| `pass(notes)` / `fail(issues)` | `qa_notes ≥ 80 chars`; `journal:learning` written; `evidence_inspected` flag true (gateway tracks whether QA called `evidence()` or `roboco_task_diff` for the task). |
| `complete(task_id)` (Cell PM) | `journal:decision` written; all subtasks terminal; PR mergeable; merge succeeds before transition. |
| `complete(task_id)` (Main PM) | `journal:decision` written; opens master PR; transitions to `awaiting_ceo_approval`. CEO approval is UI-side. |
| `i_am_idle` | No unread A2A directed at me; no @mentions in my channels in last 5min unaddressed. (Soft-block: returns "you have N unread A2As; ack or address before going idle".) |

Gate failures return:

```json
{
  "error": "tracing_gap",
  "missing": [
    "acceptance criterion 3 has no commit/note reference",
    "no journal:reflect for this task"
  ],
  "remediate": "call note(scope='reflect', task_id='...', text='<reflection addressing all 4 criteria>'), then i_am_done(notes='...')"
}
```

### 6.2 Auto-capture — gateway writes tracing artifacts itself

Wherever the gateway *can* record on the agent's behalf, it does. Agents can't forget what they don't have to remember.

- Every verb call → `audit_log` entry with `actor_id`, `task_id`, `from_state`, `to_state`, `reason` (fixes #11 NULL actors and #12 inconsistent ordering — gateway is the single point of audit-write)
- `commit` → progress entry with commit message + sha
- `i_will_work_on` → `journal:decision` "Claimed task X. Plan: <plan summary>"
- `pass` / `fail` → `journal:learning` derived from QA notes + outcome
- `complete` → `journal:decision` derived from PM notes
- `i_am_blocked` → `journal:struggle` with reason
- `i_am_done` (when called) → if no `journal:reflect` exists, gateway returns the gate failure with a *template* in `remediate` so the agent knows what to fill in

### 6.3 Inbound surfacing — every verb response carries the briefing

`context_briefing` is built by `roboco/services/gateway/evidence_builder.py` for every verb response (success or error). Sources:

- `unread_a2a` ← `A2AService.list_unread(agent_id)`
- `unread_mentions` ← `MessagingService.list_mentions(agent_id, since=last_seen)`
- `pending_notifications` ← `NotificationService.list_unacked(agent_id)`
- `task_metadata_gaps` ← `TracingGate.check_task(task_id)` → list of missing fields
- `recent_team_activity` ← `MessagingService.list_team_recent(team, limit=5)`
- `blockers_in_my_lane` ← TaskService query: `tasks WHERE team=my_team AND status='blocked'`

The briefing is bounded (each list capped at 10 items) so verb responses stay compact.

Agents do not need separate `roboco_notify_list` / `channel_history` / `a2a_check` calls. Every verb already brings the agent up to date.

### 6.4 Task-metadata completeness contract

Per-task fields the gateway treats as MUST-be-populated for the lifecycle to advance:

| Field | Required by | Source of truth |
|---|---|---|
| `plan` | `i_have_committed` (any work step) | `tasks.plan` JSON |
| `progress[]` (≥1) | `i_am_done` | `tasks.progress_updates` JSON |
| `dev_notes` | aggregated from `note` calls during in_progress | `tasks.dev_notes` text |
| `acceptance_criteria_status[]` | `i_am_done` — every criterion has `referencing_artifact_id` | new column: `tasks.acceptance_criteria_status` JSON |
| `qa_notes` | `pass` / `fail` | `tasks.qa_notes` text |
| `qa_evidence_inspected` | `pass` / `fail` (gateway-tracked) | new column: `tasks.qa_evidence_inspected` bool |
| `docs_complete` + `docs_notes ≥ 20 chars` | PM `complete` | existing |
| `journal:reflect` (latest) for task | `i_am_done` (dev), `pass`/`fail` (QA), `complete` (PM) | `journal_entries` query |
| `pm_decision` (journal) | PM `complete` | `journal_entries` query |

Even if an agent calls a content tool directly (`note`, `commit`), the artifact is recorded on the right task with the right scope. There is no path to "I did the work but skipped the trail" — every state transition is gated on the trail existing.

---

## 7. Lifecycle Ownership + PR/Merge Chain

The gateway owns lifecycle transitions. Agents express intent; the choreographer picks the API calls.

### 7.1 Smart catch-up sequences

`roboco/services/gateway/choreographer.py` implements these as pure functions. Each step is gated by `tracing_gate` and `task_lifecycle.allowed_transitions`.

**Dev `i_am_done(task_id, notes)`**

```
def i_am_done(agent_id, task_id, notes):
    task = TaskService.get(task_id)
    require_assignee(agent_id, task)
    tracing_gate.require(task, ['progress>=1', 'journal:reflect', 'acceptance_criteria_status'])
    if not task.self_verified:
        TaskService.submit_verification(agent_id, task_id, notes)  # claimed/in_progress -> verifying
    if WorkSessionService.has_unpushed_commits(task.work_session_id):
        GitService.push(task.branch_name)
    if task.pr_number is None:
        GitService.create_pr(task.branch_name, parent=parent_branch_for(task), is_root_pr=False)
    TaskService.submit_qa(agent_id, task_id, notes)
    A2AService.send(from=agent_id, to=qa_agent_for(task.team),
                    skill=resolve_existing_skill(qa_agent, ['code_review','qa_review']),
                    task_id=task_id, body='Ready for review')
    return envelope(status='awaiting_qa', evidence=evidence_builder.build(task), next='idle until QA')
```

**Dev `i_will_work_on(task_id, plan=None)`** — works for `pending`, `claimed`, OR `needs_revision` (kills #19, #23):

```
def i_will_work_on(agent_id, task_id, plan=None):
    task = TaskService.get(task_id)
    if task.status == TaskStatus.NEEDS_REVISION:
        if task.assigned_to != agent_id:
            TaskService.claim(agent_id, task_id)
        TaskService.start(agent_id, task_id)  # needs_revision -> in_progress
    elif task.status == TaskStatus.PENDING:
        TaskService.claim(agent_id, task_id)
        if not task.plan and not plan:
            return envelope(error='tracing_gap', missing=['plan'],
                            remediate=f'call i_will_work_on(task_id={task_id}, plan="...")')
        TaskService.set_plan(task_id, plan or auto_plan_from_description(task))
        TaskService.start(agent_id, task_id)
    elif task.status == TaskStatus.CLAIMED and task.assigned_to == agent_id:
        TaskService.start(agent_id, task_id)
    return envelope(status='in_progress', task=task, next='edit + commit')
```

**Cell PM `complete(task_id, notes)`** — auto-merges leaf PR + cascades (kills #22):

```
def cell_pm_complete(pm_agent_id, task_id, notes):
    task = TaskService.get(task_id)
    require_role(pm_agent_id, ['cell_pm'])
    require_status(task, [TaskStatus.AWAITING_PM_REVIEW])
    tracing_gate.require(task, ['journal:decision', 'all_subtasks_terminal'])
    parent_branch = merge_chain.parent_branch_for(task)
    GitService.pr_merge(task.pr_number, target=parent_branch)
    TaskService.complete(pm_agent_id, task_id, notes)
    if all_children_terminal(task.parent_task_id):
        NotificationService.notify_pm_of_parent(task.parent_task_id)
    return envelope(status='completed', merged_to=parent_branch, next='triage next')
```

**Main PM `complete(root_task_id, notes)`** — opens master PR, escalates to CEO:

```
def main_pm_complete(pm_agent_id, root_task_id, notes):
    task = TaskService.get(root_task_id)
    require_role(pm_agent_id, ['main_pm'])
    require_status(task, [TaskStatus.AWAITING_PM_REVIEW])
    tracing_gate.require(task, ['journal:decision'])
    if task.pr_number is None or pr_target_is_not_master(task.pr_number):
        GitService.create_pr(task.branch_name, parent='master', is_root_pr=True)
    TaskService.escalate_to_ceo(pm_agent_id, root_task_id, notes)
    return envelope(status='awaiting_ceo_approval', next='idle until CEO acts')
```

**CEO approve** — UI action, not an agent verb:

```
def ceo_approve(ceo_user, root_task_id):
    task = TaskService.get(root_task_id)
    require_status(task, [TaskStatus.AWAITING_CEO_APPROVAL])
    GitService.pr_merge(task.pr_number, target='master')
    TaskService.complete(actor=ceo_user_uuid, root_task_id, notes='CEO approved')
    cascade_complete_children(task)
```

### 7.2 PR/Merge chain

| Layer | Branch pattern | Who creates PR | Who merges |
|---|---|---|---|
| Leaf task | `feature/backend/A--B--C` | Dev (`i_am_done` opens it pre-QA) | Cell PM (`complete` merges → parent branch) |
| Sub-parent task | `feature/backend/A--B` | Cell PM (auto-opens when first child merged in, or via `open_parent_pr`) | Cell PM (`complete` merges → grandparent) up the chain |
| Root task | `feature/backend/A` | Main PM (`complete` opens master PR) | CEO via UI (approve → merge to master) |

The chain logic lives in `roboco/services/gateway/merge_chain.py`. It composes `GitService` operations; it does not reimplement git.

### 7.3 What disappears from the lifecycle complexity

- **#16 (state-overloading) gone**: agents never call `task_start`, `submit_verification`, `submit_pm_review`. Those exist as TaskService methods only, called by the choreographer.
- **#19, #23 (needs_revision / blocked recovery)**: `i_will_work_on` accepts `needs_revision`; `unblock` restores `pre_block_state` not `in_progress`.
- **#22 (PR merge gap)**: `complete` always pairs with a merge appropriate to the role's scope. Never two separate verbs.

---

## 8. Coordination — Single-Claimant, Herd Control, State Restoration

Three structural rules. All live in `roboco/services/gateway/claimant_lock.py` and `roboco/services/gateway/trigger_filter.py`.

### 8.1 Single-claimant per task

- New columns: `tasks.active_claimant_id`, `tasks.last_heartbeat_at`.
- Heartbeat: every gateway verb call updates `last_heartbeat_at` for the task it touches.
- New trigger (notification, A2A, scan) for a task that already has an active claimant: orchestrator's spawner skips spawning. The trigger is left in place (still readable as unread A2A / unacked notification) and surfaces in `context_briefing` to the active agent on their next verb call.
- Stale claim detection: if `last_heartbeat_at > now() - 180s`, `claimant_lock` auto-releases on the next conflicting trigger and writes audit `claim_released_stale`.

### 8.2 Stale-trigger cleanup + cooldown

Before `roboco/runtime/orchestrator.dispatcher_spawn(task_id, trigger_kind, trigger_id)`:

```python
def should_spawn(task_id, trigger_kind, trigger_id) -> SpawnDecision:
    task = TaskService.get(task_id)

    # 1. stale-trigger cleanup
    if trigger_filter.is_stale(trigger_kind, trigger_id, task):
        TriggerLog.record(decision='drop_stale', reason='task_state_advanced')
        if trigger_kind == 'a2a': A2AService.mark_resolved(trigger_id)
        if trigger_kind == 'notification': NotificationService.mark_resolved(trigger_id)
        return SpawnDecision.DROP

    # 2. single-claimant check
    if task.active_claimant_id and not claimant_lock.is_stale(task):
        TriggerLog.record(decision='queue', reason='active_claimant')
        return SpawnDecision.QUEUE

    # 3. spawn cooldown
    if recent_spawns_for_task(task_id, window=60).count >= 1:
        TriggerLog.record(decision='cooldown', reason='spawn_cooldown_60s')
        return SpawnDecision.QUEUE

    # 4. role-rate-limit
    if recent_spawns_for_role(target_role, window=60).count >= 6:
        TriggerLog.record(decision='cooldown', reason='role_rate_limit')
        return SpawnDecision.QUEUE

    TriggerLog.record(decision='spawn')
    return SpawnDecision.SPAWN
```

`gateway_triggers` table (see §4.3 schema additions) records every decision for observability.

### 8.3 State restoration on unblock

- Transitioning to `blocked` snapshots `pre_block_state`, `pre_block_assignee`, and a JSON `pre_block_metadata` of relevant task fields (current claim, last progress entry id, etc.).
- `unblock(task_id, restore=True)` (default): gateway restores to `pre_block_state` and re-applies the snapshot. So `awaiting_documentation → blocked → unblock → awaiting_documentation` (not `in_progress`).
- `unblock(task_id, restore=False)`: legacy behavior — back to `in_progress` for assignee.

### 8.4 What this kills

- **#17** (stale A2A respawning QA): trigger filter drops the stale A2A and marks it resolved.
- **#24** (thundering herd): single-claimant + per-task cooldown + per-role rate limit prevents 4 agents on one task.
- **#23** (no path back to awaiting_pm_review after blocked): state restoration on unblock fixes the loop.
- **#11, #12** (audit actor / ordering): gateway is single audit writer with full actor context.

---

## 9. Tool Bootstrap — No ToolSearch

Two-part change.

### 9.1 Spawn manifest (orchestrator-side)

`roboco/runtime/spawn_manifest.py`:

```python
@dataclass
class SpawnManifest:
    agent_id: UUID
    role: str
    team: str
    workspace_path: Path
    flow_tools: list[str]      # roboco-flow verbs allowed for this role
    do_tools: list[str]        # roboco-do tools allowed for this role
    read_tools: list[str]      # Read, Glob, Grep
    write_tools: list[str]     # Edit, Write (workspace-scoped)
    bash_allowed: bool         # always True, but bash-guard hook still applies
    subagent_allowed: bool     # True for PM/Board, False for Dev/QA/Doc
    subagent_model: str | None # parent's model (fixes #3); None when not allowed
    env: dict[str, str]        # X-Agent-ID, ROBOCO_PUBLIC_BASE_URL, etc.

def build(agent_id: UUID) -> SpawnManifest:
    agent = AgentService.get(agent_id)
    role_config = ROLE_CONFIGS[agent.role]    # static dict in roboco/agents_config.py
    return SpawnManifest(
        agent_id=agent_id, role=agent.role, team=agent.team,
        workspace_path=WorkspaceService.path_for(agent),
        flow_tools=role_config.flow_tools,
        do_tools=role_config.do_tools,
        read_tools=['Read', 'Glob', 'Grep'],
        write_tools=['Edit', 'Write'] if role_config.allows_write else [],
        bash_allowed=True,
        subagent_allowed=role_config.allows_subagent,
        subagent_model=ProviderService.model_for(agent) if role_config.allows_subagent else None,
        env={...},
    )
```

The manifest is written to `/app/tool-manifest.json` inside the agent container at spawn (mounted via Docker volume + env var).

### 9.2 SDK shim auto-registration

The existing SDK shim on port 9000 (already in agent containers — observed in smoke test as `[SDK] Starting for agent be-doc on port 9000`) reads `/app/tool-manifest.json` at startup and **registers every listed tool with the Claude SDK before the agent's first turn**.

Session briefing change:

```diff
- # Session briefing — be-dev-1
- ## First action required
- Before any other tool call, run ToolSearch to enable the tools
- your role needs. Copy this verbatim as your first action:
- ```
- ToolSearch(query="select:Edit,Write,Bash,Read,Glob,Grep,mcp__roboco-task__...")
- ```

+ # Session briefing — be-dev-1
+ Your tools are loaded. Start by:
+   1. Reading any unread notifications/A2As (will surface in your first verb response)
+   2. Calling `give_me_work` to get your task — or `i_will_work_on(task_id)` if you've been assigned one.
```

ToolSearch tool itself is removed from the manifest. Agents *cannot* call it because it isn't there.

### 9.3 What this kills

- **#1** (ToolSearch interpreted as bash): impossible — no instruction to copy-paste.
- **#4** (agents skip ToolSearch): impossible — nothing to skip.
- **#3** (subagent default model): `subagent_model` defaults to parent's model; haiku/sonnet routing gap dodged.

---

## 10. Slim Role Prompts

Concrete example: `agents/prompts/roles/developer.md` after migration (~15 lines, down from 49):

```markdown
# Developer

You implement features, fix bugs, and write code.

## Who you are
- Team: {team}    Workspace: /data/workspaces/{project}/{team}/{your-slug}/
- You commit + push. You don't merge. PMs merge. CEO approves master.

## Your verbs (already loaded)
- give_me_work() — you'll get a task or `idle`
- i_will_work_on(task_id, plan=None) — claims/starts/recovers any state of yours
- commit(message) — auto-prefixed [task-id]; auto-progress entry
- note(text, scope?) — journal. scope ∈ note|decision|reflect|learning|struggle
- i_have_committed(message) — quick alias if you want
- i_am_blocked(reason) — escalates and idles you
- i_am_done(notes) — runs verify/push/PR/submit-qa. The gateway will tell you exactly what's missing.
- evidence(task_id) — fetches PR diff if you need to inspect something
- i_am_idle — done for now

## Ground rules
- Edit/Write/Bash limited to your workspace.
- Tracing is enforced server-side. Reflect-journal + acceptance criteria addressed before `i_am_done`.
- Verb errors include a `remediate` field — follow it. Don't bypass.
- If unsure, call `give_me_work` and read the response.
```

QA, Doc, PM, Board prompts follow the same shape — 15-20 lines each, role-specific verb list, same ground-rules pattern. The current `agents/prompts/base.md` is also rewritten to match (no more state machine table; just identity + how the gateway speaks back).

---

## 11. Rollout Sequence

### Phase 0 — Foundations (no agent-visible change yet)

**Estimated: 3-5 days**

- `roboco/services/gateway/` skeleton + tests (each module)
- `claimant_lock`, `trigger_filter`, `tracing_gate` modules
- Alembic migration: `tasks.active_claimant_id`, `tasks.last_heartbeat_at`, `tasks.pre_block_state`, `tasks.pre_block_assignee`, `tasks.pre_block_metadata`, `tasks.acceptance_criteria_status`, `tasks.qa_evidence_inspected`; new `gateway_triggers` table
- Spawn manifest builder + Docker volume wiring + SDK shim auto-registration code (gated behind `ROBOCO_GATEWAY_ENABLED=false` env flag — old briefing still works)
- Skill registry alignment (#2): canonical skill set defined in `roboco/agents_config.py` as a single source of truth (e.g., QA roles standardize on `code_review`; `qa_review` is dropped from the canonical set). Migration `alembic/versions/NNN_align_skills.py`: backfills existing agents.skills arrays with the canonical names, and any caller passing the old name is auto-substituted via the `dm()` resolution rule (§5.2) so no in-flight A2A breaks during the rollout. The single source of truth lives in code; agent seeds reference it.
- Parallel small-fixes track (see §13) ships in this phase

**Exit criteria:** all gateway service modules pass unit tests; spawn manifest builds correctly for each role (visible via `python -m roboco.runtime.spawn_manifest --role developer`); no agent-visible changes yet.

### Phase 1 — Developer cutover

**Estimated: 5-7 days**

- `roboco-flow` MCP server with dev verbs
- `roboco-do` MCP server with `commit`, `note`, `say`, `dm`, `evidence`
- `/api/v2/flow/dev/*` and `/api/v2/do/*` endpoints
- Dev role prompt rewrite + spawn manifest swap (devs only)
- Old `roboco-task` developer-only verbs removed from MCP server registration (`task_claim`, `task_start`, `task_plan`, `task_progress`, `task_submit_verification`, `task_submit_qa`)
- Smoke test runs: dev-side workflow only; QA/PM/Doc still on old tools but read/write the same `tasks` rows

**Exit criteria:** smoke test completes dev work end-to-end through the new verbs without regressions.

### Phase 2 — QA cutover

**Estimated: 3-5 days**

- QA verbs: `claim_review`, `pass`, `fail`
- Old `roboco_task_qa_pass` / `qa_fail` removed from MCP server registration
- Smoke test: full dev → QA cycle on new infra

**Exit criteria:** dev → QA → awaiting_documentation completes through new verbs.

### Phase 3 — Doc + PMs cutover

**Estimated: 5-7 days**

- Doc verbs: `claim_doc_task`, `i_documented`
- Cell PM verbs: `triage`, `unblock`, `complete` (with auto-merge), `escalate_up`
- Main PM verbs: `complete` (with master PR + CEO escalation), `triage_all`
- All non-PM workflow verbs removed from MCP server registration
- `merge_chain` auto-merge enabled for PMs
- Smoke test: pending → completed (CEO approves)

**Exit criteria:** smoke test completes `pending → completed` end-to-end with CEO approval as the only manual step.

### Phase 4 — Board + cleanup

**Estimated: 2-3 days**

- Board (Product Owner, Head-Marketing, Auditor) verbs
- Remaining `/api/v1/*` endpoints removed (only `/api/v2/*` remains)
- Old MCP servers (`task_server.py`, `message_server.py`, `journal_server.py`, `notify_server.py`, `a2a_server.py`, `docs_server.py`, `project_server.py`, `tasks/`, `git/`, `test/`) deleted
- `agents/prompts/base.md` and remaining role prompts trimmed to slim form
- Old tool registration code removed from spawn flow

**Exit criteria:** `git grep -r "roboco_task_claim\|roboco_journal_entry\|ToolSearch" agents/ roboco/` returns nothing. Full smoke test still passes.

**Total: ≈3 weeks end-to-end. Each phase is independently shippable; the smoke test is the gate between phases.**

---

## 12. Parallel Small-Fixes Track

These run alongside Phase 0 and don't gate the gateway.

| Issue | Fix | Module |
|---|---|---|
| #3 | Subagent default model = parent's model (don't hardcode haiku); add Anthropic credentials to all agent containers OR drop Anthropic-model defaults for non-Anthropic-routed agents | `roboco/services/provider.py`, agent container env |
| #8 | RAG indexing: skip `doc_source` build when ID is None; tighten chunk filter | `roboco/services/optimal_brain/indexes/base.py` |
| #9 | Notification poller: inject `X-Agent-ID` from session context | `roboco/api/routes/notifications.py` (or wherever the SDK polls) |
| #10 | MCP keepalive + reconnect on close in SDK shim | SDK shim (port 9000) |
| #13 | Commit links: use `ROBOCO_PUBLIC_BASE_URL` config | `roboco/services/git.py` commit-trailer builder |
| #14 | Add `make` to orchestrator Dockerfile (or drop make dependency in test runner) | `docker/orchestrator.Dockerfile`, `roboco/services/test_runner.py` |
| #18 | `git_log` endpoint: accept project slug + UUID; fix project name lookup | `roboco/api/routes/git.py` |
| #20 | A2A chat: validate `conversation_id` non-empty before URL build; auto-create conversation if missing (also handled by `dm()` in §5.2) | `roboco/services/a2a.py` |

Each is its own PR. None blocks any phase.

---

## 13. Quality Gates

All gates run on every PR via a single `make quality` target (or equivalent CI step). PR cannot merge with any gate red.

### 13.1 Tools (all already in `pyproject.toml`)

| Tool | Purpose | Pass criterion |
|---|---|---|
| `ruff format --check` | Formatting | clean |
| `ruff check` | Linting (E, W, F, I, B, C4, UP, ARG, SIM, TCH, PTH, PL, RUF) | clean |
| `mypy roboco/` | Type checking (strict — `disallow_untyped_defs`, `warn_return_any`, etc.) | clean |
| `pytest tests/ -q` | Unit + integration tests | all pass |
| `pytest --cov=roboco --cov-fail-under=80` | Coverage | ≥80% |
| `xenon --max-absolute B --max-modules A --max-average A roboco/` | Cyclomatic complexity threshold | clean |
| `radon cc roboco/ -nc -a` | Complexity audit (informational + threshold per `xenon`) | reported |
| `radon mi roboco/ -nc` | Maintainability index | every module ≥ B |
| `vulture roboco/ tests/ vulture_whitelist.py --min-confidence 100` | Dead code | clean |
| `bandit -r roboco/ -ll` | Security (low+) | clean |
| `pip-audit` | Dependency vulnerabilities | no high/critical |
| `deptry roboco/` | Dependency hygiene (DEP001–005) | clean |

### 13.2 Commit-message gate

Commit messages are part of the project's audit trail and surface in PR descriptions, changelogs, and commit-trailer links. They MUST be descriptive.

- **Subject line ≥20 chars** after the `[task-id]` prefix (rejects `wip`, `fix`, `update`).
- **Conventional-Commits shape preferred**: `<type>(<scope>): <subject>` where `type ∈ {feat, fix, chore, docs, refactor, test, perf, build, ci}`. Soft-recommended in `remediate` hint, not hard-required.
- **No banned single-word patterns**: `wip`, `tmp`, `asdf`, `oops`, `fix`, `update`, `change`, `stuff`, `things`. Gateway returns `remediate: "rewrite with: <type>(<scope>): <what changed and why>"`.
- **Body optional but recommended for non-trivial changes** (e.g., commits touching ≥3 files or ≥50 lines): the gateway hints in the response when body would help, doesn't hard-block.
- Implementation: `roboco/services/gateway/commit_validator.py` — table-driven rules; configurable via `pyproject.toml` `[tool.roboco.commits]` so the rules are in one place.
- CI gate: a pre-push check (or a server-side commit-message linter as part of `roboco_git_commit`) runs the same validation. The gateway is the primary line of defense; CI is a backstop for direct git operations.

### 13.3 Architectural gates (new)

These need to be added to enforce the gateway boundary:

- **import-linter contract** (`pyproject.toml` → `[tool.importlinter]`): the gateway layer (`roboco/services/gateway/`) may import from `roboco/services/`, `roboco/enforcement/`, `roboco/models/`, `roboco/db/`, `roboco/exceptions.py`. It may NOT import from `roboco/api/routes/` or `roboco/mcp/`. Routes/MCP may import from gateway, not the other way. (Add `import-linter` to dev deps.)
- **No circular imports**: enforced via `ruff` rule `E402` + `import-linter` reachability checks.
- **Tracing-completeness property test**: `tests/property/test_tracing_completeness.py` runs the full smoke test in a fixture, asserts that every task has `≥1 audit_log entry per state transition + journal:reflect (where required) + acceptance_criteria_status complete`. Fails the gate if any task has tracing gaps.

### 13.4 Per-PR gating script

```makefile
# Makefile target (existing Makefile extended)
.PHONY: quality
quality:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy roboco/
	uv run pytest -q --cov=roboco --cov-report=term-missing --cov-fail-under=80
	uv run xenon --max-absolute B --max-modules A --max-average A roboco/
	uv run radon mi roboco/ -nc -s
	uv run vulture roboco/ tests/ vulture_whitelist.py --min-confidence 100
	uv run bandit -r roboco/ -ll
	uv run pip-audit
	uv run deptry roboco/
	uv run lint-imports
```

CI runs `make quality` on every PR. Locally, the same target is the pre-merge gate.

### 13.5 Migration safety gate

Per project CLAUDE.md and user memory: **every schema change ships as a hand-written `alembic/versions/NNN_*.py`** file with `downgrade()` and inline seed data. Migration files for the new columns/tables (§4.3) follow the same template. The `make quality` script is extended with:

```
uv run alembic upgrade head --sql > /dev/null     # validates migrations parse
```

---

## 14. Testing Strategy

### 14.1 Unit tests (`roboco/services/gateway/`)

- Each catch-up sequence is a pure function tested with table-driven inputs: `(state-from, missing prereqs, agent role) → expected API call sequence`. Targets in `tests/unit/gateway/test_choreographer.py`.
- `claimant_lock` tested with concurrent-claim scenarios (asyncio race tests).
- `trigger_filter` tested against the smoke-test trigger patterns we observed (stale A2A, escalation cascade, scan-driven spawn during cooldown).
- `merge_chain.parent_branch_for(task)` tested with multi-level branch hierarchies.
- `tracing_gate.require()` tested per verb with each combination of missing fields.

### 14.2 Integration tests (`tests/integration/gateway/`)

- Each phase ships an integration suite using the real DB + orchestrator. Tests exercise role verbs end-to-end.
- Reuses existing FastAPI test infra (`httpx.AsyncClient` + the existing fixtures in `tests/conftest.py`).
- No Docker required for unit/integration; only smoke tests need the full stack.

### 14.3 Smoke-test reruns

- The same git-workflow smoke test we just ran (`SMOKETEST_NOTES.md` lineage) is the canonical end-to-end check.
- Rerun after each phase. New issues feed back into the parallel small-fixes track.
- After Phase 3, the smoke test must run `pending → completed` without manual intervention except CEO approval.

### 14.4 Tracing audit (property test)

- `tests/property/test_tracing_completeness.py` runs a 10-task synthetic batch through the full lifecycle, asserts:
  - Every state transition has a corresponding `audit_log` entry with non-null `agent_id`.
  - Every task ending in `completed` has: ≥1 `journal:reflect` from dev, ≥1 `journal:learning` from QA, ≥1 `journal:decision` from PM.
  - Every task with passed QA has `qa_evidence_inspected=true`.
  - Every task `acceptance_criteria_status` has `referencing_artifact_id` populated for every criterion.
- Fails CI if any task has tracing gaps. Makes the tracing contract testable as a property, not a wish.

### 14.5 Regression coverage for the 24 smoke-test issues

A `tests/regression/test_smoke_2026_05_01.py` file with one test per issue. Each test reproduces the exact call sequence that triggered the bug and asserts the gateway either prevents it or handles it correctly. New issues from future smoke tests get added here.

---

## 15. Open Questions / Risks

1. **MCP tool deletion timing.** When we delete an old MCP tool from a server, agents that had it loaded mid-session may still try to call it. Mitigation: agents are short-lived (one task = one session); spawning is the natural reset boundary. Risk: low.

2. **Subagent model resolution (#3) for board roles.** PMs/Board may genuinely need parallel research that benefits from a different model. Today's hardcoded haiku-fast was the worst possible choice; defaulting to parent's model is safe but may underperform. Decision deferred to post-MVP — flag in `provider.py` for board roles to override if needed.

3. **Unblock state-restoration edge cases.** If the snapshot was taken when task was assigned to agent X but X is no longer available (container gone, replaced), restoring assignee may fail. Mitigation: `pre_block_metadata` includes a `fallback_role` so the gateway can re-route to any agent of the same role. Tested in §14.

4. **Per-role rollout cross-talk.** During Phase 1, devs are on new flow but QA still uses old `qa_pass`/`qa_fail`. The dev's `i_am_done` calls TaskService methods that still exist in the old code path. QA reads the same task row and acts via old MCP. Bridge works because the data layer is shared. Risk: dev's new flow writes new fields (`acceptance_criteria_status`, `qa_evidence_inspected`) that QA's old tool ignores. This is acceptable — old tool simply doesn't consult the new fields, no error. When QA migrates in Phase 2, its new verbs (`pass`/`fail`) start consulting the fields. No compat shim is needed because the read paths during transition are independent (each tool reads what it knows about). The old QA tools/endpoints are deleted in Phase 2.

5. **Heartbeat infrastructure.** Single-claimant relies on heartbeat updates. If a verb call fails before reaching the gateway (network, MCP drop #10), heartbeat is not refreshed and may go stale prematurely. Mitigation: heartbeat staleness threshold (180s) is generous; on stale-claim release, the gateway emits an audit entry and the orchestrator can log it for tuning.

6. **Auto-merge safety.** `complete` auto-merging the leaf PR is a write operation that's irreversible without `git revert`. Mitigation: gateway requires `journal:decision` from PM before merge — PM has explicitly stated reasoning. PR merge is logged in `audit_log` with PM as actor. CEO approval still gates master merges.

7. **Tracing-gap UX.** If verbs reject because of missing tracing, agents may loop trying to satisfy gates. Mitigation: every gate failure includes precise `remediate` hint with the exact next call. Smoke-test the gate-failure responses with multiple models (minimax, claude) to ensure the hints are interpretable.

---

## 16. Out of Scope / Tracked Separately

- **CEO UI** for approve/reject is a frontend task; tracked separately.
- **Auditor read-only verbs** are part of Phase 4 but specs may extend (Auditor needs broader-than-team visibility).
- **Multi-project workspace handling**: today's codebase is single-project (roboco itself). Multi-project will reuse the gateway pattern but isn't part of this spec.
- **Anthropic vs. minimax provider parity** for subagents (#3) is a parallel-track infra fix; the gateway just defaults to parent's model.

---

## Appendix A — Verb Implementation Skeleton

Sample for `i_am_done`, showing how the gateway composes existing services:

```python
# roboco/services/gateway/choreographer.py
from roboco.services.task import TaskService
from roboco.services.work_session import WorkSessionService
from roboco.services.git import GitService
from roboco.services.a2a import A2AService
from roboco.services.gateway.tracing_gate import TracingGate
from roboco.services.gateway.evidence_builder import EvidenceBuilder
from roboco.services.gateway.merge_chain import MergeChain

class Choreographer:
    def __init__(self, task_svc, ws_svc, git_svc, a2a_svc, tracing, evidence, merge):
        self.task = task_svc
        self.ws = ws_svc
        self.git = git_svc
        self.a2a = a2a_svc
        self.tracing = tracing
        self.evidence = evidence
        self.merge = merge

    async def dev_i_am_done(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        task = await self.task.get(task_id)
        require_assignee(agent_id, task)

        gate = await self.tracing.check(
            task,
            requirements=['progress>=1', 'journal:reflect', 'acceptance_criteria_status'],
        )
        if not gate.passed:
            return Envelope.tracing_gap(gate, evidence=await self.evidence.build(task))

        if not task.self_verified:
            await self.task.submit_verification(agent_id, task_id, notes)

        if await self.ws.has_unpushed_commits(task.work_session_id):
            await self.git.push(task.branch_name)

        if task.pr_number is None:
            parent_branch = self.merge.parent_branch_for(task)
            await self.git.create_pr(task.branch_name, parent=parent_branch, is_root_pr=False)
            task = await self.task.get(task_id)

        await self.task.submit_qa(agent_id, task_id, notes)

        qa_agent = await self.task.qa_agent_for(task.team)
        skill = self._resolve_skill(qa_agent, ['code_review', 'qa_review'])
        await self.a2a.send(
            from_agent=agent_id, to_agent=qa_agent.id,
            skill=skill, task_id=task_id,
            body=f'Ready for review. PR: {task.pr_url}',
        )

        return Envelope.ok(
            status=task.status,
            task_id=task_id,
            evidence=await self.evidence.build(task),
            next='idle until QA',
            context_briefing=await self.evidence.briefing_for(agent_id),
        )
```

The choreographer never writes to the DB directly; it composes service calls. Each service handles its own validation and transactional boundary. The gateway adds the cross-service orchestration and the standardized response envelope.

---

## Appendix B — Smoke-Test Issue → Fix Mapping

| Issue | Fixed by | Section |
|---|---|---|
| #1 ToolSearch interpreted as bash | Tools pre-loaded; ToolSearch removed | §9 |
| #2 Skill registry mismatch | Skill alignment migration in Phase 0 + `dm()` auto-resolution | §4.3, §5.2 |
| #3 Subagent default model | Spawn manifest carries parent's model | §9.1 |
| #4 Agents skip ToolSearch | No ToolSearch to skip | §9 |
| #5 task_create defaults to pending | New code path: `i_will_work_on` doesn't need separate activate; old `task_create`/`activate` removed | §5.3, §11 |
| #6 message_send requires task_id | `say()` makes `task_id` optional, auto-injects when active task | §5.2 |
| #7 Journal tools missing fields | `note()` accepts loose shapes, derives title from first line, accepts string options | §5.2 |
| #8 RAG `journals/None` doc_source | Parallel small fix | §12 |
| #9 Missing X-Agent-ID header | Parallel small fix | §12 |
| #10 MCP -32000 connection closed | SDK shim keepalive + reconnect | §12 |
| #11 Audit NULL agent_id | Gateway is single audit writer with full actor context | §6.2, §8.4 |
| #12 Inconsistent claim/spawn ordering | Spawn flow goes through gateway before any DB write | §8.2 |
| #13 Commit links to 127.0.0.1 | `ROBOCO_PUBLIC_BASE_URL` config | §12 |
| #14 Missing `make` in orchestrator | Dockerfile fix or drop dependency | §12 |
| #15 QA false-fails "no PR" | `claim_review` response includes `evidence.pr_url` inline | §5.4, §6.3 |
| #16 Lifecycle conflates dev/QA in_progress | Agents never call `task_start` directly; gateway picks transition | §7.3 |
| #17 Stale A2A respawning QA | Trigger filter drops stale A2A | §8.2 |
| #18 git_log project lookup | Parallel small fix | §12 |
| #19 No path from needs_revision | `i_will_work_on` accepts `needs_revision` | §7.1 |
| #20 A2A URL builder empty conversation_id | `dm()` auto-creates conversation; parallel small fix in `a2a.py` | §5.2, §12 |
| #21 PM/Doc workspaces miss dev branch | `evidence()` fetches dev branch on demand | §5.2 |
| #22 PR merge gap (deadlock) | `complete` always pairs with role-scoped merge | §7.1, §7.2 |
| #23 No path back to awaiting_pm_review | `unblock` restores `pre_block_state` | §8.3 |
| #24 Multi-agent thundering herd | Single-claimant + cooldown + role rate limit | §8.1, §8.2 |
