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

**In-path PR-review gate** (`awaiting_pr_review`): each assembled PR is reviewed before the PM merges. The cell PM's `submit_up` opens the cell→root PR and the Main PM's `submit_root` opens the root→master PR; both enter `awaiting_pr_review`, where a reviewer `pr_pass`es it on to `awaiting_pm_review` or `pr_fail`s it back to `needs_revision` — the merge-level reject the PM otherwise lacks. Leaf dev tasks and branchless coordination roots skip the gate.

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
| `in_progress` → `awaiting_pr_review` (submit_up / submit_root) | PM roles (opens the assembled cell→root / root→master PR) |
| `awaiting_pr_review` → `awaiting_pm_review` (pr_pass) | PR reviewer only |
| `awaiting_pr_review` → `needs_revision` (pr_fail) | PR reviewer only |
| `awaiting_pm_review` → `completed` | PM roles only |
| `awaiting_pm_review` → `needs_revision` (request_changes) | PM roles only — the merge-level reject with concrete issues |
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
| `Session` | Communication session with messages |
| `Channel` | Team communication channel |
| `Message` | Extracted message from agent streams |
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

**Communication** = constant stream (always flowing, logged, observed) **Notifications** = formal signals (require acknowledgment, sent by PMs/Board only)

### Channel Structure
- Cell channels: `#backend-cell`, `#frontend-cell`, `#uxui-cell`
- Cross-cell: `#dev-all`, `#qa-all`, `#pm-all`, `#doc-all`
- Management: `#main-pm-board`, `#board-private`
- Special: `#announcements` (read-only except Board/Main PM), `#all-hands`

The Auditor has silent read access to ALL channels.

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
| auditor       | `triage` (read-only — no `say`/`dm`)                                                             |
| prompter      | (none beyond `i_am_idle` — not a delivery-lifecycle role; intake interviewer, human-only)        |
| secretary     | (none beyond `i_am_idle` — human-only chief-of-staff; reads company state + runs gated CEO directives) |

Content tools (do_server) — most roles: `commit`, `note`, `say`, `dm`, `evidence`. Delivery roles (developer / qa / documenter / cell_pm / main_pm) also get `draft_playbook` (draft a curated playbook for the KB). Auditor is restricted to `note` (scope=reflect) + `evidence`, plus the playbook-curation verbs `approve_playbook` / `reject_playbook` / `archive_playbook` (a bounded, deliberate expansion — KB curation, not agent comms, so its no-`say`/no-`dm` restriction holds). The `pr_reviewer` posts its change-request on the PR itself (no agent comms). The `prompter` (intake) and `secretary` are restricted to `note` + `evidence` — human-only, no `say`/`dm`/`notify`. The `note`/journal write returns as soon as the entry is persisted; RAG indexing (Ollama embedding) runs fire-and-forget, so the tool no longer times out under concurrent load.

### MCP servers running per agent container

| Server               | Purpose                                                              |
|----------------------|----------------------------------------------------------------------|
| `roboco-flow`        | Intent verbs (give_me_work, i_am_done, claim_review, complete, ...) |
| `roboco-do`          | Content tools (commit, note, say, dm, evidence)                      |
| `roboco-git-readonly`| Read-only git: status, log, diff, branches                           |
| `roboco-optimal`     | RAG: `roboco_ask_mentor`, `roboco_kb_search`                         |
| `roboco-docs`        | Project docs file management (selected roles)                        |

Every verb returns a standardized **Envelope**:
- ok: `{status, task_id, next, evidence?, context_briefing}`
- error: `{error, message, remediate, missing}`

The `next` field tells the agent what to call next; the `remediate` field on errors tells them exactly how to fix and retry. Agents should not guess state — trust the response. The verb runner re-checks the task after each composed atomic action and, on a concurrent mid-verb state change, fails fast with a clean `INVALID_STATE` (re-fetch + re-issue) rather than crashing on a `None` dereference.

## Agent Providers

Agent backends are pluggable. `roboco/llm/providers/` defines an `AgentProvider` lifecycle ABC (`base.py`) and a `ProviderRegistry` keyed by `ModelProvider` (`registry.py`), with `ClaudeCodeProvider` (default) and `GrokCliProvider`. The orchestrator resolves a provider at spawn from the agent's `ModelProvider`; when no dedicated provider is registered it falls back to the built-in Claude Code spawn. `ModelProvider` (`roboco/models/base.py`) is `ANTHROPIC` (default), `GROK`, `LOCAL`, `OLLAMA_CLOUD`, `OPENAI` (reserved). The seam is additive: only `GROK` routes through `GrokCliProvider`; Anthropic / Ollama Cloud / self-hosted spawns are unchanged, and every provider gets the same MCP gateway + tool-manifest wiring by construction.

**Grok runtime.** `GROK` agents run xAI's official `grok` CLI (model `grok-build`) authenticated by a **SuperGrok subscription**, not a metered API key — so a Grok workforce can't stall mid-task on out-of-credits. The host `~/.grok/auth.json` is mounted **read-only** into each agent (`GrokCliProvider._append_grok_auth_mount`; `ROBOCO_HOST_GROK_DIR` is the host mount source, set up once with `grok login`). It reaches parity with the Claude path by construction: same MCP gateway + manifest, per-role tool-removal and git-operation deny rules, a prompt-injection guard on the task prompt, headless tool auto-approval, and per-agent token/cost capture from the grok session store. It covers both one-shot delivery roles and the interactive Intake (Prompter) and Secretary chats (per-turn `grok -p` with session resume).

**Token auto-refresh.** The grok access token has a fixed ~6h server-set TTL and the CLI cannot refresh it headlessly — on an expired token it hangs forever at an interactive login prompt. The orchestrator mints a fresh token from the offline-access refresh token (xAI's OIDC `refresh_token` grant) before expiry and rewrites the shared `auth.json` in place (`roboco/llm/providers/grok_auth.py` `refresh_if_stale`, run once per dispatch tick; the orchestrator's `~/.grok` mount is read-write so it can rewrite it). As a backstop the agent entrypoint runs `python -m roboco.llm.providers.grok_auth --check` and refuses to start (exit 78) on a missing/expired token instead of hanging.

## Self-Healing & Feature Flags

**Self-healing CI loop (default-off).** RoboCo can watch its own repository's CI (a single named workflow) and, on a detected regression, open a fix task that is held out of dispatch until the CEO approves it (it terminates at `awaiting_ceo_approval`), then dispatch it through the normal delivery flow. It is dormant by default and armed by `ROBOCO_SELF_HEAL_ENABLED` plus a second opt-in `ROBOCO_SELF_HEAL_ORIGINATE_ENABLED`; origination is bounded by `ROBOCO_SELF_HEAL_MAX_OPEN_TASKS` / `_MAX_PER_CYCLE` so it can't flood the backlog. It never auto-merges or self-deploys (`roboco/services/self_heal_engine.py`).

**Multi-repo CI-watch (default-off).** The fan-out generalization of self-heal: instead of RoboCo's single own repo, it watches every project the operator opts into (`projects.ci_watch_enabled`, migration 048) and, on a red CI conclusion on that project's default branch, opens one fix task into that project's lifecycle that rides the normal delivery flow (+ PR-review gate) and never auto-merges. It reuses the exact hardened per-project `GitService.get_latest_ci_conclusion` (a missing signal is "unknown", never a false green; per-project errors are isolated and never abort the sweep), and is bounded + deduped per repo by `git_url` (a monorepo's cell-projects share one fix task) with per-cycle / rolling caps. Armed by `ROBOCO_CI_WATCH_ENABLED` (+ `_INTERVAL_SECONDS` / `_MAX_OPEN_TASKS` / `_MAX_PER_CYCLE` / `_DEFAULT_WORKFLOW`) and per-project `ci_watch_enabled` / `ci_watch_workflow`; `MultiProjectCITelemetrySource` (`roboco/services/telemetry/source.py`) + `CiWatchEngine` (`roboco/services/ci_watch_engine.py`) + a dedicated orchestrator `_ci_watch_loop`. The single-repo self-heal loop is untouched.

**Dependency-update bot (default-off).** A per-project engine mirroring the self-heal/CI-watch shape: weekly (default) it probes whether a dependency upgrade would change a project's lockfiles and, if so, opens one "update dependencies" task that rides the normal delivery flow (+ PR-review gate) and never auto-merges. Detection is read-only — `WorkspaceService.dry_upgrade_changes_lockfile` runs the project's `dep_update_command` (e.g. `uv lock --upgrade`) in a throwaway clone of the read clone and diffs the lockfile paths (`dep_update_paths`, or inferred `uv.lock`/`pnpm-lock.yaml`); the read clone is never mutated, nothing is committed/pushed, and a null/failing command originates nothing (fail-safe). A project participates only when `projects.dep_update_command` is set (migration 049); bounded + deduped per `git_url` with per-cycle/rolling caps. Armed by `ROBOCO_DEP_UPDATE_ENABLED` (+ `_INTERVAL_SECONDS` default 604800 / `_MAX_OPEN_TASKS` / `_MAX_PER_CYCLE`); `DepUpdateEngine` (`roboco/services/dep_update_engine.py`) + a dedicated `_dep_update_loop`.

**Gated release manager (default-off).** The autonomy that automates cutting a release up to the decision. A default-off background loop (`ReleaseManagerEngine` + `_release_manager_loop`) runs the deterministic readiness sweep (`ReleaseReadinessService.assess`, `roboco/services/release_readiness.py`) — diff-since-tag → conventional-commit classification → semver bump → version-reference completeness (the missed-ref guard) → CHANGELOG completeness → docs-drift (agent count) → migration single-head → gate state — and, past a threshold (`ROBOCO_RELEASE_MIN_COMMITS`, or any feat/security) with a green gate, originates ONE **release proposal** held for the CEO. The proposal is a `source='release_manager'` task owned by the Secretary, HELD (`confirmed_by_human=False`) and skipped by every dispatcher — acted on only by the CEO-gated routes, never delivered. The CEO approves or rejects-with-changes in the panel (`release-proposal-card.tsx`; `GET/POST /api/release/proposal{,/approve,/reject}`, CEO-only); approval runs the **fail-closed** `ReleaseExecutor` (`roboco/services/release_executor.py`): write the bumps across the canonical set (derived from the previous `chore(release):` commit) + the CHANGELOG entry, run `make quality` (abort before commit on red), commit `chore(release): X.Y.Z` (signed) + push, wait for green release-commit CI (abort before publish on red), then `gh release create vX.Y.Z`. Idempotent (an already-published version is a no-op) and never publishes without the CEO. Correctness is deterministic code, not agent judgment; the only generative step is the CHANGELOG prose, which the CEO reviews. Armed by `ROBOCO_RELEASE_MANAGER_ENABLED` (+ `ROBOCO_RELEASE_MIN_COMMITS` / `_INTERVAL_SECONDS`). Auto-deploy stays out of scope — publishing builds images; deploying to the NAS is the CEO's manual step.

**Organizational memory loop (default-off).** Closes the learn→reuse loop so agents stop cold-respawning blind. Three parts, all gated by `ROBOCO_ORG_MEMORY_ENABLED`: ① **capture** — at task completion `TaskService._completion_learnings_for` distills ONE high-signal lesson (Problem→Approach→Gotcha, ≤120 words) via the local model (`MemoryDistiller`, `roboco/services/memory_distiller.py`) instead of the noisy raw-notes/duration capture (flag-off keeps the legacy capture); journal indexing excludes `is_private` reflections from the shared corpus. ② **retrieve (keystone)** — on claim, `_briefing_for` injects `context_briefing["institutional_memory"]`: top-K (`ROBOCO_ORG_MEMORY_TOP_K`) relevance-floored (`ROBOCO_ORG_MEMORY_MIN_SCORE`) lessons + approved playbooks from a role-shaped query (`EvidenceRepo.similar_memory` over the LEARNINGS + PLAYBOOKS pgvector indexes); below the floor nothing is injected (no briefing bloat). ③ **playbooks** — a first-class curated procedure store: `PlaybookTable` (migration 050), the `PLAYBOOKS` OptimalService index, the `draft_playbook` content verb (delivery roles), Auditor `approve_playbook`/`reject_playbook`/`archive_playbook` curation (approval indexes it), and the panel review queue (`playbook-review-queue.tsx`; `/api/playbooks` Auditor/CEO routes). Distillation runs on the local model only — never a cloud LLM in the hot path; every step is best-effort (a failure never blocks completion or the briefing).

**Sandboxed dev DB/Redis (default-off).** Per-project opt-in (`projects.sandbox_services`, migration 057): when armed (`ROBOCO_SANDBOX_DB_ENABLED`), each opted-in project's agent spawn gets orchestrator-provisioned throwaway `postgres:16-alpine` / `redis:8-alpine` **sibling containers** (random per-sandbox creds, tmpfs pg data dir, memory/cpu-capped, labeled `roboco.sandbox=1`), injected as `ROBOCO_TEST_DB_*` / `ROBOCO_TEST_REDIS_*` **in place of** the legacy prod-creds gate-env injection (`_append_gate_env`, which points agents at RoboCo's own production Postgres under `ROBOCO_TOOLCHAIN_MATCH_ENABLED`) — sandbox replaces, never coexists with, prod creds. Lifetime tracks the agent container 1:1: teardown at every removal path plus an orphan janitor at startup + each reaper tick (grace-windowed so a sweep can't reap a sandbox whose spawn is still mid-flight; the pre-spawn stale-clear likewise spares the just-provisioned sandbox). Provisioning failure refuses the spawn (fail-loud); docker-in-agent stays structurally absent. `SandboxProvisioner` (`roboco/runtime/sandbox.py`), wired in the orchestrator spawn path.

**Feature flags / company-in-a-box.** Env-gated, default-off subsystems toggle from the panel's Settings → Feature Flags card (`panel/src/components/settings/feature-flags-card.tsx`) instead of hand-editing env: web research (`ROBOCO_RESEARCH_ENABLED`), the strategy engine (`ROBOCO_STRATEGY_ENGINE_ENABLED`), pitch provisioning (`ROBOCO_PROVISIONING_*`), external / internal PR review, the agent-runtime toolchain match (`ROBOCO_TOOLCHAIN_MATCH_ENABLED`), the architectural-conventions standard (`ROBOCO_CONVENTIONS_ENABLED`), gateway-health recovery (`ROBOCO_GATEWAY_HEALTH_ENABLED`), multi-repo CI-watch (`ROBOCO_CI_WATCH_ENABLED`), the dependency-update bot (`ROBOCO_DEP_UPDATE_ENABLED`), the gated release manager (`ROBOCO_RELEASE_MANAGER_ENABLED`), the organizational memory loop (`ROBOCO_ORG_MEMORY_ENABLED`), the sandboxed dev DB/Redis (`ROBOCO_SANDBOX_DB_ENABLED`), and the self-heal flags above. A toggle persists in the settings store and takes effect on the next backend restart; an unset flag falls back to its environment / config default.

## Architectural Conventions Standard

**Per-project architectural standard (default-off).** Beyond the `make`-style gates (which check syntax/types/tests, not *where code lives*), each project can carry a repo-canonical `.roboco/conventions.yml` — an architecture map (which definition *kinds* belong in which modules), a toggleable rule set, custom regex rules, and waivers — so an agent cannot land a Pydantic model defined inside a router or a `# noqa` / `# type: ignore`. Placement of a *helper* (any top-level function) only **warns** — too blunt to hard-block; `thin_routes` doesn't count an explicit `db.commit()`; and a small allowlist of unavoidable framework suppressions (ruff `TC001`–`TC003`, pydantic `prop-decorator`) is exempt. Gated by `ROBOCO_CONVENTIONS_ENABLED`; fully inert when off. RoboCo itself ships a canonical `.roboco/conventions.yml`.

**Effective map.** Consumers read the *effective* map — auto-derived defaults (from a repo scan + `BUILTIN_RULES`, excluding `tests/`/`docs/` trees) overlaid by the committed file — so behaviour is identical whether the file is present, absent, or partial. `ConventionsService` (`roboco/services/conventions.py`) builds it, caches it per `(project, HEAD sha)` in `project_conventions_cache` (migration `043`), renders the per-task baseline constraints + the ambient prompt block, and scaffolds/restores the file via a PR (`GitService.open_conventions_pr`). The committed file + scan are read from a dedicated project-level **read clone** the service ensures on demand (`WorkspaceService.ensure_read_clone`, pinned to the default branch's HEAD) — the backfill that makes the standard resolve even for a project created before it existed, with no manual `workspace_path`. The schema lives in `roboco/foundation/policy/conventions/` (pure).

**Validator.** A single Python CLI, `python -m roboco.conventions check --root <repo> --files <a> <b> ...` (`roboco/conventions/`), uses tree-sitter (Python + TypeScript grammars, shipped in the agent image) to classify each changed definition and flag forbidden placements + hygiene + custom-rule matches as JSONL findings, after waiver filtering. Precision over recall (it abstains when uncertain so a `block` gate can't false-positive-strand a task) and fail-loud (a validator that cannot run exits 3 so the gate blocks, never silently passes).

**Threading + enforcement.** The standard reaches the work two ways: an ambient "Architectural Standard" block injected at spawn (`compose_prompt`) and an auto-attached `## Constraints` section on every project task (`TaskService.create`). Enforcement is deterministic: a `block`-level finding refuses `i_am_done` (dev pre-submit) and `pr_pass` (the in-path PR gate) with the offending `file:line` + fix hint; findings also surface in QA's `claim_review` evidence (`convention_findings`). A false positive is relieved by a `waiver` the dev commits in their branch — accountable, reviewed in the PR. The panel's per-project Conventions tab (in the edit-project dialog) shows the map + health and offers Save / Restore.

## MegaTask (sequenced batch intake)

**MegaTask** lets the CEO describe several tasks in one Intake chat and ship them as one collision-aware, sequenced batch — even across projects that don't share a codebase (the motivating case: a SaaS app + its OSS core engine + a framework adapter). It is a **core capability, not a feature flag** (additive + opt-in by nature: proposed only when the CEO asks for several tasks; single-task intake is byte-for-byte unchanged), branded "MegaTask" on every user-facing surface while internal names stay technical (`batch_id`, `SequencingService`).

**The umbrella model.** A MegaTask's identity is a real **umbrella** task — branchless, no PR of its own — over N **root-subtasks**, each a real Main-PM coordination root with its own `project_id`, branch, and PR. Hierarchy: Umbrella (Main PM) → N Root-subtasks (Main PM) → Cell tasks (cell PMs) → Dev subtasks. One extra Main-PM layer on top of the normal model. The umbrella is the single board-review / CEO-approve / Main-PM-coordinate unit, so the batch plugs into the existing coordination-root flow for free (task tree, progress rollup, CEO queue).

**Identity predicate (single source of truth).** `roboco/foundation/policy/batch.py`: `is_batch_umbrella` (`batch_id` set AND `parent_task_id` None), `is_batch_root_subtask` (`batch_id` set AND parented), `is_branchless_coordination` ((no-project AND product) OR umbrella). Every git-exemption site consults it so the umbrella's exemptions can't drift: the orchestrator's `_is_coordination_task`, the claim→in_progress branch gate (`GitContext.is_coordination`), `_ensure_branch_for_task` (returns `""` for an umbrella), and the CEO-reject routing. `submit_root` hard-rejects an umbrella (it assembles no PR); umbrella completion reuses the existing branchless path (`all_subtasks_terminal`, PR waived → escalate to CEO).

**Sequencing.** The pure `SequencingService.analyze(surfaces, cell_of, cell_capacity)` (`roboco/services/sequencing.py`; schema in `roboco/foundation/policy/sequencing/`) turns each draft's collision surface — `intends_to_touch` (globs), `adds_migration`, `touches_shared` — into a dependency DAG + Kahn-layered **waves**: file-overlap serializes (more-important first by `(priority, idx)`), migration-adders chain serially, a shared-surface edit runs after each non-shared task it overlaps (file-overlap-conditioned), independent tasks run in parallel; cell-contention only warns. Correctness lives in code, not agent judgment. The columns `tasks.batch_id` + `intends_to_touch` / `adds_migration` / `touches_shared` are migration **046**.

**Intake + create path.** The intake chat can be scoped to a **MegaTask** (a multi-project picker → `StartLiveRequest.project_ids`); the orchestrator clones each repo (`_clone_intake_scope` / `_slugs_for_project_ids`, the multi-repo machinery products already used). The intake agent proposes the whole batch with one **`propose_batch`** tool call — wired on both runtimes (the Claude SDK driver emits one `batch` stream chunk; the grok `intake_server` POSTs a `batch` relay event). The panel's third intake scope accumulates it into a Review-MegaTask card → `POST /prompter/live/{session}/confirm-batch`. `PrompterService.confirm_live_batch` builds the umbrella + N root-subtasks (via `create_task_from_draft` + a `BatchPlacement`) and wires the analyzer edges through `add_dependency`. The Board route holds the root-subtasks in BACKLOG until `approve_and_start` releases them (`_activate_batch_root_subtasks`); the Main-PM route dispatches wave 0 at once. The Product Owner + Head of Marketing review the whole batch (their identity prompts carry a MegaTask section).

## Services

Core services in `roboco/services/`:

| Service | Purpose |
|---------|---------|
| `TaskService` | Task CRUD and state transitions |
| `WorkSessionService` | Git session management, PR lifecycle |
| `WorkspaceService` | Multi-agent workspace resolution and cloning |
| `ProjectService` | Project/repository management |
| `MessagingService` | Channels, sessions, messages |
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
| `agent-base-image` / `agent-*-image` | Pre-built images spawned per agent | One-shot |
| `orchestrator` | API + agent spawner | Depends on all above |
| `panel` | Next.js control panel (internal, port 3000) | — |
| `nginx` | Reverse proxy fronting panel + orchestrator | — |

### Single Entry Point

`nginx` is the only externally-exposed service. It listens on `localhost:3000` and routes:

- `/api/*` and `/ws/*` → `orchestrator:8000`
- everything else → `panel:3000`

This avoids CORS since the browser sees one origin. The Next.js code uses relative URLs (`/api`, `/ws`) and lets nginx do the dispatch.

### WebSocket streams

The orchestrator exposes WebSocket endpoints under `/ws` (router in `roboco/api/websocket.py`, `ConnectionManager` + `broadcast_*` helpers):

| Endpoint | Purpose |
|----------|---------|
| `/ws/channels/{id}`, `/ws/agents/{id}`, `/ws/sessions/{id}`, `/ws/notifications/{id}` | Per-resource live streams — `/ws/channels` + `/ws/sessions` carry live `message.new` frames (from `EventType.MESSAGE_SENT`), so a session transcript updates without a manual refresh |
| `/ws/system` | Operator/system-wide stream (no per-agent keying) — the rate-limit lifecycle (`RATE_LIMIT_HIT` / `RATE_LIMIT_LIFTED`) and live usage (`USAGE_SNAPSHOT`, pushed to the usage dashboard) |

Server-side events reach these sockets through `roboco/api/websocket_bridge.py`, which subscribes to the `StreamEventBus` and forwards each event to the matching connections. To add a new live event: define an `EventType` (dotted value), publish it to the bus, add a `_handle_*` forwarder in `websocket_bridge`, and consume it on the panel via the `useWebSocket("/<endpoint>", …)` hook — do not stand up a parallel endpoint or client stack. `MESSAGE_SENT` is the worked example: `send_message` publishes it, `_handle_message_event` fans it out to `/ws/sessions/{id}` + `/ws/channels/{id}` as a `message.new` frame, and the panel's `useSessionStream` consumes it.

### Rate limiting & usage

- **Provider rate limits** are tracked in Redis (`RateLimitStateTracker`, `roboco/services/gateway/`). On a provider 429 an agent calls `i_am_blocked(reason="rate_limited")`; the spawn gate then **queues** (never drops) further work for that provider, and a background probe-and-resume loop in the orchestrator clears the limit and revives parked agents when it lifts.
- **Provider overloads** reuse the same park-and-probe break. A persistent model-API overload (HTTP 529 / 500 / 503 — the SDK already retries transient ones) parks the provider exactly like a 429 instead of crash-retrying the agent straight back into the overload and burning tokens; the overload is detected orchestrator-side from the dead container's log markers, and the background loop revives the parked work when it recovers. The same break also catches the **Claude session-limit** 429 (the org's 5-hour usage window): an agent exiting with a 0-token session-limit rejection parks the provider and is auto-revived when the window resets, instead of fleet-wide crash-respawning straight back into the limit. Gated by `ROBOCO_OVERLOAD_BREAK_ENABLED` (default-on).
- **Gateway-health recovery** closes a blind spot in the stale-claim reaper: the heartbeat is bumped only by gateway verbs, so a broken-but-alive agent (a corrupted `/app/.venv` so no gateway tool imports) goes heartbeat-stale yet keeps its container up, and the reaper's live-skip would protect it forever. On a stale-heartbeat live container the reaper now probes the gateway out-of-band (`_probe_gateway_health` → `docker exec` the gateway venv imports) and, once broken past `ROBOCO_GATEWAY_HEALTH_GRACE_SECONDS` (a transient probe miss is tolerated), kills + evicts it (`_maybe_recover_broken_gateway`) so it falls through to release + respawn; healthy or inconclusive probes spare it. Gated by `ROBOCO_GATEWAY_HEALTH_ENABLED` (default-on). It is the third leg beside the shipped bash-guard `/app` block (prevents the self-corruption) and the reaper Docker-liveness fallback (stops over-reaping live containers).
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

The organizational structure, communication matrix, role descriptions, and access-control model are documented inline above and in the user-facing documentation site at **[docs.roboco.tech](https://docs.roboco.tech)** (the `roboco-website` repo — Next.js MDX, the canonical docs site as of the 2026-07-03 docs-site split: `docs/internal/specs/2026-07-03-docs-site-split.md`). This repo's old MkDocs-built user tree is gone; `.github/workflows/docs.yml` now only deploys the committed `docs-redirects/` stubs (meta-refresh + canonical) so every URL the old Pages site published keeps resolving, to docs.roboco.tech. `docs/rag/` remains the agent-facing RAG corpus (never published); `docs/map/` is the agent-facing exhaustive codebase map; `docs/internal/` holds specs and working notes; the old root `usage.md` / `deployment.md` now link straight to docs.roboco.tech.
