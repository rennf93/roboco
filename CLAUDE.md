# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Licensing

RoboCo is licensed under **AGPL-3.0** (see `LICENSE`). Copyright (c) 2026 Renzo Franceschini. Do NOT reintroduce an MIT or other license reference anywhere (README, headers, package metadata) — the project is AGPL.

Contributions require a signed **Contributor License Agreement** (`CLA.md`), automated via the CLA Assistant workflow (`.github/workflows/cla.yml`). The CLA preserves the option to dual-license / offer a commercial edition later; keep copyright assignment language intact. See `CONTRIBUTING.md`.

## Project Overview

**RoboCo** is an AI Agentic Company - a virtual organization of 25 AI agents + 1 human CEO, designed to operate as a complete software development workforce. The system implements a structured organizational hierarchy with formal communication protocols, task management, and quality controls.

### Core Architecture

```
CEO (Renzo - Human)
    |
    +-- Intake (on-demand interviewer: chats only with the CEO to draft a task)
    +-- Secretary (on-demand chief-of-staff: reads company state, runs gated CEO directives)
    +-- PR Reviewer (read-only: the main reviewer — inbound external/fork + internal PRs, and the root→master in-path gate)
    |
    +-- Board (3 agents)
         +-- Product Owner
         +-- Head of Marketing
         +-- Auditor (silent observer, reports to CEO)
              |
              +-- Main PM (coordinates all cells)
                   |
                   +-- Backend Cell (6 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer)
                   +-- Frontend Cell (6 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer)
                   +-- UX/UI Cell (6 agents: 2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer)
```

### Hardware Infrastructure

- **Olares One (Powerhouse)**: Intel Ultra 9 + RTX 5090, runs Claude Code instances and AI inference - NOT YET ARRIVED
- **UGREEN NAS (Warehouse)**: 36TB RAID6, 128GB RAM, hosts PostgreSQL, Redis
- **Pi Cluster (Operations)**: Monitoring, notifications, smart home

## Development Standards

### Python (Backend)
```bash
# Package manager
uv

# Before any commit
uv run ruff format .
uv run ruff check .
uv run mypy roboco/
uv run pytest

# Coverage target: 80%
```

### TypeScript (Frontend)
```bash
# Package manager
pnpm

# Before any commit
pnpm format
pnpm lint
pnpm typecheck
pnpm test

# Coverage target: 80%
```

## Technology Stack

| Layer | Technology |
|-------|------------|
| API Framework | FastAPI |
| Database | PostgreSQL + asyncpg |
| Vector Store | PostgreSQL + pgvector (in-house engine) |
| RAG Engine | in-house (asyncpg + pgvector, hybrid retrieval) |
| Cache/Queue | Redis |
| Container Runtime | Docker + Docker Compose |
| Cloud LLM | Claude API (claude-opus-4-6) + xAI Grok (official `grok` CLI, SuperGrok subscription) |
| Local LLM | Ollama (glm-5.2:cloud for RAG/hybrid retrieval) |
| Embeddings | qwen3-embedding:0.6b (1024 dim) |
| Frontend | Next.js 16 + TypeScript + Tailwind + Radix UI (in `panel/`) |
| Edge / Proxy | nginx (single entry point on port 3000) |

## Multi-Agent Workspace Structure

Each agent gets their own git clone of a project, enabling parallel development without conflicts:

```
{ROBOCO_WORKSPACES_ROOT}/          # Default: /data/workspaces
+-- {project-slug}/
    +-- {team}/
        +-- {agent-slug}/
            +-- [git repository]
```

**Example:**
```
/data/workspaces/
+-- roboco/
    +-- backend/
    |   +-- be-dev-1/     # be-dev-1's workspace
    |   +-- be-dev-2/     # be-dev-2's workspace
    +-- frontend/
        +-- fe-dev-1/
        +-- fe-dev-2/
```

Note: the Next.js control panel now lives at `roboco/panel/` inside this repo (no longer a separate `roboco-panel` project or workspace).

**Key Configuration (roboco/config.py):**
- `ROBOCO_WORKSPACES_ROOT`: Root directory for workspaces (default: `/data/workspaces`)
- `ROBOCO_WORKSPACE_AUTO_CLONE`: Auto-clone repos on first access (default: `true`)
- `ROBOCO_WORKSPACE_CLONE_TIMEOUT`: Clone timeout in seconds (default: `300`)

On a Python workspace, `WorkspaceService` runs `uv sync --extra dev` (not plain `uv sync`) so the clone's `.venv` carries the full gate toolchain (ruff/mypy/xenon/pytest) — the lint/type/complexity tools live in the `dev` **extra**, which plain `uv sync` skips. Without it an agent's `make quality` fails on `ruff: command not found` and the agent can't gate its own work.

Because the clone is shared across a dev's tasks, a **fresh claim** git-resets the workspace to a clean tree (`git reset --hard`) before checking out the new task's branch — discarding abandoned uncommitted cruft from a finished task while preserving all commits and the gitignored `.venv`. A resume short-circuits before this, so committed work is never reset.

Terminal completion and cancellation also force-delete the task's local branch ref and its `.previews/` video-render dir in the assignee's clone (alongside the existing worktree removal), skipping any branch that coincides with an environment-ladder rung; a PM/CEO can additionally sweep older backlog branches project-wide via `POST /git/branches/cleanup` or the Git page's "Clean Up Stale Branches" button.

## Git Workflow

### Branch Naming Convention

Branch names follow the pattern: `{type}/{team}/{task-hierarchy}`

**Types:** `feature`, `bug`, `chore`, `docs`, `hotfix`

**Task Hierarchy:** Uses `--` separator (not `/`) to avoid git ref conflicts.

**Examples:**
- Root task: `feature/backend/ABC12345`
- Subtask: `feature/backend/ABC12345--DEF67890`
- Sub-subtask: `feature/backend/ABC12345--DEF67890--GHI11111`

### Commit Format

Commits are automatically prefixed with the task ID:

```
[{task-id[:8]}] {message}
```

**Example:**
```
[ABC12345] Add user authentication endpoint
```

### Work Sessions

When a developer claims a task, a **WorkSession** is created that tracks:
- Branch name and base/target branches
- All commits made during the session
- Files modified
- PR number/URL when created
- Merge status and who merged

A task has at most **one active WorkSession**: re-claiming a task (pool release, reaper unclaim, escalation redirect) supersedes any prior agent's stale active session, enforced both at the service layer and by a DB partial-unique index (migration 047). Without it, duplicate active sessions made the one-row active lookup raise and crashed the claim/plan flow into a respawn loop.

A developer's clone is shared across all their tasks, so push and PR-head operate on the task's **recorded branch by name**, independent of the clone's current checkout — fixing the `BRANCH_MISMATCH` / "No commits between" failures when the clone was parked on a later task's branch. A missing local task-branch ref is first recovered from `origin/<branch>` before the push-by-name.

### Git Credentials

Git authentication is managed **per-project** through encrypted GitHub PATs:

- **Each project stores its own git token** - no global fallback
- **Tokens are encrypted at rest** using Fernet symmetric encryption
- **API never exposes tokens** - only returns `has_git_token: boolean`
- **Self-service via UI** - users set/update tokens in project settings

**Project fields:**
| Field | Description |
|-------|-------------|
| `git_token_encrypted` | Fernet-encrypted GitHub PAT (DB column) |
| `has_git_token` | Boolean indicator for API responses |

**Token flow:**
1. User creates project in UI, enters GitHub PAT
2. Token encrypted and stored in `projects.git_token_encrypted`
3. WorkspaceService decrypts token when cloning repos
4. GitService decrypts token for PR operations (gh CLI)

**HTTPS URLs require tokens** - attempting to clone without a token will raise `WorkspaceError`.

### Forge providers (GitHub + Gitea + GitLab)

The REST surface (PRs, CI status, reviews, labels, releases) is provider-routed (`roboco/services/forge/`): `GitProvider` is the ~20-method transport contract, `GitHubProvider`, `GiteaProvider`, and `GitLabProvider` implement it, and `GitService._forge` returns a `ForgeRouter` that picks the transport per call from `RepoRef.host` — `None` (github.com/GHE) rides GitHub, a registered Gitea/GitLab host rides that instance's provider, so `GitService`'s call sites never know which forge they're on. A project opts in via `projects.git_provider` (gitlab.com auto-detects like github.com; self-hosted instances set it explicitly; `"github"` doubles as the GHE escape hatch with `ROBOCO_GITHUB_API_BASE_URL`) — panel: the Forge select in the edit-project dialog. The host→provider(+scheme — plain-http LAN instances are supported) map is in-memory per process, self-healing: `ProjectService.get`/`get_by_slug` re-register on every read. Both non-GitHub providers adapt their wire contracts back into the GitHub shapes `GitService` classifies (`forge/shaping.py` `ShapedResponse`): Gitea — `token` auth scheme, duplicate-PR 409→422, commit statuses reshaped into `check_runs`/`workflow_runs`, `Do`-keyed POST merge, slash-encoded refs; GitLab — MR iid→`number`, source/target_branch→`head`/`base`, per-file diffs reassembled into unified-diff text, approve-vs-note review routing (no request-changes verb exists), pipelines/statuses CI reshapes, reviewer-request skipped (needs numeric ids). Neither has GitHub's server-side merges API: their `merge_branch` returns a shaped 501 and `GitService.sync_env_branch` runs the shared local-git fallback (`_local_merge_branch`: throwaway clone → merge → push; conflict aborts with the remote untouched, same status vocabulary). Plain git (clone/fetch/push) is forge-agnostic — the Basic-auth `x-access-token:<token>` extraheader works on Gitea/GitLab unchanged (verified live on Gitea). The env-gated `tests/e2e_smoke/test_gitea_live.py` is the live contract suite (self-seeding against a dockerized `gitea/gitea`; it caught the slash-encoding and http-scheme gaps).

## Task Lifecycle

### Task States

The complete task lifecycle is defined in `roboco/foundation/policy/lifecycle.py` (`roboco/enforcement/task_lifecycle.py` is a backwards-compat shim over it):

```
backlog -> pending -> claimed -> in_progress -> [blocked|paused] -> verifying
                                     |                                  |
                                     v                                  v
                                 awaiting_qa <------------------+   awaiting_documentation
                                     |         (needs_revision) |           |
                                     v                          |           v
                                 awaiting_documentation --------+   awaiting_pm_review
                                     |                                      |
                                     v                                      v
                                 awaiting_pm_review             awaiting_ceo_approval
                                     |                                      |
                                     v                                      v
                                 completed                              completed
```

**In-path PR-review gate** (`awaiting_pr_review`): each assembled PR is reviewed before the PM merges. The cell PM's `submit_up` opens the cell→root PR and the Main PM's `submit_root` opens the root→master PR; both enter `awaiting_pr_review`, where a reviewer `pr_pass`es it on to `awaiting_pm_review` or `pr_fail`s it back to `needs_revision` — the merge-level reject the PM otherwise lacks. Leaf dev tasks and branchless coordination roots skip the gate. `pr_pass` additionally refuses while the assembled PR's own CI (its head commit's checks, `GitService.get_pr_ci_status`) is failing, pending, or unresolvable — a repo with no CI configured passes through with an evidence note; `pr_fail` stays available regardless so a reviewer is never stuck waiting on CI. The reviewer prompt requires a per-AC `file:line` walk (a silently dropped deliverable is an automatic fail) and the gate's diff/conventions base resolves the task's REAL parent branch (`resolve_parent_branch`, the parent task's own `branch_name`) instead of deriving it from the branch-name string, so a cross-team hop (e.g. cell→root) no longer attributes inherited base-branch content to the task under review.

**Sequence is the bar.** A task with a parent and effective sequence N (`COALESCE(sequence, 0)`) cannot be claimed while any same-parent sibling with a strictly lower effective sequence is non-terminal — assignee-blind, independent of and stricter than `dependency_ids`, enforced in `TaskService._validate_claim_preconditions` (the `claim` chokepoint itself) so every claim path crosses it. Ties run parallel; cancelled siblings never block; sequence `0` and parentless tasks are unaffected. Delegation stamps sequence from the collision DAG (`stamp_wave_sequence`: `1 + max` same-parent dependency sequence, or `0` when independent) instead of a raw per-sibling ordinal, so fully independent siblings tie and run in parallel while colliding/ordered work ascends — PM-authored sequences are never rewritten. `tasks.parent_task_id` is indexed (migration 069) since the guard's sibling probe runs on every claim; the dispatcher pre-filters dependency/sequence-held tasks (`TaskService.is_pending_claim_blocked`) before attempting a doomed claim.

**PM-turn elimination (auto-submit).** When every child of an assembled, PR-bearing parent goes terminal, the orchestrator's closure dispatcher (`_maybe_spawn_pm_closure` → `_closure_handled_without_pm` → `_try_auto_submit`) runs the real `submit_up`/`submit_root` gate system-side as the owning PM instead of spawning the PM for that turn — same verb, same guards (ownership, notes, journal:decision, subtasks-terminal, parent-AC coverage, branch), authorized via the internal API with the PM's own identity headers. This is unconditional — the turn cut IS the flow, no kill-switch. Success lands the task on `awaiting_pr_review` with an audited `task.auto_submitted` row and no PM spawn; ANY refusal (branchless/umbrella parent, a gate rejection — freshness, AC coverage, a subtask-terminal race — or a transport error) falls back to spawning the PM exactly as before — that fallback is the sole safety net — with the refusal reason threaded into the PM's closure prompt so it isn't rediscovering it blind.

**Revision findings ledger (always-on, no flag — core lifecycle).** QA/PR-gate/PM/CEO bounce feedback used to be prose-only: `issues: list[str]` flattened into free text with no structural anchor, `request_changes`/`ceo_reject` had no structured note at all, and the raw `dev_notes` append both used was silently overwritten by the very next `note(scope='handoff')` call — a live data-loss bug. `fail_review` (QA), `pr_fail` (in-path PR gate), `request_changes` (PM merge reject), and `ceo_reject` all now take structured `findings: list[dict]` — validated into `Finding` (`file` repo-relative ≤300 chars/no `..`, `line` ≥1, `severity` blocker\|major\|minor\|nit, `criterion` must match an AC id or its exact text, `expected`/`actual` ≤300, `fix` ≤500, `evidence` ≤2000) — with a soft nudge above 5 findings and a hard reject above 10 in one call (`roboco/services/gateway/choreographer/findings.py`). `issues=[...]` still works this release as a shim (each string → a file-less `severity=major` finding, deprecation-logged) and merges with `findings` rather than one silently dropping the other. Every producer inserts one append-only row per finding into `task_review_findings` (migration 071; `origin` qa\|pr_gate\|pm\|ceo, `round` = `revision_count+1` read pre-transition, `status` open→addressed→verified\|waived) via `ReviewFindingsRepository`, then writes a structured note whose `summary` IS the deterministic per-finding rendering `[F-id8] file:line (severity) — expected → actual → fix`, mirrored into `qa_notes`/`pr_reviewer_notes`/the new `pm_notes` column (new `PmReviewContent` "pm_review" content type). `ceo_reject` now validates its reason (previously could 500 on an empty/trivial one) and stamps it as one `origin=ceo` `blocker` finding; on a branchless coordination root — which routes to `pending` via `admin_set_status`, bypassing the normal audit chokepoint — it bumps `revision_count` and emits `task.ceo_reject` directly instead of silently skipping both. New audit events `task.request_changes`/`task.ceo_reject` join `task.qa_fail`/`task.pr_fail` in `_audit_events_for` so rework metrics attribute every bounce kind, not just QA/PR-gate.

Resolution: `i_am_done`/`submit_up`/`submit_root` all gain `resolved_findings` (`{finding_id, commit?, note?}`), gated by a new `Requirement.FINDINGS_ADDRESSED` — every OPEN finding on the task must be named (a fuzzy 8-char-prefix match against `[F-id8]`) or the envelope rejects, listing the still-open ids. `pass_review`/`pr_pass`/`complete` bulk-verify their own origin's `addressed` findings same-transaction (a stamp failure fails the verb outright, not best-effort); `ceo_approve` stamps `ceo`-origin best-effort. `mark_waived` is wired to the auditor-only `waive_finding` flow verb (severity-scoped: blocker/major must be fixed, never waived; only minor/nit open findings are waivable, with a required note and a `task.finding_waived` audit event; no task status change).

Delivery: `evidence()`/`build_task_handoff` carry `revision_findings` (open only, capped) so a bounced dev finally gets what `developer.md` promises instead of nothing; `claim_review`/`claim_gate_review` additionally carry `prior_findings` (the full ledger) so a round-2+ reviewer checks prior findings instead of re-deriving them blind. The orchestrator's `REVISION_REQUIRED` dev prompt and the PM triage "bounced" block render open findings inline with the same rendering; A2A fail bodies share it. `GET /api/tasks/{id}/findings` (capped 500, SQL-aggregated per-origin/status summary + `total`/`truncated`) backs the panel's task-detail Findings tab and a `bounced xN` header chip (`revision_count`); metrics attribute `pm_rejects`/`ceo_rejects` + open/total findings counts per task; vault task notes render a capped `## Findings` section (fail-open fetch, never blocks the note write).

**States:**
| State | Description |
|-------|-------------|
| `backlog` | PM setup phase - dependencies or session setup needed |
| `pending` | Ready for work - orchestrator can spawn agents |
| `claimed` | Agent has locked the task |
| `in_progress` | Active development |
| `blocked` | External dependency blocking progress |
| `paused` | Temporarily stopped (can resume) |
| `verifying` | Self-verification by developer |
| `needs_revision` | QA or CEO requested changes |
| `awaiting_qa` | Submitted for QA review — PR must already exist |
| `awaiting_documentation` | Documentation phase — PR already open from pre-QA; doc writes docs |
| `awaiting_pr_review` | In-path PR-review gate: a reviewer checks the assembled cell→root / root→master PR before the PM merges (assembled, PR-bearing tasks only) |
| `awaiting_pm_review` | Docs complete, PM reviews + merges |
| `awaiting_ceo_approval` | Major tasks escalated for CEO final approval |
| `completed` | Terminal state - work done and merged |
| `cancelled` | Terminal state - work cancelled |

### Role-Based Transitions

All status transitions are validated through the enforcement layer. Key restrictions:

| Transition | Allowed Roles |
|------------|---------------|
| `backlog` → `pending` (activate) | PM roles only |
| `pending` → `claimed` (claim) | Role must match task type (QA for awaiting_qa, etc.) |
| `claimed` → `pending` (unclaim) | Assignee or PM |
| `awaiting_qa` → `awaiting_documentation` (pass) | QA only |
| `awaiting_qa` → `needs_revision` (fail) | QA only |
| `awaiting_documentation` → `awaiting_pm_review` | Documenter or Developer (parallel completion) |
| `in_progress` → `awaiting_pr_review` (submit_up / submit_root) | PM roles (opens the assembled cell→root / root→master PR) — or the orchestrator running the same verb system-side as the owning PM once all children are terminal |
| `awaiting_pr_review` → `awaiting_pm_review` (pr_pass) | PR reviewer only |
| `awaiting_pr_review` → `needs_revision` (pr_fail) | PR reviewer only |
| `awaiting_pm_review` → `completed` | PM roles only |
| `awaiting_pm_review` → `needs_revision` (request_changes) | PM roles only — the merge-level reject with structured findings (see "Revision findings ledger" above) |
| `awaiting_pm_review` → `awaiting_ceo_approval` | PM roles only |
| `awaiting_ceo_approval` → `completed/needs_revision/cancelled` | CEO only |
| Any → `cancelled` | PM roles only |

**Unclaim Operation**: Agents can release claimed tasks back to the pool using `unclaim()`. This transitions `claimed` → `pending` and optionally reassigns to another agent.

**Board never owns a coordination root**: a Board role (Product Owner / Head of Marketing) is never assigned a Main-PM coordination root (delivery root or MegaTask root-subtask) via escalation or reassignment — Board roles have no `unblock` verb, so such a hand-off would deadlock. The transition is diverted to the pool for a role-matched Main-PM reclaim.

### Git Integration Requirements

All tasks follow git workflow. PR is created BEFORE QA review (not after) so QA can review the real PR diff on GitHub and downstream PM/CEO approval chain off a PR that already exists:

1. **claimed -> in_progress**: `branch_name` is auto-set on claim (hierarchical branches)
2. **verifying -> awaiting_qa** (submit-qa): Requires `self_verified`, `commits`, `pr_number` (PR open), and at least one `progress_updates` entry
3. **awaiting_qa -> awaiting_documentation** (pass-qa): Requires `pr_number` and substantive QA notes
4. **awaiting_documentation -> awaiting_pm_review**: Requires `docs_complete=True` (PR already exists from step 2 above)
5. **awaiting_pm_review -> awaiting_ceo_approval**: Must have `pr_number` set and all subtasks in a terminal state

### CEO Approval Workflow

Major tasks are escalated to CEO for final approval:
1. PM reviews and approves, escalates to `awaiting_ceo_approval`
2. CEO can:
   - **Approve**: Merges PR, task -> `completed`
   - **Request changes**: Task -> `needs_revision`
   - **Cancel**: Task -> `cancelled`

## Data Models

### Core Models (roboco/models/)

| Model | Purpose |
|-------|---------|
| `Task` | Atomic unit of work with acceptance criteria |
| `Project` | Git repository configuration and CI/CD commands |
| `WorkSession` | Links agent work to task, tracks branch/commits/PR |
| `Agent` | AI agent with role, team, capabilities |
| `Notification` | Formal notification requiring acknowledgment |
| `Journal` | Agent personal log for reflections/learnings |

### Task Model Key Fields

```python
# Git configuration (all tasks follow git workflow)
task_type: TaskType      # code, documentation, research, planning, design, administrative
project_id: UUID         # Project this task works on (required)
branch_name: str         # Branch for this task (auto-created on claim)
work_session_id: UUID    # Active work session

# PR tracking (parallel execution in awaiting_documentation)
pr_number: int           # GitHub/GitLab PR number
pr_url: str              # Full URL to PR
docs_complete: bool      # Documenter has finished
pr_created: bool         # Developer has created PR

# Commits linked to task
commits: list[CommitRef] # All commits made for this task
```

## Communication Model

Agents coordinate via **task state + task detail fields**, not a channel/session backbone. Two comms primitives sit alongside that: **A2A** (`dm` + `read_a2a`, direct peer-to-peer, same-cell only — see `docs/rag/tools/a2a-tools.md`) for informal contact, and **Notifications** (`notify`, ack-required, sent by PMs/Board only) for formal signals. The CEO is the one asymmetric participant: from the panel it can open a direct 1:1 A2A conversation with any DM-capable agent at any time, but an agent can never initiate to the CEO — only reply in-thread once the CEO has opened one. A CEO-authored DM wakes an offline recipient via the `a2a_request` notification dispatch path, a wake same-cell `dm` never triggers.

Agent learnings (`note` scope='learning') broadcast as knowledge-share notifications only to other **agents** — the human / human-driven roles (CEO, prompter, secretary) are excluded, since agent knowledge-sharing is noise in a human's inbox.

## Key Principles

1. **Everything is a task** - All work is tracked and documented
2. **No work without a task** - Create task record first
3. **No task without acceptance criteria** - How do we know it's done?
4. **No closure without documentation** - Future agents need context
5. **Communication is constant** - Stream reasoning, log everything
6. **State is sacred** - If interrupted, state must be recoverable
7. **The Auditor sees all** - Quality monitored silently
8. **Commits linked to tasks** - Every commit references its task ID
9. **CEO approves major changes** - Escalation path for important work

## Agent Gateway

Agents do not call the API or per-domain MCP tools directly. They go through two thin MCP servers (`roboco-flow`, `roboco-do`) backed by the server-side **Choreographer** in `roboco/services/gateway/`. The Choreographer composes the existing services (TaskService, JournalService, GitService, etc.) into intent-verb sequences. Tracing, claim-locking, evidence assembly, and remediation hints are all centralized there.

Each agent gets a **spawn manifest** at `/app/tool-manifest.json` listing the verbs its role is allowed to call. The orchestrator builds the manifest from `roboco/services/gateway/role_config.py` and mounts it read-only into the agent container.

### Verb surface (canonical source: `lifecycle.intents_for_role`; every role also gets `i_am_idle`)

| Role          | Flow verbs (beyond `i_am_idle`)                                                                  |
|---------------|--------------------------------------------------------------------------------------------------|
| developer     | `give_me_work`, `i_will_work_on`, `open_pr`, `i_am_done`, `i_am_blocked`, `resume`, `sync_branch`, `unclaim`     |
| qa            | `give_me_work`, `claim_review`, `pass_review`, `fail_review`, `i_am_blocked`, `resume`, `unclaim` |
| documenter    | `give_me_work`, `claim_doc_task`, `i_documented`, `i_am_blocked`, `resume`, `unclaim`             |
| cell_pm       | `give_me_work`, `i_will_plan`, `delegate`, `complete`, `request_changes`, `submit_up`, `triage`, `unblock`, `escalate_up`, `reassign`, `resume`, `unclaim` |
| main_pm       | `give_me_work`, `i_will_plan`, `delegate`, `complete`, `request_changes`, `submit_root`, `triage`, `triage_all`, `unblock`, `escalate_up`, `escalate_to_ceo`, `resume`, `unclaim` |
| pr_reviewer   | `give_me_work`, `claim_pr_review`, `post_pr_review` (inbound external/fork PRs), `claim_gate_review`, `pr_pass`, `pr_fail` (in-path assembled-PR gate), `unclaim` |
| product_owner | `triage`, `escalate_to_ceo`                                                                      |
| head_marketing| `triage`, `escalate_to_ceo`                                                                      |
| auditor       | `triage`, `waive_finding` (read-only; carries `dm`/`read_a2a` as a content tool so it can reply to a CEO-opened DM, but never initiates) |
| prompter      | (none beyond `i_am_idle` — not a delivery-lifecycle role; intake interviewer, human-only)        |
| secretary     | (none beyond `i_am_idle` — human-only chief-of-staff; reads company state + runs gated CEO directives) |

Content tools (do_server) — most roles: `commit`, `note`, `dm`, `read_a2a`, `evidence`. Delivery roles (developer / qa / documenter / cell_pm / main_pm) also get `draft_playbook` (draft a curated playbook for the KB). Product Owner additionally gets `propose_roadmap` (product_owner-only, authors the weekly board-roadmap cycle) and Head of Marketing additionally gets `propose_feature_spotlight` (head_marketing-only, drafts a feature-spotlight X post) — see "Board roadmap engine" / "RoboCo X account" below. Auditor is restricted to `note` (scope=reflect) + `evidence` + `dm`/`read_a2a`, plus the playbook-curation verbs `approve_playbook` / `reject_playbook` / `archive_playbook` (a bounded, deliberate expansion — KB curation, not agent comms) and, when the Obsidian vault is armed, `curate_vault` (writes one narrative paragraph onto a just-completed root's vault note — see "Obsidian vault V1+V2" below). The auditor's `dm`/`read_a2a` exists so it can read and reply in-thread when the CEO opens a DM with it (mid-task, stuck) — it still never *initiates* peer A2A (`agents_config.can_a2a_direct` refuses it unconditionally as sender), preserving it as a silent observer to other agents. The `pr_reviewer` likewise now carries `dm`/`read_a2a` for the same CEO-reachability reason, on top of posting its change-request on the PR itself; its only INITIATION target stays its owning cell_pm/main_pm. The `prompter` (intake) and `secretary` are restricted to `note` + `evidence` — human-only, no `dm`/`notify`, they have their own dedicated chat pages instead. The `note`/journal write returns as soon as the entry is persisted; RAG indexing (Ollama embedding) runs fire-and-forget, so the tool no longer times out under concurrent load.

### MCP servers running per agent container

| Server               | Purpose                                                              |
|----------------------|----------------------------------------------------------------------|
| `roboco-flow`        | Intent verbs (give_me_work, i_am_done, claim_review, complete, ...) |
| `roboco-do`          | Content tools (commit, note, dm, read_a2a, evidence)                  |
| `roboco-git-readonly`| Read-only git: status, log, diff, branches                           |
| `roboco-optimal`     | RAG: `roboco_ask_mentor`, `roboco_kb_search`                         |
| `roboco-docs`        | Project docs file management (selected roles)                        |
| `playwright`         | Structured browser tools (navigate/snapshot/evaluate/screenshot) — `fe-qa`/`ux-qa` only, role-gated not image-gated; the wrapper entrypoint points it at the image's own baked `chromium-headless-shell` |

Every verb returns a standardized **Envelope**:
- ok: `{status, task_id, next, evidence?, context_briefing}`
- error: `{error, message, remediate, missing}`

The `next` field tells the agent what to call next; the `remediate` field on errors tells them exactly how to fix and retry. Agents should not guess state — trust the response. The verb runner re-checks the task after each composed atomic action and, on a concurrent mid-verb state change, fails fast with a clean `INVALID_STATE` (re-fetch + re-issue) rather than crashing on a `None` dereference.

## Agent Providers

Agent backends are pluggable. `roboco/llm/providers/` defines an `AgentProvider` lifecycle ABC (`base.py`) and a `ProviderRegistry` keyed by `ModelProvider` (`registry.py`), with `ClaudeCodeProvider` (default), `GrokCliProvider`, and `GeminiCliProvider`. The orchestrator resolves a provider at spawn from the agent's `ModelProvider`; when no dedicated provider is registered it falls back to the built-in Claude Code spawn. `ModelProvider` (`roboco/models/base.py`) is `ANTHROPIC` (default), `GROK`, `GEMINI`, `LOCAL`, `OLLAMA_CLOUD`, `OPENAI` (reserved). The seam is additive: only `GROK`/`GEMINI` route through their dedicated providers; Anthropic / Ollama Cloud / self-hosted spawns are unchanged, and every provider gets the same MCP gateway + tool-manifest wiring by construction.

**Grok runtime.** `GROK` agents run xAI's official `grok` CLI (model `grok-build`) authenticated by a **SuperGrok subscription**, not a metered API key — so a Grok workforce can't stall mid-task on out-of-credits. The host `~/.grok/auth.json` is mounted **read-only** into each agent (`GrokCliProvider._append_grok_auth_mount`; `ROBOCO_HOST_GROK_DIR` is the host mount source, set up once with `grok login`). It reaches parity with the Claude path by construction: same MCP gateway + manifest, per-role tool-removal and git-operation deny rules, a prompt-injection guard on the task prompt, headless tool auto-approval, and per-agent token/cost capture from the grok session store. It covers both one-shot delivery roles and the interactive Intake (Prompter) and Secretary chats (per-turn `grok -p` with session resume).

**Token auto-refresh.** The grok access token has a fixed ~6h server-set TTL and the CLI cannot refresh it headlessly — on an expired token it hangs forever at an interactive login prompt. The orchestrator mints a fresh token from the offline-access refresh token (xAI's OIDC `refresh_token` grant) before expiry and rewrites the shared `auth.json` in place (`roboco/llm/providers/grok_auth.py` `refresh_if_stale`, run once per dispatch tick; the orchestrator's `~/.grok` mount is read-write so it can rewrite it). As a backstop the agent entrypoint runs `python -m roboco.llm.providers.grok_auth --check` and refuses to start (exit 78) on a missing/expired token instead of hanging.

**Gemini runtime (V1: one-shot delivery roles only, no interactive Intake/Secretary).** `GEMINI` agents run Google's official `gemini` CLI (GA ids `gemini-2.5-pro`/`-flash`/`-flash-lite`, pinned via `ROBOCO_GEMINI_CLI_MODEL`) authenticated by an **OAuth login**, not a metered key. The host `~/.gemini` (from a one-time interactive `gemini` login, `ROBOCO_HOST_GEMINI_DIR`) is mounted **read-only** at a staging path; the entrypoint COPIES it into a container-local, writable `~/.gemini` so the CLI's own in-process token refresh (google-auth-library) can write back locally without ever touching the host copy. Unlike grok's single-use refresh token (which needs one orchestrator-side writer serializing every refresh, `grok_auth.py`), Google's refresh token is reusable, so each container refreshing its own copy independently is safe with **no orchestrator refresh daemon** — `roboco/llm/providers/gemini.py`'s module docstring spells out the contrast. Tool scoping has no CLI-flag equivalent to grok's `--disallowed-tools`/`--deny`: it's expressed entirely through a rendered TOML Policy Engine (`~/.gemini/policies/roboco.toml`, deny-only rules keyed by `toolName`/`commandPrefix`) plus `settings.json` (`security.auth.selectedType` for headless OAuth, `experimental.enableAgents=false` for the fleet-wide subagent ban, `advanced.autoConfigureMemory=false`), all rendered by `roboco/llm/providers/gemini_cli_config.py`; `--approval-mode yolo` is universal (headless auto-approval). Usage/cost capture (`gemini_cli_usage.py`) reads the run's own `--output-format stream-json` terminal `result` event for per-model token stats — no session-file scraping — and prices each of the three GA models at its own rate before flattening to the grok-shaped `usage.json`; the same module also remaps a quota/rate-limit error (no dedicated CLI exit code — parsed from the run's JSON `error.type`) to exit 75, while exit 41 (the CLI's own auth-failure code) passes straight through, so the orchestrator parks the `GEMINI` provider on either exactly like it does for grok's exit-75/78.

## Self-Healing & Feature Flags

**Self-healing CI loop (default-off).** RoboCo can watch its own repository's CI (a single named workflow) and, on a detected regression, open a fix task that is held out of dispatch until the CEO approves it (it terminates at `awaiting_ceo_approval`), then dispatch it through the normal delivery flow. It is dormant by default and armed by `ROBOCO_SELF_HEAL_ENABLED` plus a second opt-in `ROBOCO_SELF_HEAL_ORIGINATE_ENABLED`; origination is bounded by `ROBOCO_SELF_HEAL_MAX_OPEN_TASKS` / `_MAX_PER_CYCLE` so it can't flood the backlog. It never auto-merges or self-deploys (`roboco/services/self_heal_engine.py`).

**Multi-repo CI-watch (default-off).** The fan-out generalization of self-heal: instead of RoboCo's single own repo, it watches every project the operator opts into (`projects.ci_watch_enabled`, migration 048) and, on a red CI conclusion on that project's default branch, opens one fix task into that project's lifecycle that rides the normal delivery flow (+ PR-review gate) and never auto-merges. It reuses the exact hardened per-project `GitService.get_latest_ci_conclusion` (a missing signal is "unknown", never a false green; per-project errors are isolated and never abort the sweep), and is bounded + deduped per repo by `git_url` (a monorepo's cell-projects share one fix task) with per-cycle / rolling caps. Armed by `ROBOCO_CI_WATCH_ENABLED` (+ `_INTERVAL_SECONDS` / `_MAX_OPEN_TASKS` / `_MAX_PER_CYCLE` / `_DEFAULT_WORKFLOW`) and per-project `ci_watch_enabled` / `ci_watch_workflow`; `MultiProjectCITelemetrySource` (`roboco/services/telemetry/source.py`) + `CiWatchEngine` (`roboco/services/ci_watch_engine.py`) + a dedicated orchestrator `_ci_watch_loop`. The single-repo self-heal loop is untouched.

**Dependency-update bot (default-off).** A per-project engine mirroring the self-heal/CI-watch shape: weekly (default) it probes whether a dependency upgrade would change a project's lockfiles and, if so, opens one "update dependencies" task that rides the normal delivery flow (+ PR-review gate) and never auto-merges. Detection is read-only — `WorkspaceService.dry_upgrade_changes_lockfile` runs the project's `dep_update_command` (e.g. `uv lock --upgrade`) in a throwaway clone of the read clone and diffs the lockfile paths (`dep_update_paths`, or inferred `uv.lock`/`pnpm-lock.yaml`); the read clone is never mutated, nothing is committed/pushed, and a null/failing command originates nothing (fail-safe). A project participates only when `projects.dep_update_command` is set (migration 049); bounded + deduped per `git_url` with per-cycle/rolling caps. Armed by `ROBOCO_DEP_UPDATE_ENABLED` (+ `_INTERVAL_SECONDS` default 604800 / `_MAX_OPEN_TASKS` / `_MAX_PER_CYCLE`); `DepUpdateEngine` (`roboco/services/dep_update_engine.py`) + a dedicated `_dep_update_loop`.

**Docs-divergence sync (default-off).** Keeps the public docs site honest per release: when a release publishes and the docs site has drifted, `DocsSyncEngine` (`roboco/services/docs_sync_engine.py`) opens ONE docs-update task that rides the normal delivery flow (+ PR-review gate) and never auto-merges. Release-triggered, not polling; requires the docs-site repo (roboco-website) registered as a project with a git token; bounded + deduped like the other originate-one-task engines. Armed by `ROBOCO_DOCS_SYNC_ENABLED`.

**Gated release manager (default-off).** The autonomy that automates cutting a release up to the decision. A default-off background loop (`ReleaseManagerEngine` + `_release_manager_loop`) runs the deterministic readiness sweep (`ReleaseReadinessService.assess`, `roboco/services/release_readiness.py`) — diff-since-tag → conventional-commit classification → semver bump → version-reference completeness (the missed-ref guard) → CHANGELOG completeness → docs-drift (agent count) → migration single-head → gate state — and, past a threshold (`ROBOCO_RELEASE_MIN_COMMITS`, or any feat/security) with a green gate, originates ONE **release proposal** held for the CEO. The proposal is a `source='release_manager'` task owned by the Secretary, HELD (`confirmed_by_human=False`) and skipped by every dispatcher — acted on only by the CEO-gated routes, never delivered. The CEO approves or rejects-with-changes in the panel (`release-proposal-card.tsx`; `GET/POST /api/release/proposal{,/approve,/reject}`, CEO-only); approval runs the **fail-closed** `ReleaseExecutor` (`roboco/services/release_executor.py`): write the bumps across the canonical set (derived from the previous `chore(release):` commit) + the CHANGELOG entry, run `make quality` (abort before commit on red), commit `chore(release): X.Y.Z` (signed) + push, wait for green release-commit CI (abort before publish on red), then `gh release create vX.Y.Z`. Idempotent (an already-published version is a no-op) and never publishes without the CEO. Correctness is deterministic code, not agent judgment; the only generative step is the CHANGELOG prose, which the CEO reviews. Armed by `ROBOCO_RELEASE_MANAGER_ENABLED` (+ `ROBOCO_RELEASE_MIN_COMMITS` / `_INTERVAL_SECONDS`). Auto-deploy stays out of scope — publishing builds images; deploying to the NAS is the CEO's manual step.

**Organizational memory loop (default-off).** Closes the learn→reuse loop so agents stop cold-respawning blind. Three parts, all gated by `ROBOCO_ORG_MEMORY_ENABLED`: ① **capture** — at task completion `TaskService._completion_learnings_for` distills ONE high-signal lesson (Problem→Approach→Gotcha, ≤120 words) via the local model (`MemoryDistiller`, `roboco/services/memory_distiller.py`) instead of the noisy raw-notes/duration capture (flag-off keeps the legacy capture); journal indexing excludes `is_private` reflections from the shared corpus. ② **retrieve (keystone)** — on claim, `_briefing_for` injects `context_briefing["institutional_memory"]`: top-K (`ROBOCO_ORG_MEMORY_TOP_K`) relevance-floored (`ROBOCO_ORG_MEMORY_MIN_SCORE`) lessons + approved playbooks from a role-shaped query (`EvidenceRepo.similar_memory` over the LEARNINGS + PLAYBOOKS pgvector indexes); below the floor nothing is injected (no briefing bloat). ③ **playbooks** — a first-class curated procedure store: `PlaybookTable` (migration 050), the `PLAYBOOKS` OptimalService index, the `draft_playbook` content verb (delivery roles), Auditor `approve_playbook`/`reject_playbook`/`archive_playbook` curation (approval indexes it), and the panel review queue (`playbook-review-queue.tsx`; `/api/playbooks` Auditor/CEO routes). Distillation runs on the local model only — never a cloud LLM in the hot path; every step is best-effort (a failure never blocks completion or the briefing).

**Sandboxed dev DB/Redis/Mongo (default-off).** Per-project opt-in (`projects.sandbox_services`, migration 057); when armed (`ROBOCO_SANDBOX_DB_ENABLED`), provisioning is **on-demand (2026-07-08)**, not eager at spawn: a developer or QA agent calls the `request_sandbox` do-verb (role-scoped to `_DEV_DO`/`_QA_DO` in `role_config.py`; `services` omitted means the project's whole opted-in set) and `ContentActions.request_sandbox` (`roboco/services/gateway/content_actions.py`) walks a guard chain — flag off; no active project-bound task; project not opted into any service; a requested service outside the opted set (remediate names the allowed set); orchestrator handle unavailable (the one **retryable** guard) — before calling `AgentOrchestrator.ensure_sandbox`, which always provisions the project's whole opted-in set regardless of the requested subset (so a later subset/superset request within that set is a guaranteed cache hit and can never trigger a mid-session teardown of a live container the agent is using), verifies a cache hit is still live before trusting it (evicting + re-provisioning on a dead container), serializes concurrent calls for one agent behind a per-slug `asyncio.Lock`, and caches the result in-memory per agent slug (`_sandbox_info`) — the verb filters the returned creds back down to what this call actually asked for. Sibling containers get random per-sandbox creds, tmpfs data dir, memory/cpu caps, labeled `roboco.sandbox=1`; creds return in the verb's envelope `evidence` (`SandboxInfo.as_payload()`), one entry per service including a ready-to-`export` `env` sub-dict (`ROBOCO_TEST_DB_*` / `ROBOCO_TEST_REDIS_*` / `ROBOCO_TEST_MONGO_*`) — never injected as container env, so no spawn-time creds delivery exists at all. Spawn itself only injects a cheap marker env `ROBOCO_SANDBOX_SERVICES_AVAILABLE=<csv>` (never creds) for an opted-in project, plus a briefing line naming `request_sandbox()` explicitly, **in place of** the legacy prod-creds gate-env injection (`_append_gate_env`, which points agents at RoboCo's own production Postgres under `ROBOCO_TOOLCHAIN_MATCH_ENABLED`) — sandbox replaces, never coexists with, prod creds. A provisioning failure now surfaces as a retryable envelope on the verb, never a spawn refusal — sandbox trouble can no longer block dispatch. A sandbox is torn down at end-of-engagement, not just at container removal: `AgentOrchestrator.release_sandbox(agent_slug)` is called (best-effort, never failing the verb; a fast cache-check no-op when nothing was ever requested) by the Choreographer on the SUCCESSFUL exit of `i_am_done` / `unclaim` / `i_am_idle` / `pass_review` / `fail_review` / `i_documented`, so a sidecar doesn't outlive the work that requested it. Lifetime still tracks the agent container 1:1 as the backstop: teardown at every removal path plus an orphan janitor at startup + each reaper tick (grace-windowed so a sweep can't reap a sandbox whose request is still mid-flight; the pre-spawn stale-clear likewise spares a just-requested sandbox) also evicts the `_sandbox_info` cache entry. **Known ceiling:** the cache is in-memory only — an orchestrator restart forgets live sandboxes, so the next `request_sandbox` call re-provisions (the pre-clear tears down any still-running stale container) and returns fresh creds. Docker-in-agent stays structurally absent throughout. The service set is a **pluggable engine registry** (`roboco/models/sandbox.py`): each engine declares its image, run args, readiness probe, and `ROBOCO_TEST_*` env; `VALID_SANDBOX_SERVICES` is derived from the registry, and the provisioner + the verb's payload builder iterate it, so adding an engine (e.g. mongo) is one class + one registry line — no branch edited in the provisioner or the env emitter. **Extensions/modules on the fly (2026-07-13):** a project may declare `sandbox_extensions` (migration 072, jsonb null) — a per-service extension/module map (e.g. `{"postgres": ["vector", "postgis"], "redis": ["search"]}`) activated post-ready via `docker exec` (`CREATE EXTENSION IF NOT EXISTS` / `MODULE LOAD`) then verified; `request_sandbox(extensions=...)` unions a per-call override with the project's standing set, bounded to the opted set + a fixed allowlist (`SANDBOX_PG_EXTENSIONS` = vector/postgis/pg_trgm/citext/uuid-ossp, `SANDBOX_REDIS_MODULES` = search/json/bloom — `plpython3u` excluded by construction; mongo has none). No default set — opters set extensions explicitly, existing opters stay bare. A bare request uses the light upstream image; features pull a kitchen-sink image (`image_for(features)`), so the pgvector+postgis intersection just works. Cache-by-features: a cached entry satisfies a new call iff services are a subset AND per-service requested features are a subset of cached features; a superset re-provisions (rotates creds). The evidence entry carries `available_extensions`. Set the full set in project settings so agents request subsets.

**Cloud auth via FastAPI Users (default-off).** Lets the panel/API be safely exposed beyond localhost without touching the CEO's local no-login flow while off. Gated by `ROBOCO_CLOUD_AUTH_ENABLED` (+ `ROBOCO_CLOUD_AUTH_EMAIL` / `_PASSWORD` / `_SECRET` / `_COOKIE_MAX_AGE`; `Settings` fails loud at startup if the flag is on with no secret). Off: `get_agent_context` (`roboco/api/deps.py`) and the WS `_require_panel_token` gate (`roboco/api/websocket.py`) are byte-for-byte unchanged (header-trust). On: header-trust is dead for humans — any agent-role claim (`ceo` OR a privileged `main_pm`/`cell_pm`/board role) with no valid HMAC token or session cookie is 401, closing the header-spoof hole on the host-published `:8000` port for every role, not just `ceo` (real agents always carry a signed token, so they're unaffected); the agent-fleet HMAC path (and the orchestrator's `system` self-PATCH) keeps working unmodified in both modes; a valid session cookie authenticates as the single seeded CEO user. New `users` table (migration 058, `UserTable` in `roboco/db/tables.py`) backs FastAPI Users' `SQLAlchemyUserDatabase`; no registration router — `roboco/api/auth/seed.py` idempotently upserts exactly one row from env at startup (by primary key, so an email change renames the row instead of duplicating it). `roboco/api/auth/backend.py` wires a **cookie** transport (httponly, secure, samesite=lax) + a `JWTStrategy` subclass that binds each token to a fingerprint of the current `hashed_password`, so rotating the seeded password invalidates every prior session. Session lifetime is **sliding**: every authenticated request through `get_agent_context` re-mints + re-sets the cookie (`_slide_session_cookie`), so an active session never expires — only genuine inactivity past `cloud_auth_cookie_max_age` (default 30 days) logs out. `GET /api/auth/status` is always mounted (public); `/api/auth/login` + `/api/auth/logout` mount only when armed (`roboco/api/auth/routes.py`, mirroring `apply_guard`'s conditional mount). A second route mints the identical cookie without a password: `POST /api/telegram/webapp-auth` (`roboco/api/routes/telegram.py`), mounted only when `telegram_miniapp_enabled` AND `cloud_auth_enabled` are both armed — see the Telegram bridge entry below. Panel: `(auth)/login/page.tsx` + `proxy.ts` (the Next 16 rename of `middleware.ts`; probes `/auth/status` over the docker-internal orchestrator URL, not through nginx, and fails open to "off" on any probe error/timeout) gate the `(dashboard)` group; `client.ts` adds `withCredentials` + a 401→`/login` redirect. nginx needs no changes (`/api/auth/*` rides the existing `/api/` proxy location) — but its own static `X-Agent-Token` injection (`ROBOCO_PANEL_AGENT_TOKEN`) is itself a valid HMAC credential that bypasses login when present, so a deployment arming cloud auth for real public exposure should leave that token unset (the two are alternative human-auth tiers, not layered).

**RoboCo X account (default-off).** The Head-of-Marketing voice on X (Twitter): drafts a post when a release publishes, drafts replies to meaningful mentions, and — a third, independent capability — periodically investigates RoboCo's own shipped features and drafts a spotlight for an under-publicized one. NOTHING auto-posts across any of the three; every tweet is held in a panel queue for the CEO to edit/approve. Gated by `ROBOCO_X_ENGINE_ENABLED` (+ `_MENTIONS_INTERVAL_SECONDS` / `_MENTIONS_MAX_PER_CYCLE` / `_MENTIONS_MIN_ENGAGEMENT` / `_MAX_OPEN_POSTS` / `X_ACCOUNT_USER_ID`); inert without credentials regardless. Mirrors the `ReleaseManagerEngine` held-artifact shape: `XEngine` (`roboco/services/x_engine.py`) originates a held task (`source` `x_post` / `x_reply` / `x_feature`, `confirmed_by_human=False`, Secretary-owned, skipped by every dispatcher) whose marker payload carries a body clamped to 280 chars. Release posts hook `ReleaseProposalService.approve`'s publish-success branch via a small `draft_release_post` seam; mentions ride a dedicated `_x_mentions_poll_loop` (no webhook infra exists) deduped by a `x_seen_mentions` ledger + per-cycle/open caps — both are **local-model-drafted** (never a cloud LLM in the hot path). The spotlight half is the one exception to "no agent spawn": gated by its own sub-switch `ROBOCO_X_FEATURE_SPOTLIGHT_ENABLED` (+ `_INTERVAL_SECONDS`, default 3 days) on top of `x_engine_enabled`, `_x_feature_spotlight_loop` opens a held PENDING exploration task (`source=x_feature_exploration`, team=Board, assigned to Head of Marketing, carrying a `x_seen_features` dedup-ledger snapshot marker) that `_dispatch_pm_work` routes (mirroring `ROADMAP_SOURCE`) to a one-shot real cloud-LLM spawn of the Head of Marketing — full read tools, investigates CHANGELOG.md/feature-flags/docs/map/charter/KB, calls the Head-of-Marketing-only `propose_feature_spotlight` do-tool exactly once, which marks the feature slug seen (`x_seen_features` table, migration 061) and materializes a brand-new `source=x_feature` held draft (completing the exploration task as a side effect — a deliberate asymmetry from `propose_roadmap`, which instead leaves its own task open). The four OAuth 1.0a secrets live Fernet-encrypted in a singleton `x_credentials` row (migration 059, all-or-nothing set/clear, mirroring the git-token pattern; the API only ever returns `has_credentials`) — decryption is server-side only, agents never hold creds or egress. `XPostService.approve` (CEO-only route) is the ONLY caller of `x_client.post_tweet`: it posts under a Redis single-flight lock, **re-reads the committed task state inside the lock and commits COMPLETED before releasing** so a concurrent approve can't double-post, and is idempotent (an already-posted draft is a no-op). The hand-rolled OAuth 1.0a HMAC-SHA1 signer (`roboco/services/x_client.py`) adds no dependency; a `NullXClient` makes the unconfigured path a graceful no-op (research-engine posture). All three draft kinds share one voice: `XEngine._voice_guide` reads the CEO-editable `company_goals.brand_voice` charter field (migration 061, panel-editable in Business → Goals) and appends it to a generic baseline (`_HOM_VOICE`) — the baseline alone until the CEO supplies a real sample. Panel: `x-post-queue.tsx` (editable draft + 280 counter, approve/reject, a `sourceMeta`-driven label/icon per source including "Feature spotlight") + `x-credentials-card.tsx` (4 write-only secret inputs).

**RoboCo video engine (default-off).** Bespoke motion-graphics videos (release announcements, feature spotlights, on-demand CEO briefs) authored by a UX/UI dev and distributed to X/TikTok — nothing renders or posts without the flags on, and nothing posts without an explicit CEO approval. Gated by `ROBOCO_VIDEO_ENGINE_ENABLED` (+ sub-switches `ROBOCO_VIDEO_ON_RELEASE` / `ROBOCO_VIDEO_ON_SPOTLIGHT`, and `_MAX_OPEN_POSTS` / `_RENDER_INTERVAL_SECONDS` / `_RENDER_TIMEOUT_SECONDS` / `_REQUEST_TIMEOUT_SECONDS` / `_OUTPUT_DIR`); a CEO on-demand brief rides `POST /video/request` regardless of the release/spotlight sub-switches. A project opts in via `projects.video_engine_enabled` (migration 063, mirroring `ci_watch_enabled`): the global flag arms the subsystem, the per-project flag opts a repo into authoring against its `motion/` — `VideoEngine._opted_in_project` no-ops `open_video_task` until the operator flips it in the panel's edit-project dialog. Two task kinds mirror the XEngine/ReleaseManagerEngine "originate a CEO-scoped artifact" shape but split across the real delivery lifecycle: `VideoEngine.open_video_task` (`roboco/services/video_engine.py`) opens a normal, ASSIGNED **authoring task** (`source=video`, `confirmed_by_human=True`, team=UX/UI, balanced across `ux-dev-1`/`ux-dev-2` by open-task count) that dispatches like any other pre-assigned code task — NOT held, NOT in any dispatcher's skip bucket. The assigned dev builds a HyperFrames HTML composition under `motion/compositions/<id>/` and calls the UX/UI-team-gated `propose_video` do-tool (metadata-only: composition id, input props, per-platform captions — every developer role carries the tool on their manifest, but the runtime `_caller_team` check rejects a be-dev/fe-dev) to stamp the task's `video_draft` marker, then commits + `open_pr` through the normal PR-review gate. Once that authoring task reaches `completed`, the orchestrator's `_video_render_loop` (bounded retry, `_MAX_VIDEO_RENDER_ATTEMPTS`) tars the merged `motion/` dir from the project's read-clone and POSTs it to the credential-free **video-renderer sidecar** (`VideoRenderer` in `roboco/services/video_renderer_client.py`, `ROBOCO_VIDEO_RENDERER_BASE_URL`) to render both the 9:16 and 1:1 cuts to MP4 (`video_output_dir`); on success `VideoEngine._originate_video_post` materializes a held **video-post draft** (`source=video_post`, `confirmed_by_human=False`, Secretary-owned, skipped by every dispatcher) carrying `mp4_paths` (`{vertical, square}` absolute paths) + the per-platform captions. The CEO reviews it in the panel's video queue (`video-post-queue.tsx`; `GET /video/posts` lists drafts including `mp4_paths` so the panel knows which cuts exist, `GET /video/posts/{id}/media?cut=vertical|square` streams the MP4 bytes for the preview player, CEO-gated throughout) and edits captions / approves / rejects. `VideoPostService.approve` (`roboco/services/video_post_service.py`) is the ONLY caller of the X-v2 (`XVideoPoster` in `x_video_client.py`) and TikTok inbox-upload (`TikTokPoster` in `tiktok_client.py`) posters; because a video upload + transcode/poll can run well past a minute, the critical section runs under a heartbeat-renewed Redis mutex (`heartbeat_mutex.py`, mirroring `ReleaseProposalService`'s release-execute lock shape) rather than a flat lock, commits each platform's posted-id durably before attempting the next (a partial failure never re-posts an already-succeeded platform on retry), and is idempotent (an already-`COMPLETED` draft returns the stored ids without calling a poster again). TikTok's four OAuth2 secrets live Fernet-encrypted in a singleton `tiktok_credentials` row (mirroring the git-token / `x_credentials` pattern; the API only ever returns `has_credentials`) — set via the panel's TikTok credentials card. `NullVideoRenderer` / `NullXVideoPoster` / `NullTikTokPoster` make every unconfigured leg a graceful no-op rather than a crash. **Artifact verification (2026-07-16):** authoring is gated on the RENDERED clip, not its source — the `request_render` do-verb (developer/QA, mirroring `request_sandbox`'s shape) renders the caller's actual composition via the sidecar's new frames mode (`POST /render` with `frames=N` → ffprobe'd duration + N midpoint-sampled PNGs, tar.gz back) and extracts them to the container-shared `{workspaces_root}/{project}/.previews/{task8}/{orientation}/`; the dev renders their own working tree (worktree-aware, `head_sha`/`dirty` provenance), QA a read-only `git archive` export of the assembled branch (`WorkspaceService.export_branch_motion` — the read clone's pinned HEAD undisturbed). Success stamps the `render_preview` marker; `i_am_done` on a `source=video` task refuses without it (`Requirement.RENDER_VERIFIED` in the tracing gate, mirrored in the possibilities-matrix fast path — the canonical source string lives in foundation as `markers.VIDEO_TASK_SOURCE`), the dev spawn prompt orders Read-every-frame verification, and QA's `claim_review` evidence carries a `video_context` block so the reviewer checks output, not source. A CEO reject of a held video-post draft with a non-empty reason now also re-enters the flow: `VideoPostService.reject` → `VideoEngine.reauthor_from_rejection` opens a fresh authoring task carrying the verbatim feedback + a revise-in-place pointer at the existing composition (best-effort, never fails the reject).

**Board roadmap engine (default-off).** The Board originating strategic work: on a weekly interval (`ROBOCO_ROADMAP_ENGINE_ENABLED` + `_INTERVAL_SECONDS` / `_MIN_ITEMS_PER_CYCLE` / `_MAX_ITEMS_PER_CYCLE`) `RoadmapEngine` (`roboco/services/roadmap_engine.py`) opens ONE held **exploration** task (`source="board_roadmap"`, `confirmed_by_human=False`, PENDING, Product-Owner-assigned, `Team.BOARD`), deduped to one open cycle at a time. A dedicated one-shot `_dispatch_roadmap_exploration` spawns the Product Owner **solo** — deliberately NOT `_handle_board_assigned_task` (which would also spawn Head of Marketing and fire the Approve-&-Start handoff, both wrong for a PO-authored cycle) — reusing the `_board_dispatched` one-shot tracker + respawn breaker, and short-circuiting once the cycle is authored. The PO explores (read-only git, KB/RAG, metrics, releases, charter, optional web research) and makes ONE `propose_roadmap` call (a content verb gated to `product_owner` only, `_ROADMAP_ROLES`; wired through the do_server/Choreographer like `pitch`) authoring a **themed cycle** — a one-line goal + 3-7 item drafts — persisted as a `roadmap_cycle` marker on the exploration task (no table/migration). The CEO acts per-item in the panel roadmap queue (`roadmap-review-queue.tsx`; `/api/roadmap/cycles{,/items/{id}/approve,/items/{id}/reject}`, CEO-only): approve materializes that item as a BACKLOG task (`source="roadmap"`, no assignee — never auto-starts; normal PM activation picks it up) via `PrompterService.create_task_from_draft`, reject records a reason; when every item is terminal the exploration task completes (`RoadmapService`, idempotent per item). Dispatchers skip `board_roadmap` (never delivery work). `create_task_from_draft` honors a draft-declared `source` only from a `{prompter, roadmap}` whitelist — an LLM-authored draft can't impersonate a privileged origin.

**Obsidian vault V1+V2 (default-off).** The org's human-readable memory palace as a rebuildable DB projection — tasks, journals, and A2A digests as wikilinked markdown — gated by `ROBOCO_OBSIDIAN_VAULT_ENABLED` + `ROBOCO_VAULT_PATH` (default off, but both compose files arm it `true`). `VaultWriter` (`roboco/services/vault_writer.py`) is a pure, DB-free materializer under `RoboCo/{Tasks/<project-slug>,Journals/<agent-slug>,A2A,Agents,Archive/<year>/Tasks/<project-slug>,Reports}/`; every note carries a stable `aliases: [<id8>]` so a title rename (or an archival move) never breaks a `[[id8|title]]` cross-link, and private journals are excluded. Four best-effort event seams (`TaskService.create`'s materialize-on-create, `TaskService`'s status-transition frontmatter touch, `JournalService`, `A2AService`) patch/append on the relevant transition — a vault write failure never blocks the real action; materialize-on-create means a task's note exists from the moment it's created, not just at curation/rebuild. `python -m roboco.vault rebuild` re-projects every entity from the DB (preserving an existing task's Auditor-authored `## Narrative`, archive-aware so an old terminal task lands directly in `Archive/<year>/`) and materializes the shipped `.obsidian/` config (Dataview, Kanban, graph groups) + `RoboCo/_meta/` dashboards + `.base` Bases views from `roboco/vault_assets/`; `relocate <path>` moves the tree, grafting `RoboCo/` into an existing personal vault without touching its own config. The Auditor gets a one-shot `curate_vault(task_id, narrative)` do-tool, spawned by the orchestrator on each completed root task, writing the `## Narrative` section a deterministic write otherwise leaves as a placeholder. A second, independently-gated `ROBOCO_VAULT_INTAKE_ENABLED` watcher (`VaultIntakeEngine`) turns `#roboco`-tagged notes under the vault's inbox folder into PENDING, Product-Owner-assigned board-review drafts (`source=vault_note`) — the identical board-review path a chat-confirmed draft takes, never straight into delivery. Extraction runs on the local model with a deterministic fallback; the note body is screened through `foundation/policy/injection_guard.screen_external_text` (the same untrusted-content envelope `XEngine` applies to X mentions — flags an injection-pattern line inline, never removes content) before it reaches the prompt or the fallback. Deduped per `(vault-relative path, content hash)` via `vault_seen_notes`, so an edit re-qualifies a previously-seen note — the same hashing convention (every RoboCo feedback callout stripped first, `foundation/policy/vault_notes.py`) is now shared with the KB engine below. V2 adds three things on top: a **drift janitor** (`services/vault_janitor.py`, `_vault_janitor_loop`) hourly-ticked but gated by a `RoboCo/_meta/.janitor_state.json` state file so real work (a daily changed-task re-projection + random-sample drift check + archival pass, each capped at 200/cycle and per-item isolated so one bad row never wedges the sweep) and a weekly org-report (`vault_report_enabled`, default true — `VaultWriter.write_org_report` from `MetricsService`/`UsageService`, best-effort CEO notification) each fire exactly once per elapsed period regardless of loop/restart cadence; **archival** (`vault_archive_days`, default 30, `0`=off) moving old terminal tasks' notes into `RoboCo/Archive/<year>/Tasks/<project>/` during the sweep, alias links making the move free and the shipped Dataview/graph assets `Archive/`-aware; and **KB ingest** (`vault_kb_enabled`, default false — NAS compose arms it `true`, registry compose leaves it `false`) embedding the CEO's own `RoboCo/Notes/` (config `vault_kb_dirs`, csv, load-time-validated against traversal/overlap with reserved projection dirs) into a new `IndexType.VAULT_NOTES` corpus via `_vault_kb_loop` (`services/vault_kb_engine.py`, default 900s), with every note re-checked for symlink/path-escape at read time and screened through the injection guard as a hard GATE (a flagged note is quarantined — skipped, logged, callout-marked, never embedded) rather than the intake watcher's screen-and-still-process posture — reaching `roboco_kb_search`, `MentorService`'s default domain, and `EvidenceRepo.similar_memory` (claim-time briefings, relevance-floored, labeled `vault_note`) so the CEO's own writing finally becomes fleet-retrievable institutional memory.

**Fable-mode (default-off).** Full opus-fable-playbook adoption: makes the fleet behave more like Fable 5 on the existing model tiers (the tiers stay — Fable 5 the model is not an option). Two levers, both gated by `ROBOCO_FABLE_MODE_ENABLED`: ① **doctrine** — `fable_doctrine_layer()` (`roboco/agents/factories/_base.py`) composes the vendored behavioral doctrine (`agents/prompts/doctrine/fable.md`, from `github.com/rennf93/opus-fable-playbook` MIT `output-styles/fable.md`, YAML frontmatter stripped) into `compose_prompt`'s layer tuple immediately after `base.md` — universal cross-role doctrine, the same tier as the base rules, ahead of role/team/identity layers so those keep their specificity precedence. ② **hooks** — 5 vendored scripts under `docker/scripts/fable-*.sh` (stop-gate, bash-discipline, honesty-nudge, prompt-nudge, precompact; `session-start.sh` deliberately SKIPPED — its doctrine card is redundant with ① and its output-style check is inapplicable here) are installed alongside RoboCo's own hooks, never replacing them: `AgentOrchestrator._fable_hook_groups()` appends them AFTER RoboCo's own per-event entries in the Claude-path settings.json (isolated into its own helper to protect `_generate_agent_settings`'s xenon budget); the grok path installs only `honesty-nudge` (`write_grok_fable_hooks`, `roboco/llm/providers/grok_cli_config.py`) — a deliberately conservative V1 scope, since a grok `PreToolUse`/`Stop` hook deny cancels the entire run (verified live) while `PostToolUse` never denies. Off by default: the spawn path (composed prompt, settings.json, grok hooks) is byte-for-byte unchanged when the flag is off. No new eval harness — measurement rides the existing rework/spawn-waste/`revision_count` dashboard (see "Delivery observability" below). Armed on the NAS deploy like the rest; left OFF in `docker-compose.registry.yml`.

**Ponytail (bundled with Fable-mode).** Rides `ROBOCO_FABLE_MODE_ENABLED` — no separate flag. Vendors the ponytail "lazy senior dev" build-laziness doctrine (`agents/prompts/doctrine/ponytail.md` + ethos sibling, MIT, Copyright (c) 2026 DietrichGebert — trimmed, YAML frontmatter stripped) into every composed system prompt via `ponytail_doctrine_layer` (`roboco/agents/factories/_base.py`), slotted immediately after the Fable doctrine layer and gated on the same flag. Role-scoped: developers (`AgentRole.DEVELOPER`) get the full ladder (YAGNI → reuse-in-this-codebase → stdlib → native-platform → installed-dep → one-line → minimal); every other role gets the ethos-only cut (`ponytail-ethos.md`) — the code-mechanics rungs are dropped so they can't leak into prose artifacts (task plans, review notes, docs). Both files carry a 5-point RoboCo preamble (the ethos sibling adds a 6th: free-text field obligations) that makes the ladder yield to the Architectural Conventions Standard (placement), the 80% coverage gate + QA review + self-verification, the per-team design bar, task hygiene (everything-is-a-task / commits-linked / state-is-sacred), and reviewer feedback (`needs_revision` / `pr_fail` / `request_changes`) — the overlap mitigation is scoping, not deletion, and it rides ponytail's own "when NOT to be lazy" clause. Developer intensity is tunable via `ROBOCO_PONYTAIL_INTENSITY` (`lite` / `full` / `ultra`, default `full`; `roboco/config.py` `ponytail_intensity`, a string value — not a feature flag): `full` enforces the ladder, `lite` builds what's asked and names the lazier alternative, `ultra` is YAGNI-extremist (deletion before addition, challenge the requirement). Non-developers get no dial — `ultra` is wrong for prose artifacts, so the ethos runs a fixed restrained stance. Prompt-only: no hooks, no grok-path changes — ponytail adds no hook surface, so bundling it under the Fable flag changes only the composed prompt, not the spawn hooks. The Fable flag's description in `roboco/config.py` names both doctrines.

**Env-branches ladder + EnvSyncEngine (default-off `ROBOCO_ENV_SYNC_ENABLED`).** Replaces a project's single `default_branch` with an ordered environment ladder: nullable `projects.environments` JSONB (migration 073), an ordered `list[{name, branch}]` where index 0 is the **head** rung (where dev/cell/leaf PRs land) and index -1 is the **prod** rung (where the gated release executor commits + tags); middle rungs are intermediates (qa/stag). A null ladder degenerates to a single-branch ladder synthesized from `default_branch` at read time (`roboco/models/env_branches.py`: `head_branch` / `prod_branch` / `ladder_pairs` / `promotion_chain`) — no backfill, byte-for-byte legacy behavior until the CEO declares a real split. Every former `default_branch` consumer now routes through the shim: the PR target and per-agent clone (`WorkspaceService.ensure_workspace` / `ensure_read_clone`), the CI branch, the release executor's clone/commit/tag target (`_ReleaseContext.prod_branch`) plus its full-chain head→…→prod promotion before bumping (`promote_env_chain`, fail-closed `promotion_failed` on a merge conflict), and `release_readiness`'s diff baseline (`prod..head` instead of `last_tag..HEAD`) with a tag-drift cross-check (`_tag_drift_gaps` — the last tag's commit vs. prod tip disagreeing flags a hotfix that landed on prod after the tag). `EnvSyncEngine` (`roboco/services/env_sync_engine.py`) cascades the ladder prod→…→head via GitHub's merges API: a clean merge auto-pushes straight to the lower rung, a conflict opens ONE idempotent sync PR + a Main-PM coordination task and stops that project's cascade for the cycle — the cascade's target is never the prod rung by construction, so "only the CEO merges master" still holds. Bounded + deduped per repo (one open env_sync task at a time). Panel: an environment-ladder editor on the project edit dialog.

**Telegram notifications bridge V1+V2+V3 (default-off `ROBOCO_TELEGRAM_ENABLED`).** V1: best-effort, outbound-only Telegram DMs to the CEO on escalation and completion. Mirrors the `x_credentials` pattern: a singleton Fernet-encrypted `telegram_credentials` row (migration 074, bot token + chat id; the API returns `has_credentials` only) behind CEO-only `/telegram/credentials` routes and a panel credentials card. `_notify_telegram` (`roboco/services/notification_delivery.py`) fans out from `notify_ceo_of_escalation` / `notify_ceo_of_completion`, sending only the notification's subject plus an optional panel deep-link (`panel_base_url`) — never the body — via a deferred, best-effort send that never raises into the producer (`NullTelegramClient` when unconfigured or the flag is off, `LiveTelegramClient` posting to the Bot API otherwise). V2 (`ROBOCO_TELEGRAM_INBOUND_ENABLED`, sub-switch on top of V1's flag — both plus stored credentials are required, otherwise the bot only sends and never listens) makes the bridge two-way: `TelegramInboundEngine` (`roboco/services/telegram_inbound.py`) long-polls `getUpdates` from a dedicated orchestrator loop (`_telegram_poll_loop`), authorizing every update by BOTH chat id and sender id, and routes `/status` / `/queue` / `/task` commands plus `Approve`/`Reject`/`Open` inline-keyboard taps (a compact `apv|rej:<kind>:<id8>` callback codec; a reject reason or a task-approve note is collected via a force_reply prompt held in a TTL'd in-memory pending-action map) through the SAME CEO-gated service calls the HTTP routes make (task/release/xpost/video/roadmap), stamping a `via=telegram` audit row on each. Escalation DMs (not completion DMs) carry the actionable keyboard when V2 is armed. All bot/bridge messages are HTML-styled (`parse_mode=HTML` with mandatory `_esc`/`_esc_attr` escaping at every dynamic interpolation and balance-aware 4096 truncation — the injection posture moved from no-parse_mode to escaping discipline), and every held-draft origination (release proposal, X post, video post, roadmap item via `propose_roadmap`) pushes a styled DM with its Approve/Reject keyboard the moment it materializes (`notify_ceo_of_queue_item`, best-effort, sharing `/queue`'s renderer). Closing the loop exposed a real hole: a stale Approve/Reject button targets its item by id regardless of current status, so `ReleaseProposalService.approve`/`.reject`, `XPostService.approve`, and `VideoPostService.approve` now all refuse an already-CANCELLED (rejected) or already-COMPLETED (published/posted) target instead of silently re-executing — a fix that also closes the identical hole via a replayed HTTP call, not just Telegram. V3 adds a Telegram **Mini App** sign-in: `POST /api/telegram/webapp-auth` (`roboco/api/routes/telegram.py`, mounted only when `telegram_miniapp_enabled` AND `cloud_auth_enabled` are both armed — `telegram_miniapp_enabled` is env-only like `cloud_auth_enabled`, deliberately off the panel feature-flags card, and fails loud at startup if armed without cloud auth on) validates Telegram's signed `initData` (`roboco/utils/telegram_initdata.py` — pure HMAC-SHA256 `WebAppData`-keyed validation, constant-time compare, a `telegram_initdata_max_age_seconds` freshness window with 60s clock-skew tolerance) against the stored bot token and the CEO's own `chat_id`, then mints the same cloud-auth session cookie `/api/auth/login` issues — turning the CEO's phone into a real panel client at the new `(tg)` route group (`/tg`: Approvals/Inbox/Board/Chat tabs, outside the normal dashboard shell; `proxy.ts`'s matcher excludes `tg(?:/|$)` so a phone session is never bounced to the password `/login` page it can't reach). Requires a public HTTPS origin (the cookie is secure-only) and BotFather's `/setmenubutton` pointed at `https://<host>/tg`. **V4 (Mini App V4)** rebuilds the cockpit and the command tier on both sides. Panel: the `(tg)` surface opens on a "Today" brief (`GET /api/telegram/today`, CEO-gated, one DB-only round trip via `TgCockpitService` — needs-you items, held-draft counts, fleet with per-agent task titles, day-rollup spend, ship state), the Approvals tab is a native card stack over all four held-draft queues (MainButton/BackButton/haptics with visible fallbacks; X 280-counter editing, blob-fetched video player, per-AC release view; a failed queue source is surfaced, never rendered as "queue is clear"), Chat/Today ride the shared `/ws/system` socket (invalidate-on-frame, poll fallback), theme adopts the user's Telegram `themeParams` scoped to `#tg-shell`, a dev-only mock bridge + `/tg?demo=1` fixtures make the whole surface workable in a plain browser, and shared primitives (`panel/src/components/tg/ui.tsx`) carry the visual language. Bot: `BOT_COMMANDS` is the single registry driving `/help` AND a once-per-process Bot API `setMyCommands` sync; `/agents` `/usage` `/blocked` join the read tier, and `/secretary` + `/newtask` bridge the chat into the SAME in-process live runtimes the panel drives (`roboco/services/telegram_bridge.py`): a per-chat consumer task drains the `PrompterLiveRegistry` stream (sole consumer — no sync reply seam exists) and pushes one Telegram message per `turn_end`; free text routes into the live session; a `draft` event becomes a Send-to-Board/Discard keyboard whose confirm runs `PrompterService.confirm_live_draft(route="board")` and PARKS the session so board feedback streams back into the thread; `/end` reaps; the bridge sweeps its own idle TTL (the held stream arms the registry keepalive, so the registry's reap never fires), parked sessions exempt. Intake/secretary containers are process-wide singletons, so a bridged session preempts a live panel session of the same kind by construction; MegaTask batches still confirm in the panel only. **V6 (Mini App V6)** is the premium overhaul: a native-type design system on the `#tg-shell` tokens (borderless elevated cards, wallet-style tabular-numeral heroes, floating dock; Share Tech Mono demoted to the `ROBOCO_` wordmark only) with Telegram window-chrome painting riding the theme bridge (`setHeaderColor`/`setBackgroundColor`/`setBottomBarColor`); Inbox moves behind a header bell as a pushed sub-page with humanized notifications (UUIDs resolve to task names via the Board's shared task index, `tg-format.tsx`); a new Metrics tab (period-segmented spend hero + by-agent/team/model + delivery/efficiency; tapping an agent pushes a drilldown over the previously-untapped `/usage/time-series?agent_slug` plus the member scorecard); Chat is rebuilt with honest scopes — Mine rides the participant-scoped `/a2a/chat/conversations` (resolved peer, real unread counts, mark-read on open, plain CEO send) while Fleet rides the admin list (task-linked threads interject via `replyAsCeo` with a recipient chip, task-less threads are watch-only), both with markdown transcripts, live pulse flashes, and a pinned **Secretary** live chat on the same `secretary_live` SSE session runtime the panel drives; and the Board task sheet carries the CEO's own decide verbs (approve / request-changes / unblock) instead of being read-only. The `/api/dashboard/*` router is now `require_panel_token`-gated at router level (mirroring `/api/usage`), closing the unauthenticated metrics/scorecard exposure.

**Possibilities matrix (default-off `ROBOCO_POSSIBILITIES_MATRIX_ENABLED`).** A work-already-done fast path on `i_am_done`: when a claimed/in_progress task already has commits, an open PR, every acceptance criterion addressed, and no open findings (`_work_appears_done`), the dev submits straight to QA in one call instead of the standard multi-turn plan/journal/local-gate derivation. `_i_am_done_fast_path` still runs the non-negotiable guards — ownership, branch-pushed, not-behind-base, conventions, `FINDINGS_ADDRESSED` — and trusts the PR's own CI-green signal as the quality-gate proxy (`_fast_path_quality_verdict`, the same signal `pr_pass` trusts); a repo with no CI signal falls back to the local `make quality` gate (plus the toolchain-match guard when `ROBOCO_TOOLCHAIN_MATCH_ENABLED` is armed), and a known-red CI refuses the fast path outright rather than shipping it to QA. The orchestrator's dev spawn prompt steers a matching task to a `WORK_ALREADY_DONE` state that tells the dev to call `i_am_done` directly instead of re-deriving what's already done.

**Reviewer/PM collision map (always-on).** The collision surface authored at delegate time (`intends_to_touch` / `adds_migration` / `touches_shared`) used to be consumed once by `SequencingService` to wire dependency edges and never shown to a reviewer again. `build_collision_context` (`roboco/services/gateway/choreographer/collision.py`, pure — no DB/IO) now surfaces it for a task under review: same-parent siblings that would collide — overlapping declared file globs, or both adding a migration (the Alembic-head collision needs no file overlap) — rendered with the overlapping globs and, where the caller hands real touched files, a declared-vs-actual drift flag. Capped at 10 siblings / 5 globs. The same builder feeds QA's `claim_review` evidence, the PR-gate's `claim_gate_review` evidence, the PM's `i_will_plan` planning briefing (no drift there — no work yet), and the panel's `GET /api/tasks/{id}/collision-map` route backing a Collision tab on the task detail page.

**Delegation detail-fidelity (always-on, 2026-07-16).** Details no longer thin out at hand-off in either direction. DOWN: `delegate` refuses any child that doesn't declare `covers_parent_criteria` mapping onto the parent's real acceptance criteria (matched by id or exact text; an unresolvable ref is rejected naming the valid criteria, never silently dropped — previously the mapping was optional and coverage surfaced only at `submit_up`'s roll-up gate, after the whole wave had already run); the success envelope carries `parent_ac_coverage` `{covered, uncovered}` so a wave-planning PM sees remaining gaps in the same turn, while multi-wave planning stays legal. UP: `pass_review` requires `criteria_verified` — one `{criterion, evidence}` entry per task acceptance criterion (the findings ledger's id-or-exact-text matcher, soup-checked and length-capped evidence), rejecting with the unverified criteria named; entries render deterministically into `qa_notes` as `[AC] <criterion> — verified: <evidence>` lines, so a gestalt "looks good" pass is structurally impossible. Video briefs stopped being prose-only: an enumerable feature list (release `highlights`, or `input_props.highlights` carried onto a reject re-author) becomes its own acceptance criterion ("Every brief-named feature appears as its own fully readable scene: …", bounded to the AC caps; a re-author without highlights carries "every point in the CEO rejection feedback is visibly addressed"), so the dropped-scene class — a four-feature brief shipping three scenes past every gate — is caught by the QA per-AC stamp instead of the CEO's eyeball.

**PR labeler (always-on).** `derive_pr_labels` (`roboco/foundation/policy/pr_labels.py`, pure) derives the org-structure label vocabulary every fleet PR now carries: `to {base_branch}` — the PR's REAL resolved target branch, verbatim (never assumed from `is_root_pr`, so a project with a renamed/non-standard trunk or an env-ladder rung gets an accurate label instead of a hardcoded `master`/`slave`), `root` for an assembled root PR, `MegaTask` for a batch-carrying task, and a layer label (`main-pm` for a Main-PM coordination root, `cell/{team}` for a cell-assembled PR, else `subtask/{team}` for a leaf dev PR). Applied best-effort at all three PR-opening sites in `GitService` so a human triaging the PR queue sees which tree and which org layer a PR belongs to at a glance.

**Feature flags / company-in-a-box.** Env-gated, default-off subsystems toggle from the panel's Settings → Feature Flags card (`panel/src/components/settings/feature-flags-card.tsx`) instead of hand-editing env: web research (`ROBOCO_RESEARCH_ENABLED`), the strategy engine (`ROBOCO_STRATEGY_ENGINE_ENABLED`), pitch provisioning (`ROBOCO_PROVISIONING_*`), external / internal PR review, the agent-runtime toolchain match (`ROBOCO_TOOLCHAIN_MATCH_ENABLED`), the architectural-conventions standard (`ROBOCO_CONVENTIONS_ENABLED`), gateway-health recovery (`ROBOCO_GATEWAY_HEALTH_ENABLED`), multi-repo CI-watch (`ROBOCO_CI_WATCH_ENABLED`), the dependency-update bot (`ROBOCO_DEP_UPDATE_ENABLED`), the gated release manager (`ROBOCO_RELEASE_MANAGER_ENABLED`), the organizational memory loop (`ROBOCO_ORG_MEMORY_ENABLED`), the sandboxed dev DB/Redis (`ROBOCO_SANDBOX_DB_ENABLED`), the RoboCo X account (`ROBOCO_X_ENGINE_ENABLED`), the RoboCo video engine (`ROBOCO_VIDEO_ENGINE_ENABLED`), the board roadmap engine (`ROBOCO_ROADMAP_ENGINE_ENABLED`), Fable-mode (`ROBOCO_FABLE_MODE_ENABLED`), the vault weekly report + KB ingest (`ROBOCO_VAULT_REPORT_ENABLED` / `ROBOCO_VAULT_KB_ENABLED`), the env-sync cascade (`ROBOCO_ENV_SYNC_ENABLED`), the Telegram notifications bridge (`ROBOCO_TELEGRAM_ENABLED`, + inbound commands/actionable buttons sub-switch `ROBOCO_TELEGRAM_INBOUND_ENABLED`), the possibilities matrix (`ROBOCO_POSSIBILITIES_MATRIX_ENABLED`), the docs-divergence sync (`ROBOCO_DOCS_SYNC_ENABLED`), and the self-heal flags above. Cloud auth (`ROBOCO_CLOUD_AUTH_ENABLED`) is deliberately NOT on this card — like `ROBOCO_DB_NETWORK_ISOLATED`, it's a compose/env-coupled flag a runtime toggle can't safely flip mid-session. A toggle persists in the settings store and takes effect on the next backend restart; an unset flag falls back to its environment / config default.

## Architectural Conventions Standard

**Per-project architectural standard (default-off).** Beyond the `make`-style gates (which check syntax/types/tests, not *where code lives*), each project can carry a repo-canonical `.roboco/conventions.yml` — an architecture map (which definition *kinds* belong in which modules), a toggleable rule set, custom regex rules, and waivers — so an agent cannot land a Pydantic model defined inside a router or a `# noqa` / `# type: ignore`. Placement of a *helper* (any top-level function) only **warns** — too blunt to hard-block; `thin_routes` doesn't count an explicit `db.commit()`; and a small allowlist of unavoidable framework suppressions (ruff `TC001`–`TC003`, pydantic `prop-decorator`) is exempt. Gated by `ROBOCO_CONVENTIONS_ENABLED`; fully inert when off. RoboCo itself ships a canonical `.roboco/conventions.yml`.

**Effective map.** Consumers read the *effective* map — auto-derived defaults (from a repo scan + `BUILTIN_RULES`, excluding `tests/`/`docs/` trees) overlaid by the committed file — so behaviour is identical whether the file is present, absent, or partial. `ConventionsService` (`roboco/services/conventions.py`) builds it, caches it per `(project, HEAD sha)` in `project_conventions_cache` (migration `043`), renders the per-task baseline constraints + the ambient prompt block, and scaffolds/restores the file via a PR (`GitService.open_conventions_pr`). The committed file + scan are read from a dedicated project-level **read clone** the service ensures on demand (`WorkspaceService.ensure_read_clone`, pinned to the default branch's HEAD) — the backfill that makes the standard resolve even for a project created before it existed, with no manual `workspace_path`. The schema lives in `roboco/foundation/policy/conventions/` (pure).

**Validator.** A single Python CLI, `python -m roboco.conventions check --root <repo> --files <a> <b> ...` (`roboco/conventions/`), uses tree-sitter (Python + TypeScript grammars, shipped in the agent image) to classify each changed definition and flag forbidden placements + hygiene + custom-rule matches as JSONL findings, after waiver filtering. Precision over recall (it abstains when uncertain so a `block` gate can't false-positive-strand a task) and fail-loud (a validator that cannot run exits 3 so the gate blocks, never silently passes).

**Threading + enforcement.** The standard reaches the work two ways: an ambient "Architectural Standard" block injected at spawn (`compose_prompt`) and an auto-attached `## Constraints` section on every project task (`TaskService.create`). Enforcement is deterministic: a `block`-level finding refuses `i_am_done` (dev pre-submit) and `pr_pass` (the in-path PR gate) with the offending `file:line` + fix hint; findings also surface in QA's `claim_review` evidence (`convention_findings`). A false positive is relieved by a `waiver` the dev commits in their branch — accountable, reviewed in the PR. The panel's per-project Conventions tab (in the edit-project dialog) shows the map + health and offers Save / Restore.

## Design Bar

**FE/UX-UI design bar (prompt-only, always on).** Frontend and UX/UI team agents carry a design-taste bar distilled from `Leonxlnx/taste-skill` (MIT) in their team prompts, so agent-authored UI stops defaulting to generic-AI layout/fonts/motion. It's a `## Design bar` section appended to `agents/prompts/teams/frontend.md` and `agents/prompts/teams/ux_ui.md`, reached by every cell role on those teams (dev/QA/PM/Documenter) via the team prompt layer, plus a pointer in the shared `agents/prompts/roles/developer.md` so `fe-dev`/`ux-dev` know to look for it without leaking the content into `be-dev`'s prompt. It covers three tuning dials — `DESIGN_VARIANCE` / `MOTION_INTENSITY` / `VISUAL_DENSITY` (1-10 each; dense product UI like the panel defaults to `2-3 / 2-3 / 7-8`) — plus typography/hierarchy, spacing/layout, motion, and "AI tells to avoid" rules, scoped to respect a project's existing design system (fonts, colors, radius) rather than silently override it. Prompt-only: `compose_prompt` itself is unchanged, no new verb/gate/state; guarded by `tests/unit/agents/test_design_bar_layer.py`.

**Niche aesthetics + Image direction (the deferred taste-skill half, prompt-only, always on).** Two more `Leonxlnx/taste-skill` (MIT) distillations on top of the core Design bar. A `## Niche aesthetic vocabularies` section (identical body in both `frontend.md` and `ux_ui.md`) names three opt-in visual systems — industrial brutalist, minimalist editorial, premium agency — each keyed to the same three dials (a vocabulary changes what the dials produce, not whether they apply); picked only when a task brief explicitly calls for one, never a default. A `## Image direction` section lives in `ux_ui.md` only (that team owns visual-asset/motion-composition work): composition variety, palette discipline, anti-slop imagery, iconography, mockup/device-frame conventions, and cross-asset set consistency, distilled from taste-skill's imagegen skills; `frontend.md` carries a one-line pointer to it instead of duplicating the content. Guarded by the same `tests/unit/agents/test_design_bar_layer.py`.

## MegaTask (sequenced batch intake)

**MegaTask** lets the CEO describe several tasks in one Intake chat and ship them as one collision-aware, sequenced batch — even across projects that don't share a codebase (the motivating case: a SaaS app + its OSS core engine + a framework adapter). It is a **core capability, not a feature flag** (additive + opt-in by nature: proposed only when the CEO asks for several tasks; single-task intake is byte-for-byte unchanged), branded "MegaTask" on every user-facing surface while internal names stay technical (`batch_id`, `SequencingService`).

**The umbrella model.** A MegaTask's identity is a real **umbrella** task — branchless, no PR of its own — over N **root-subtasks**, each a real Main-PM coordination root with its own `project_id`, branch, and PR. Hierarchy: Umbrella (Main PM) → N Root-subtasks (Main PM) → Cell tasks (cell PMs) → Dev subtasks. One extra Main-PM layer on top of the normal model. The umbrella is the single board-review / CEO-approve / Main-PM-coordinate unit, so the batch plugs into the existing coordination-root flow for free (task tree, progress rollup, CEO queue).

**Identity predicate (single source of truth).** `roboco/foundation/policy/batch.py`: `is_batch_umbrella` (`batch_id` set AND `parent_task_id` None), `is_batch_root_subtask` (`batch_id` set AND parented), `is_branchless_coordination` ((no-project AND product) OR umbrella). Every git-exemption site consults it so the umbrella's exemptions can't drift: the orchestrator's `_is_coordination_task`, the claim→in_progress branch gate (`GitContext.is_coordination`), `_ensure_branch_for_task` (returns `""` for an umbrella), and the CEO-reject routing. `submit_root` hard-rejects an umbrella (it assembles no PR); umbrella completion reuses the existing branchless path (`all_subtasks_terminal`, PR waived → escalate to CEO).

**Sequencing.** The pure `SequencingService.analyze(surfaces, cell_of, cell_capacity)` (`roboco/services/sequencing.py`; schema in `roboco/foundation/policy/sequencing/`) turns each draft's collision surface — `intends_to_touch` (globs), `adds_migration`, `touches_shared` — into a dependency DAG + Kahn-layered **waves**: file-overlap serializes (more-important first by `(priority, idx)`), migration-adders chain serially, a shared-surface edit runs after each non-shared task it overlaps (file-overlap-conditioned), independent tasks run in parallel; cell-contention only warns. Correctness lives in code, not agent judgment. The columns `tasks.batch_id` + `intends_to_touch` / `adds_migration` / `touches_shared` are migration **046**.

**Intake + create path.** The intake chat can be scoped to a **MegaTask** (a multi-project picker → `StartLiveRequest.project_ids`); the orchestrator clones each repo (`_clone_intake_scope` / `_slugs_for_project_ids`, the multi-repo machinery products already used). The intake agent proposes the whole batch with one **`propose_batch`** tool call — wired on both runtimes (the Claude SDK driver emits one `batch` stream chunk; the grok `intake_server` POSTs a `batch` relay event). The panel's third intake scope accumulates it into a Review-MegaTask card → `POST /prompter/live/{session}/confirm-batch`. `PrompterService.confirm_live_batch` builds the umbrella + N root-subtasks (via `create_task_from_draft` + a `BatchPlacement`) and wires the analyzer edges through `add_dependency`. The Board route holds the root-subtasks in BACKLOG until `approve_and_start` releases them (`_activate_batch_root_subtasks`); the Main-PM route dispatches wave 0 at once. The Product Owner + Head of Marketing review the whole batch (their identity prompts carry a MegaTask section).

**Board-review → redraft loop (batch parity).** A first board-route batch confirm PARKS the intake session against the umbrella instead of reaping it — the same keep-alive loop single drafts get. When both board reviewers finish, the orchestrator injects a batch-aware brief into the still-live chat (`_compose_parked_intake_redraft` → `compose_batch_redraft_message`: every live root-subtask's snapshot + the board's decision notes + an explicit one-`propose_batch`-call re-proposal instruction); the revised batch re-confirms with `BatchConfirmRequest.task_id` set, which routes to `PrompterService.update_live_batch` — an in-place update, not a new batch: umbrella prose re-composed, live root-subtasks positionally patched (cancel+recreate only on a per-item scope change; create/cancel on count changes), dependency edges rewired to the fresh wave plan, and the create path's `_validate_batch_scope` gate re-applied so a redraft can't collapse the batch to one project or drift outside the scoped repos. Every reader uses the CANCELLED-excluding `get_live_subtasks` view, so multi-round redrafts survive earlier cancels. The cold fallback (`POST /prompter/live/re-interview/{task_id}`, the task-detail "Re-draft with board feedback" button) now handles the branchless umbrella by recovering its multi-repo scope from the live children (`TaskService.distinct_projects_for_batch`) and returning `project_ids` so the panel re-enters batch mode. On the re-confirm, `route="main_pm"` approves-and-starts (releasing the BACKLOG children) and `route="board"` clears `board_review_complete` for another review round; a redraft re-confirm always reaps (parity with single drafts — later rounds ride the cold path). Panel side: `confirmBatch`'s board branch keeps the chat open and threads `batchRedraftTaskIdRef` (persisted with the chat) into the next confirm.

## Services

Core services in `roboco/services/`:

| Service | Purpose |
|---------|---------|
| `TaskService` | Task CRUD and state transitions |
| `WorkSessionService` | Git session management, PR lifecycle |
| `WorkspaceService` | Multi-agent workspace resolution and cloning |
| `ProjectService` | Project/repository management |
| `NotificationService` | Formal notifications |
| `JournalService` | Agent journals and entries |
| `OptimalService` | RAG queries (in-house pgvector engine) |
| `PermissionsService` | Role-based access control |

## Configuration

Key settings in `roboco/config.py` (env prefix: `ROBOCO_`):

```bash
# Database
ROBOCO_DATABASE_HOST=localhost
ROBOCO_DATABASE_PORT=5432
ROBOCO_DATABASE_USER=roboco
ROBOCO_DATABASE_PASSWORD=roboco
ROBOCO_DATABASE_NAME=roboco

# Redis
ROBOCO_REDIS_HOST=localhost
ROBOCO_REDIS_PORT=6379

# Security (REQUIRED)
# Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
ROBOCO_ENCRYPTION_KEY=<your-fernet-key>

# Workspaces
ROBOCO_WORKSPACES_ROOT=/data/workspaces
ROBOCO_WORKSPACE_AUTO_CLONE=true
ROBOCO_WORKSPACE_CLONE_TIMEOUT=300

# RAG (in-house pgvector engine)
ROBOCO_RAG_CHUNK_STRATEGY=fixed
ROBOCO_RAG_CHUNK_SIZE=512
ROBOCO_RAG_USE_HYDE=true
ROBOCO_RAG_USE_HYBRID_SEARCH=true

# AI/LLM
ROBOCO_DEFAULT_EMBEDDING_MODEL=qwen3-embedding:0.6b
ROBOCO_LOCAL_LLM_MODEL=glm-5.2:cloud
ROBOCO_LOCAL_LLM_BASE_URL=http://roboco-ollama:11434/v1
ROBOCO_OLLAMA_BASE_URL=http://roboco-ollama:11434

# Doctrine (bundled with fable-mode)
ROBOCO_PONYTAIL_INTENSITY=full      # lite/full/ultra — developer ladder intensity (bundled with fable-mode)
```

## Docker Deployment

### Container Architecture

The system runs as Docker Compose services. All Dockerfiles live under `docker/` at the project root; every service uses `context: .` plus `dockerfile: docker/<name>.Dockerfile`.

| Service | Purpose | Healthcheck |
|---------|---------|-------------|
| `postgres` | PostgreSQL + pgvector | `pg_isready` |
| `redis` | Cache, sessions, event bus | `redis-cli ping` |
| `ollama` | Local LLM + embeddings | `ollama list` |
| `ollama-init` | Pulls models on startup | One-shot |
| `backup` | Daily `pg_dump` sidecar, data-only network, newest-14 rotation — see `docs/backend/ops/database-backups.md` | — |
| `agent-base-image` / `agent-*-image` | Pre-built images spawned per agent | One-shot |
| `orchestrator` | API + agent spawner | Depends on all above |
| `panel` | Next.js control panel (internal, port 3000) | — |
| `nginx` | Reverse proxy fronting panel + orchestrator | — |

### Single Entry Point

`nginx` is the only externally-exposed service. It listens on `localhost:3000` and routes:

- `/api/*` and `/ws/*` → `orchestrator:8000`
- everything else → `panel:3000`

This avoids CORS since the browser sees one origin. The Next.js code uses relative URLs (`/api`, `/ws`) and lets nginx do the dispatch.

### Network topology (DB isolation)

Two user-defined bridges: `roboco_default` (the agent mesh — panel, nginx, ollama, every spawned agent container, and their sandbox DB/Redis sidecars) and `roboco_data` (postgres + redis ONLY). The orchestrator is the only multi-homed service (both networks), so agent containers cannot resolve or TCP-reach `roboco-postgres:5432` / `roboco-redis:6379` at all — network membership is the containment (redis has no auth). Agent↔agent A2A (`:9000`), orchestrator→agent SDK polls (`:9000`), MCP→orchestrator (`:8000`), and host-published ports (`15432`/`16379`/`11435`) are unaffected; `docker exec`/`inspect` paths ride the daemon socket, not the network. `ROBOCO_DB_NETWORK_ISOLATED` (config default `false`) is set `true` by the compose files that carry this topology and suppresses the legacy `_append_gate_env` prod-creds injection (unreachable creds are worse than none); DB-needing projects use the sandbox opt-in instead. The flag is deliberately NOT in the panel feature-flags card — it must travel with the compose `networks:` stanzas.

### WebSocket streams

The orchestrator exposes WebSocket endpoints under `/ws` (router in `roboco/api/websocket.py`, `ConnectionManager` + `broadcast_*` helpers):

| Endpoint | Purpose |
|----------|---------|
| `/ws/agents/{id}`, `/ws/notifications/{id}` | Per-resource live streams |
| `/ws/system` | Operator/system-wide stream (no per-agent keying) — the rate-limit lifecycle (`RATE_LIMIT_HIT` / `RATE_LIMIT_LIFTED`), live usage (`USAGE_SNAPSHOT`, pushed to the usage dashboard), and A2A message events (`a2a.message` frames) |

Server-side events reach these sockets through `roboco/api/websocket_bridge.py`, which subscribes to the `StreamEventBus` and forwards each event to the matching connections. To add a new live event: define an `EventType` (dotted value), publish it to the bus, add a `_handle_*` forwarder in `websocket_bridge`, and consume it on the panel via the `useWebSocket("/<endpoint>", …)` hook — do not stand up a parallel endpoint or client stack. `A2A_MESSAGE_SENT` is the worked example: `A2AService.send` publishes it (excerpt-capped payload), the bridge forwards it to `/ws/system` as an `a2a.message` frame, and the panel's `useA2ALiveStream` hook (a second consumer of that same shared `/ws/system` connection) consumes it to invalidate-on-frame.

### Rate limiting & usage

- **Provider rate limits** are tracked in Redis (`RateLimitStateTracker`, `roboco/services/gateway/`). On a provider 429 an agent calls `i_am_blocked(reason="rate_limited")`; the spawn gate then **queues** (never drops) further work for that provider, and a background probe-and-resume loop in the orchestrator clears the limit and revives parked agents when it lifts.
- **Provider overloads** reuse the same park-and-probe break. A persistent model-API overload (HTTP 529 / 500 / 503 — the SDK already retries transient ones) parks the provider exactly like a 429 instead of crash-retrying the agent straight back into the overload and burning tokens; the overload is detected orchestrator-side from the dead container's log markers, and the background loop revives the parked work when it recovers. The same break also catches the **Claude session-limit** 429 (the org's 5-hour usage window): an agent exiting with a 0-token session-limit rejection parks the provider and is auto-revived when the window resets, instead of fleet-wide crash-respawning straight back into the limit. Gated by `ROBOCO_OVERLOAD_BREAK_ENABLED` (default-on).
- **Gateway-health recovery** closes a blind spot in the stale-claim reaper: the heartbeat is bumped only by gateway verbs, so a broken-but-alive agent (a corrupted `/app/.venv` so no gateway tool imports) goes heartbeat-stale yet keeps its container up, and the reaper's live-skip would protect it forever. On a stale-heartbeat live container the reaper now probes the gateway out-of-band (`_probe_gateway_health` → `docker exec` the gateway venv imports) and, once broken past `ROBOCO_GATEWAY_HEALTH_GRACE_SECONDS` (a transient probe miss is tolerated), kills + evicts it (`_maybe_recover_broken_gateway`) so it falls through to release + respawn; healthy or inconclusive probes spare it. Gated by `ROBOCO_GATEWAY_HEALTH_ENABLED` (default-on). It is the third leg beside the shipped bash-guard `/app` block (prevents the self-corruption) and the reaper Docker-liveness fallback (stops over-reaping live containers). The bash-guard hook also denies raw `uv run` / `uv pip` / `uv lock`/`add`/`remove`, `pip`/`pip3 install`/`uninstall`, `conda install`/`create`/`run`, and `poetry run`/`install`/`add` whenever a Makefile is present in the workspace, remediating to `make quality`/`gate`/`lint`/`test` instead (a Makefile-less project is unaffected); the grok path mirrors this with a native `--deny` rule set (`_RAW_PM_DENY`) that nudges the model back to `make` without canceling the run.
- **Tool-call budget.** A per-session counter (`docker/scripts/post-tool-budget-hook.sh` → the in-container SDK server) warns an agent at 100 tool calls and halts (auto-substitutes, releasing the task) at 300 (`BudgetPolicy.tool_call_warn_at` / `_halt_at`, `roboco/foundation/policy/agent_loop.py`; env overrides `ROBOCO_AGENT_TOOL_CALL_WARN` / `_HALT`). Raised from a 150 hard cap, which repeatedly halted legitimate multi-file work mid-task. A same-window loop detector (same tool + args repeated past `ROBOCO_AGENT_LOOP_THRESHOLD`) is a separate, tighter check that can deny the repeating call before the budget cap is ever reached.
- **PM coordinator concurrency.** A Main / Cell PM plans and delegates many root tasks in parallel — the actual work then runs in the delegated children/cells, not in the PM's own hands. The claim-time concurrency guards that keep a *developer* to one task at a time (`already_active` / `paused`, in `roboco/services/gateway/claim_guards.py`) are therefore **skipped for the coordinator PM roles** (`_COORDINATOR_ROLES = {main_pm, cell_pm}`, consulted in `_run_claim_guards`); only a genuine upstream **sequence dependency** (`unmet_dependency`, which parks the task back to `pending`) holds a PM's root back. Without this a single PM that claimed one root could never plan a second — it thrashed between its claimed roots and respawned forever, burning tokens for zero progress (the live `i_am_idle`-auto-paused-umbrella deadlock). The `paused` guard also excludes the target task itself, so a PM re-entering its own paused umbrella never self-blocks.
- **Orchestrator runtime-state durability.** The PM-respawn loop breaker (`_pm_respawn_tracker`, the `(agent_slug, task_id) → strike-count` circuit breaker) is **DB-durable** via the `respawn_tracker` table (migration 051): each gate mutation write-throughs fire-and-forget on the `_bg_tasks` set (`_schedule_respawn_persist` → `_persist_respawn_record`), and `restore_respawn_tracker()` repopulates it at `start()`, validating each row against live tasks (terminal/missing rows are evicted). Kept only in memory it reset to `count=1` on every restart and re-burned the whole strike threshold (4 spawns) against a still-wedged task. It mirrors the `WaitingRecordTable` / `restore_waiting_records` pattern: best-effort (a DB hiccup degrades to in-memory-only — it can only ever *suppress* a spawn, never manufacture one) and inert when the table is empty. The companion `_instances` registry is **reconciled-from-Docker** (not persisted) at startup via `_readopt_running_agents`, so the reaper's liveness path and the spawn gate's `_is_agent_active` check see surviving containers immediately after a restart.
- **Token usage** is captured per agent session from the Claude Code transcript via the SDK server's `/usage/sync` (hook → orchestrator finalize → `agent_spawn_sessions` → `daily_usage_rollups` → dashboard). Cost uses provider-aware pricing in `roboco/billing/pricing.py` (Anthropic priced; local/Ollama intentionally `$0`). The token sweep also publishes `USAGE_SNAPSHOT` to `/ws/system`, so the dashboard's "Token Usage & Cost" panel updates live and falls back to HTTP polling when the stream is down.
- **Delivery observability** (the panel's Metrics → "Delivery" tab) shows how work *flows*, computed by `MetricsService` from data already captured — no new feature flag. Per-stage cycle time and the bottleneck distribution are reconstructed from the `audit_log` transition journey (each generic `task.<status>` event marks entry into a status; the named `task.qa_fail`/`task.pr_fail` events are excluded from the reconstruction). Rework rate reads `tasks.revision_count` — incremented once per transition into `needs_revision` at the single chokepoint `TaskService._emit_status_transition_audit` — and attributes each bounce to the QA / PR-reviewer via those named audit events; rework cost joins `agent_spawn_sessions.task_id`. Read-only endpoints: `/dashboard/metrics/{cycle-time,bottlenecks,rework,scorecard/agent/{id},scorecard/team/{team}}`.

### Startup Sequence

The startup order is critical due to dependencies:

```
postgres ──┐
redis ─────┼──> ollama ──> ollama-init ──> orchestrator ──> panel ──> nginx
           │        │            │
           │        │            └── Pulls qwen3-embedding:0.6b, glm-5.2:cloud
           │        └── Healthcheck: ollama list
           └── Healthcheck: pg_isready, redis-cli ping
```

**Important timing notes:**
1. `ollama-init` pulls models (~30s for embedding model, ~2min for LLM)
2. Orchestrator waits for models before starting
3. FastAPI lifespan indexes documents using Ollama (~30-60s)
4. Orchestrator polls `/health` until API is ready before starting dispatcher
5. After orchestrator is up, `panel` (Next.js) builds/starts, then `nginx`

### Database migrations

Schema changes ship as Alembic migrations under `alembic/versions/`. Run:

```bash
docker compose exec orchestrator alembic upgrade head
```

after pulling any change that adds a new migration.

### Ollama Configuration

Ollama provides two APIs:
- `/v1/*` - OpenAI-compatible API (for LLM chat/completion)
- `/api/*` - Native Ollama API (for embeddings, model management)

The embedder uses `/api/embed` endpoint with the `qwen3-embedding:0.6b` model.

**Environment variables for Docker:**
```bash
ROBOCO_LOCAL_LLM_BASE_URL=http://roboco-ollama:11434/v1    # OpenAI-compat
ROBOCO_OLLAMA_BASE_URL=http://roboco-ollama:11434          # Native API
```

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `404 /api/embed` | Model not pulled | Check `docker logs roboco-ollama-init` |
| `All connection attempts failed` | API not ready | Orchestrator starts before FastAPI lifespan completes |
| Healthcheck failing | Wrong endpoint | Use `ollama list` not `curl` |

## Blueprint Reference

The organizational structure, communication matrix, role descriptions, and access-control model are documented inline above and in the user-facing documentation site at **[docs.roboco.tech](https://docs.roboco.tech)** (the `roboco-website` repo — Next.js MDX, the canonical docs site as of the 2026-07-03 docs-site split: `docs/internal/specs/2026-07-03-docs-site-split.md`). This repo's old MkDocs-built user tree is gone; `.github/workflows/docs.yml` now only deploys the committed `docs-redirects/` stubs (meta-refresh + canonical) so every URL the old Pages site published keeps resolving, to docs.roboco.tech. `docs/rag/` remains the agent-facing RAG corpus (never published); `docs/map/` is the agent-facing exhaustive codebase map — both directories are auto-indexed into the KB at startup and re-indexed periodically on file changes (`OptimalService.AUTO_INDEX_DIRS`, `roboco/services/optimal.py`), so `docs/map/*.md` is `roboco_kb_search`-able the same as `docs/rag/*.md`; `docs/internal/` holds specs and working notes; the old root `usage.md` / `deployment.md` now link straight to docs.roboco.tech.
