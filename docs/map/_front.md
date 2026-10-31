# RoboCo — The Complete Map

> **What this is.** The agent-facing map of the RoboCo codebase. Each subsystem has a slice file in `docs/map/`; `scripts/rebuild_map_concat.py` concatenates this front matter and every slice into `_complete_map.md`. Both are indexed into the knowledge base at startup (`OptimalService.AUTO_INDEX_DIRS`), so RoboCo's agents retrieve these rows when they orient on a task. A slice is built from the same tables: Files (path, role, line count), Key Symbols (name, kind, `file:line`, responsibility), Entry Points, Config Flags, Gotchas and Regression Risks, around prose on purpose and data flow.
>
> **State (2026-10-29).** 37 slices. Every path, symbol and line number in the tables was checked against the code that day. Every Python module under `roboco/` (package `__init__.py` files aside) and every file under `docker/` and `scripts/` (README files aside) has a row, and the migration chain is mapped through 110. Descriptions were checked mechanically (every backticked identifier exists in the repository; every prose `file:line` points inside its file and at the definition it names) and a random sample of the rows whose code had moved was re-read by hand; descriptions are not otherwise machine-checked.
>
> **Kept honest mechanically.** `scripts/verify_codebase_map.py`, run by `make quality`, fails when a table names a path or symbol that no longer exists, and when a Python module under `roboco/`, a file under `docker/` or `scripts/`, or a migration has no row; its message names the slice to add the row to. Line-number, size and Alembic-range drift are warnings, because every code edit shifts them: `make map` rewrites them and rebuilds `_complete_map.md`. The checker does not read prose, so a description can still go stale: fix it by hand when you change the code it describes.
>
> **History.** The map began as a 2026-06-29 audit of the `fd10cc862..HEAD` range. That audit's findings, its git log and the follow-up deltas are kept at the end of this front matter as a record; the current behaviour is in the slices, and each slice's own Regression Risks table is the live risk list.

## Table of Contents

**Chapters (this front matter):**
- System at a Glance
- Data Model
- Task Lifecycle State Machine
- Cross-Cutting Flows
- History: the 2026-06-29 audit and its follow-up deltas

**Slices (concatenated after this front matter, in this order):**

1. `a2a-audit-journal-permissions`: A2A conversations and messages, audit logging, journals, structured notes, message extraction, permissions.
2. `deployment-tooling`: Compose topologies, Dockerfiles and agent images, entrypoints and hooks, config, Makefile, ops scripts.
3. `engines-heal-ciwatch-depupdate`: Default-off engines that open one fix/update task: self-heal, CI-watch, dep-update, plus env-sync and CI run history.
4. `foundation-batch-sequencing`: MegaTask identity predicates and the deterministic collision-sequencing analyzer.
5. `foundation-conventions-identity`: Roles, teams, the agent roster, and the conventions-standard schema.
6. `foundation-lifecycle`: The task state machine, intent verbs, claim and team rules (the lifecycle spec).
7. `foundation-policy-misc`: The other pure policy catalogs: communications, journaling, content models and markers, agent-loop budgets, and more.
8. `gateway-support`: Envelope, claim guards, content actions (do-tools), evidence, role config, rate-limit tracker, quality gate, merge chain.
9. `intake-secretary`: CEO-facing Intake (prompter) and Secretary: drafts, MegaTask batches, directives.
10. `notification`: Notification factory, delivery outbox, dedup, re-escalation backoff.
11. `orchestrator`: AgentOrchestrator and its engine mixins: spawn, dispatch, reaper, park-and-probe, background loops.
12. `org-memory-playbooks`: Organizational memory, playbooks, and the Optimal/RAG brain (index plugins, embedder, indexer).
13. `pr-gate-review`: The PR reviewer's two surfaces: the in-path assembled-PR gate and external/fork PR review.
14. `prompts-roles-taxonomy`: Prompt composition, the role/team/permission taxonomy, the injection guard.
15. `release-manager`: The gated release manager: readiness, proposal, executor, certificate.
16. `runtime-providers`: Agent runtimes and LLM providers (Claude Code, Grok, Gemini, Codex, Kimi, OpenRouter, Nebius, Hummin) and the in-container agent SDK.
17. `vault`: The Obsidian vault projection, intake watcher, janitor and KB engine.
18. `video-engine`: Default-off video authoring, rendering and X/TikTok distribution.
19. `models`: The Pydantic/dataclass domain models.
20. `db-migrations`: Async SQLAlchemy setup, the ORM tables, and the Alembic chain (one row per migration).
21. `api-core-websocket`: The FastAPI app shell, dependencies, middleware, security, WebSocket fan-out.
22. `api-routes-schemas`: Every HTTP route (panel API, agent flow/do gateway, cloud auth) and its schemas.
23. `mcp-servers`: The MCP servers inside agent containers (flow, do, git-readonly, optimal, docs, search, intake, secretary).
24. `choreographer`: The Choreographer: intent verbs composed into atomic task actions, and its per-role mixins.
25. `task-service`: TaskService: CRUD, claim, every status transition, dependencies, completion.
26. `worksession-git`: GitService, work sessions, forge adapters, the GitHub App.
27. `workspace`: Per-agent clones and per-task worktrees.
28. `support-services`: Service base, crypto, event bus, settings and feature flags, model routing, maintenance pause, seed data.
29. `review-findings`: The revision-findings ledger and its producers.
30. `metrics-observability`: Metrics, usage, dashboard, governance report.
31. `conventions-service-validator`: The per-project conventions service, validator and custom rules.
32. `product-strategy-research-pitch`: Projects, products, kanban, strategy, research, pitches, and the Board Programs.
33. `engine-docs-sync`: The release-triggered docs-site sync engine.
34. `panel`: The Next.js control panel.
35. `tests`: The test infrastructure (fixtures, ephemeral DB, e2e smoke).
36. `attestation`: The per-task verification attestation.
37. `decisions`: The Decisions service: pilots, resolver, outcome labelling, corpus export, audit engine, the decisions container.

---

## System at a Glance

```mermaid
graph TB
    CEO["CEO (Human)"]
    Intake["Intake / Prompter<br/>(on-demand, human-only)"]
    Sec["Secretary<br/>(on-demand, human-only)"]
    PRRev["PR Reviewer<br/>(read-only reviewer)"]

    Board["Board"]
    PO["Product Owner"]
    HM["Head of Marketing"]
    Aud["Auditor (silent observer)"]

    MainPM["Main PM<br/>(coordinates all cells)"]
    Float["Floaters<br/>cell-pr-reviewer-2 (backs the cell gates)<br/>devops-1 (infra tasks + infra-file PR co-review, ROBOCO_DEVOPS_ENABLED)"]

    BE["Backend Cell"]
    FE["Frontend Cell"]
    UX["UX/UI Cell"]

    BEDev["2 Devs + QA + PM + Documenter + PR Reviewer"]
    FEDev["2 Devs + QA + PM + Documenter + PR Reviewer"]
    UXDev["2 Devs + QA + PM + Documenter + PR Reviewer"]

    CEO --> Intake
    CEO --> Sec
    CEO --> PRRev
    CEO --> Board
    Board --> PO
    Board --> HM
    Board --> Aud
    Board --> MainPM
    MainPM --> Float
    MainPM --> BE
    MainPM --> FE
    MainPM --> UX
    BE --> BEDev
    FE --> FEDev
    UX --> UXDev

    Agents["Agent containers<br/>(Claude Code default; Grok, Gemini, Codex, Kimi,<br/>OpenRouter, Nebius, Hummin CLIs)"]
    BEDev --> Agents
    FEDev --> Agents
    UXDev --> Agents

    GW["Gateway<br/>Choreographer + MCP servers<br/>(roboco-flow / roboco-do / roboco-git-readonly / roboco-optimal,<br/>role-scoped roboco-docs / roboco-search / playwright,<br/>roboco-intake / roboco-secretary for the live chats)"]
    Agents -->|intent verbs + content tools| GW

    Svcs["Services<br/>TaskService · GitService · WorkSessionService<br/>WorkspaceService · A2A · Notification<br/>Optimal/RAG · Journal · Audit · Permissions · Decisions"]
    GW --> Svcs

    PG[("PostgreSQL + pgvector")]
    Redis[("Redis")]
    Svcs --> PG
    Svcs --> Redis

    Orch["Orchestrator, the dispatcher role<br/>(spawn / reap / park-probe / respawn-tracker / engines)"]
    Orch -->|spawn/reap| Agents
    Orch --> Svcs

    Ollama["Ollama<br/>(qwen3-embedding:0.6b + glm-5.3:cloud)"]
    Svcs -->|embeddings + local LLM| Ollama

    Panel["Next.js Panel"]
    Nginx["nginx :3000"]
    Panel --> Nginx
    Nginx -->|/api/* /ws/* except the fleet routes| API["API role (stateless routes)"]
    Nginx -->|/api/orchestrator/*, /api/secretary/live/*, /api/prompter/live/*| Orch
    API --> Svcs
    Nginx -->|everything else| Panel
```

```mermaid
flowchart TD
  RT["RoboCo"]
  RT --> LC["Lifecycle"]
  LC --> LC1["15 statuses"]
  LC --> LC2["35 intent verbs"]
  LC --> LC3["26 atomic actions"]
  LC --> LC4["spec gates and import validators"]
  RT --> GW["Gateway"]
  GW --> GW1["Choreographer"]
  GW --> GW2["VerbRunner SAVEPOINT"]
  GW --> GW3["MCP servers flow do git optimal docs search intake secretary"]
  GW --> GW4["role manifests"]
  RT --> OR["Orchestrator"]
  OR --> OR1["spawn and reap"]
  OR --> OR2["park-and-probe"]
  OR --> OR3["respawn-tracker durable"]
  OR --> OR4["gateway-health recovery"]
  RT --> SV["Services"]
  SV --> SV1["TaskService"]
  SV --> SV2["GitService"]
  SV --> SV3["WorkSessionService"]
  SV --> SV4["WorkspaceService"]
  SV --> SV6["Notification"]
  SV --> SV7["Optimal RAG"]
  SV --> SV8["Journal Audit Permissions"]
  RT --> EN["Engines"]
  EN --> EN1["self_heal"]
  EN --> EN2["ci_watch"]
  EN --> EN3["dep_update"]
  EN --> EN4["release_manager"]
  EN --> EN5["env_sync and docs_sync"]
  EN --> EN6["Board Programs"]
  EN --> EN7["X, video, vault, Telegram"]
  RT --> DE["Decisions"]
  DE --> DE1["pilots and resolver"]
  DE --> DE2["outcome labels and corpus"]
  RT --> OM["OrgMemory"]
  OM --> OM1["distill learnings"]
  OM --> OM2["briefings injection"]
  OM --> OM3["playbooks"]
  RT --> CO["Comms"]
  CO --> CO2["A2A"]
  CO --> CO3["notifications dedup"]
  RT --> PA["Panel"]
  PA --> PA1["Next.js 16"]
  PA --> PA2["nginx"]
  PA --> PA3["WebSocket streams"]
  RT --> MG["Migrations"]
  MG --> MG1["001 to 110"]
  MG --> MG2["pgvector"]
  RT --> PR["Prompts"]
  PR --> PR1["roles"]
  PR --> PR2["taxonomy"]
  PR --> PR3["guard"]
```

---

## Data Model

Generated from the foreign keys of the 56 ORM tables in `roboco/db/tables.py` (one edge per foreign key, labelled with the referencing column). The RAG chunk tables (`chunks_*`) are created by migrations 030/031 outside the ORM and are not shown. Tables with no foreign key in either direction: `ci_runs`, `company_goals`, `daily_usage_rollups`, `decision_log`, `entire_credentials`, `github_app_credentials`, `indexed_documents`, `member_performance_daily`, `memory_links`, `memory_retrieval_log`, `pitches`, `playbooks`, `rag_index_failures`, `respawn_tracker`, `routing_presets`, `secretary_directives`, `system_settings`, `telegram_credentials`, `tiktok_credentials`, `users`, `vault_seen_notes`, `verb_latency_samples`, `x_credentials`, `x_seen_features`, `x_seen_mentions`. Column-level detail is in the `models` and `db-migrations` slices.

```mermaid
erDiagram
    TASKS ||--o{ A2A_CONVERSATIONS : "task_id"
    A2A_CONVERSATIONS ||--o{ A2A_MESSAGES : "conversation_id"
    A2A_MESSAGES ||--o{ A2A_MESSAGES : "response_to_id"
    TASKS ||--o{ AGENTS : "current_task_id"
    AGENTS ||--o{ AUDIT_LOG : "agent_id"
    TASKS ||--o{ BOARD_PROGRAM_CYCLES : "exploration_task_id"
    ENTIRE_TRAILS ||--o{ ENTIRE_TRAIL_CHANGES : "root_task_id"
    TASK_REVIEW_FINDINGS ||--o{ ENTIRE_TRAIL_FINDINGS : "review_finding_id"
    ENTIRE_TRAILS ||--o{ ENTIRE_TRAIL_OUTBOX : "root_task_id"
    TASKS ||--o{ ENTIRE_TRAILS : "root_task_id"
    TASKS ||--o{ GATEWAY_TRIGGERS : "task_id"
    AGENTS ||--o{ HANDOFFS : "assigned_to"
    TASKS ||--o{ HANDOFFS : "task_id"
    JOURNALS ||--o{ JOURNAL_ENTRIES : "journal_id"
    TASKS ||--o{ JOURNAL_ENTRIES : "task_id"
    AGENTS ||--o{ JOURNALS : "agent_id"
    PROVIDER_CONFIGS ||--o{ MODEL_ASSIGNMENTS : "provider_config_id"
    AGENTS ||--o{ NOTIFICATIONS : "from_agent"
    TASKS ||--o{ NOTIFICATIONS : "related_task_id"
    PRODUCTS ||--o{ PRODUCT_PROJECTS : "product_id"
    PROJECTS ||--o{ PRODUCT_PROJECTS : "project_id"
    AGENTS ||--o{ PRODUCTS : "created_by"
    PROJECTS ||--o{ PROJECT_CONVENTION_FINDINGS : "project_id"
    PROJECTS ||--o{ PROJECT_CONVENTIONS_CACHE : "project_id"
    AGENTS ||--o{ PROJECTS : "created_by"
    PROMPTER_SESSIONS ||--o{ PROMPTER_MESSAGES : "session_id"
    AGENTS ||--o{ PROMPTER_SESSIONS : "agent_id"
    PROJECTS ||--o{ TASK_CELL_PROJECTS : "project_id"
    TASKS ||--o{ TASK_CELL_PROJECTS : "task_id"
    PROMPTER_SESSIONS ||--o{ TASK_DRAFTS : "session_id"
    TASKS ||--o{ TASK_DRAFTS : "task_id"
    TASKS ||--o{ TASK_REVIEW_FINDINGS : "task_id"
    AGENTS ||--o{ TASKS : "active_claimant_id"
    AGENTS ||--o{ TASKS : "assigned_to"
    AGENTS ||--o{ TASKS : "blocker_raised_by"
    AGENTS ||--o{ TASKS : "claimed_by"
    AGENTS ||--o{ TASKS : "created_by"
    AGENTS ||--o{ TASKS : "pre_block_assignee"
    PRODUCTS ||--o{ TASKS : "product_id"
    PROJECTS ||--o{ TASKS : "project_id"
    TASKS ||--o{ TASKS : "parent_task_id"
    WORK_SESSIONS ||--o{ TASKS : "work_session_id"
    AGENT_SPAWN_SESSIONS ||--o{ TOKEN_USAGE_SNAPSHOTS : "agent_spawn_session_id"
    TASKS ||--o{ WAITING_RECORDS : "task_id"
    AGENTS ||--o{ WORK_SESSIONS : "agent_id"
    AGENTS ||--o{ WORK_SESSIONS : "merged_by"
    PROJECTS ||--o{ WORK_SESSIONS : "project_id"
    TASKS ||--o{ WORK_SESSIONS : "task_id"
```

---

## Task Lifecycle State Machine

Generated from `docs/rag/lifecycle/status-transitions.md`, which `make lifecycle` renders from the spec's `_STATUS_TRANSITIONS` in `roboco/foundation/policy/lifecycle.py` (15 statuses, 42 transitions). Each edge is labelled with its action and the roles allowed to take it (`any` means the action itself is not role-gated; the intent verbs that compose it are). Not drawn: `cancel`, which moves every non-terminal status to `cancelled` (cell_pm, main_pm or ceo; from `awaiting_ceo_approval`, ceo only).

```mermaid
stateDiagram-v2
    [*] --> pending: create
    [*] --> backlog: create
    awaiting_ceo_approval --> completed: ceo_approve (ceo)
    awaiting_ceo_approval --> needs_revision: ceo_reject (ceo)
    awaiting_ceo_approval --> pending: ceo_reject_to_pool (ceo)
    awaiting_documentation --> awaiting_pm_review: docs_complete (documenter)
    awaiting_documentation --> claimed: claim (documenter)
    awaiting_pm_review --> awaiting_ceo_approval: escalate_to_ceo (head_marketing, main_pm, product_owner)
    awaiting_pm_review --> completed: complete (cell_pm, main_pm)
    awaiting_pm_review --> needs_revision: request_changes (cell_pm, main_pm)
    awaiting_pr_review --> awaiting_pm_review: pr_pass (devops, pr_reviewer)
    awaiting_pr_review --> claimed: claim (devops, pr_reviewer)
    awaiting_pr_review --> needs_revision: pr_fail (devops, pr_reviewer)
    awaiting_qa --> awaiting_documentation: qa_pass (qa)
    awaiting_qa --> claimed: claim (qa)
    awaiting_qa --> needs_revision: qa_fail (qa)
    backlog --> pending: activate (any)
    blocked --> awaiting_ceo_approval: escalate_to_ceo (head_marketing, main_pm, product_owner)
    blocked --> in_progress: unblock (any)
    blocked --> pending: unblock (any)
    claimed --> in_progress: start (any)
    in_progress --> awaiting_pm_review: submit_pm_review (any)
    in_progress --> awaiting_pr_review: submit_for_review (any)
    in_progress --> blocked: block (any)
    in_progress --> completed: pr_review_done (pr_reviewer)
    in_progress --> paused: pause (any)
    in_progress --> verifying: submit_verification (any)
    needs_revision --> claimed: claim (any)
    paused --> in_progress: resume (any)
    pending --> claimed: claim (any)
    verifying --> awaiting_qa: submit_qa (any)
    completed --> [*]
    cancelled --> [*]
```

---

## Cross-Cutting Flows

### (a) Agent → MCP → Choreographer → Service → Envelope

```mermaid
sequenceDiagram
    participant Agent
    participant MCP as MCP flow_server
    participant Ch as Choreographer
    participant Spec as lifecycle spec
    participant VR as VerbRunner
    participant Svc as TaskService / GitService
    Agent->>MCP: verb(agent_id, task_id, notes)
    MCP->>Ch: intent
    Ch->>Svc: task.get
    Ch->>Ch: _briefing_for (institutional memory)
    Ch->>Spec: can_invoke_intent(role, verb, task, ctx)
    alt rejected
        Ch-->>Agent: Envelope error (remediate, missing, next)
    else allowed
        Ch->>Ch: preflight guards (claim / conventions / behind_base / unchanged_pr)
        Ch->>VR: run_intent(verb)
        VR->>Svc: pre_side_effects (create_root_pr) [outside SAVEPOINT]
        VR->>Svc: session.begin_nested() SAVEPOINT
        loop composes (claim/set_plan/start/submit_qa/...)
            VR->>Svc: atomic action
            Svc-->>VR: updated task (None on concurrent mismatch)
            alt intermediate None
                VR-->>Ch: raise INVALID_STATE
            end
        end
        VR->>Svc: commit savepoint
        VR->>Svc: side_effects (push_branch / create_pr / pr_merge)
        VR-->>Ch: final task
        Ch-->>Agent: Envelope ok(status, next, context_briefing, evidence)
    end
```

### (b) Orchestrator dispatch / reap / park-probe / respawn durability

```mermaid
graph LR
    Tick["dispatch tick"] --> Spawn["spawn_agent"]
    Spawn --> Running["running instance"]
    Running --> Reaper["stalled-claim reaper"]
    Reaper --> Live{"live container?"}
    Live -- yes --> GWHealth{"gateway-health probe<br/>(ROBOCO_GATEWAY_HEALTH_ENABLED)"}
    GWHealth -- broken past grace --> Kill["kill + evict + release claim"]
    GWHealth -- healthy/inconclusive --> Spare["spare"]
    Live -- no --> Release["release claim + respawn"]
    RL["provider 429 / 529 / session-limit"] --> Park["park provider<br/>(ROBOCO_OVERLOAD_BREAK_ENABLED)"]
    Park --> ProbeLoop["background probe-and-resume loop"]
    ProbeLoop --> Revive["revive parked work"]
    Strike["respawn strike"] --> RT["respawn_tracker table (051, durable)"]
    RT --> Restore["restore_respawn_tracker at start()"]
```

### (c) MegaTask sequencing

```mermaid
graph TB
    Umb["MegaTask umbrella<br/>(branchless, Main PM)"] --> Root1["root-subtask #1<br/>(Main PM, branch+PR)"]
    Umb --> RootN["root-subtask #N<br/>(Main PM, branch+PR)"]
    Root1 --> Seq["SequencingService.analyze<br/>(surfaces, cell_of, cell_capacity)"]
    RootN --> Seq
    Seq --> Waves["Kahn-layered waves"]
    Waves --> CellTasks["cell tasks (cell PMs)"]
    CellTasks --> DevSub["dev subtasks"]
    Seq -- "file-overlap" --> Serial["serialize (priority, idx)"]
    Seq -- "migration-adders" --> Chain["chain serially"]
    Seq -- "shared-surface" --> SharedAfter["run after each non-shared overlap"]
    Seq -- "independent" --> Parallel["parallel"]
```

### (d) In-path PR-review gate

```mermaid
stateDiagram-v2
    in_progress --> awaiting_pr_review: submit_for_review (submit_up by a cell PM, submit_root by the Main PM)
    awaiting_pr_review --> claimed: claim (pr_reviewer, devops)
    awaiting_pr_review --> awaiting_pm_review: pr_pass (pr_reviewer, devops; infra-file PRs also need the DevOps verdict when ROBOCO_DEVOPS_ENABLED)
    awaiting_pr_review --> needs_revision: pr_fail (pr_reviewer, devops; findings written to task_review_findings)
    awaiting_pm_review --> completed: complete (cell_pm, main_pm)
    awaiting_pm_review --> awaiting_ceo_approval: escalate_to_ceo (main_pm; a root PR ends here)
    awaiting_ceo_approval --> completed: ceo_approve (CEO merges)
```

### (e) Default-off engines — each originates one held task or artifact into the normal delivery flow (+ PR-review gate), never auto-merges, auto-posts or auto-deploys

```mermaid
graph LR
    SH["self_heal_engine<br/>(ROBOCO_SELF_HEAL_ENABLED)"] -->|HELD PENDING fix task, confirmed_by_human=False| Flow["normal delivery flow"]
    CIW["ci_watch_engine<br/>(ROBOCO_CI_WATCH_ENABLED)"] -->|READY fix task| Flow
    DEP["dep_update_engine<br/>(ROBOCO_DEP_UPDATE_ENABLED)"] -->|READY update task| Flow
    ENV["env_sync_engine<br/>(ROBOCO_ENV_SYNC_ENABLED)"] -->|sync PR + task per conflicted repo, never pushes prod| Flow
    DOCS["docs_sync_engine<br/>(ROBOCO_DOCS_SYNC_ENABLED)"] -->|docs-update task per release| Flow
    RM["release_manager_engine<br/>(ROBOCO_RELEASE_MANAGER_ENABLED)"] -->|release proposal HELD for CEO| CEOGate
    BP["Board Programs<br/>(per-program arming)"] -->|held exploration cycle| CEOGate
    Flow --> Gate["PR-review gate"]
    Gate --> CEOGate["CEO approval (never auto-merge / never auto-deploy)"]
    OM["org_memory loop<br/>(ROBOCO_ORG_MEMORY_ENABLED)"] -.->|distill on complete + inject on claim| Flow
```

---

## History: the 2026-06-29 audit and its follow-up deltas

Everything below this heading is a dated record, kept as written apart from file locations the map checker re-points. It describes the codebase as it was on the date of each part, not as it is now: the slices above it, and each slice's own Regression Risks table, are the current map.

### What's Wrong — Drift & Regression Risks

**Method.** Thirty-one per-slice maps were produced from the `fd10cc862..HEAD` tree. Each slice's `## Regression Risks` and `## Drift from CLAUDE.md` sections were extracted with `grep` (480 + 781 lines of raw evidence), then deduplicated and ranked across slices. The table below consolidates the top ~25 risks, critical/high first; the full per-slice risk tables follow in each slice section. A separate "Verified Regression Risks" chapter (appended by a later step) will adversarially verify the top 12 against the live code.

#### Top Regression Risks (consolidated across 31 slices)

| # | Risk | Slice | File:Line | Claim | Severity |
|---|------|-------|-----------|-------|----------|
| 1 | `ceo_approve` skips work-session close | task-service | `roboco/services/task.py:8420` | `ceo_approve` calls `_remove_task_worktree_on_terminal` but NOT `_close_work_session_for_task` (only `complete()` does). CEO-approved root tasks leave the WorkSession row not marked closed → reporting/session-resolution drift. **FIXED post-snapshot: `536bbb64` added `_close_work_session_for_task(task, reason="ceo approved")` before worktree removal (task.py:5379).** | High |
| 2 | `fail_qa` route depends on unreliable `original_developer` marker | task-service | `roboco/services/task.py:7123` | Fast path reads the marker; if absent falls to `_resolve_revision_dev`. If both miss (no dev work session, e.g. parent-only edit) task is unassigned to pool → a PM may grab a dev task (the original 2026-06-27 loop). | High |
| 3 | `do`/`a2a` any-role token gate | api-routes-schemas | `roboco/api/routes/v1/do.py:43`, `a2a.py:114` | `require_any_authenticated_agent` only verifies HMAC + agent exists; does NOT assert role matches the verb's role family. A QA-signed token could call `do/commit`; a dev could call `a2a` admin paths. Service-layer scope is the sole guard → a missed service check = privilege escape. | High |
| 4 | 422 response echoes secrets | api-routes-schemas / api-core-websocket | `roboco/api/middleware.py:407` (resp body), `:434` | `_scrub_secrets` redacts only the **log** body; the JSON response still contains `body` with the caller's original `git_token`/`api_key`. A 422 returns the secret to the client (and any MITM/log of the response). | High |
| 5 | `pr_merge` project_id scoping assumes non-None | choreographer | `roboco/services/gateway/choreographer/_verb_runner.py:263` | `project_id=task.project_id` — if a coordination/umbrella task reaches `pr_merge` with `project_id=None`, the cross-repo collision guard silently matches nothing or None-keys the scoping; could merge the wrong PR or no-op. | High |
| 6 | `_submit_root_unchanged_pr_guard` / `_submit_up_unchanged_pr_guard` fail open on resolver regression | choreographer | `roboco/services/gateway/choreographer/_impl.py:9707,9758` | Any future break in `_current_pr_head_sha` / `_project_slug_for` / `git.get_pr_head_sha` makes the pr_fail loop-stopper a no-op, re-opening the 2026-06-27 pr_fail re-submit loop silently. | High |
| 7 | Intermediate-None trailing-None contract | choreographer | `_verb_runner.py:89` + `_impl.py:1277,6358` | A verb that forgets the trailing-None guard None-derefs `t.status`; any NEW verb using `run_intent` with a possibly-None last action inherits the trap. **FIXED post-snapshot: `0e7674af` added `if task is not None:` guard before the side_effects loop (_verb_runner.py:107); trailing None now flows out without touching side effects.** | High |
| 8 | `has_cell_projects` threaded incorrectly breaks branchless exemption | foundation-batch-sequencing | `roboco/foundation/policy/batch.py:66` | `is_branchless_coordination` now requires callers to pass `has_cell_projects`. A call site that omits it (defaults False) for a cell-map root will NOT recognize it as branchless and will demand a branch/PR the root cannot supply — wedging that root in the git gate. Any new call site is a landmine. | High |
| 9 | Kanban admin-override drag skips lifecycle | panel | `panel/src/components/kanban/core/kanban-board.tsx:160` | A confirmed override routes through `useUpdateTask` (admin status-override), bypassing the in-band validator. `skippedPreconditions` is precision-over-recall — a careless confirm can complete a task with no PR / QA-bypass / docs-incomplete. | High |
| 10 | `create_all` schema drift — NOT NULL ORM-mapped columns break all DB tests | tests | `tests/conftest.py:182` | Schema built via `Base.metadata.create_all`, not alembic. A post-052 migration adding a NOT NULL ORM-mapped column without `server_default` breaks every `db_session` test. Conftest only backfills migration-006 cols. | High |
| 11 | Cycle-time SQL depends on audit-log event naming | metrics-observability | `roboco/services/metrics.py:554` | A future named audit event whose `to_status` resolves under `event_type='task.'\|\|to_status` could inject zero-length stages or skew dwell averages across every cycle-time/bottleneck panel. | High |
| 12 | Rework cost join on `agent_spawn_sessions.task_id` | metrics-observability | `roboco/services/metrics.py:742` | If spawn sessions stop populating `task_id` (orchestrator regression), rework cost silently drops to $0 — underreported CEO spend. | High |
| 13 | Release Redis mutex TTL shorter than worst-case execute | release-manager | `roboco/services/release_proposal.py:39` | `_RELEASE_LOCK_TTL_SECONDS=3000` (50min) but execute can run clone+gate+CI+publish ≈ 85min. TTL expires mid-execute → a second approve acquires and `rm -rf`s the in-flight clone, corrupting the release. **FIXED post-snapshot: `05616607`+`2759edf7` added a background `_heartbeat_loop` (`_RELEASE_LOCK_HEARTBEAT_SECONDS=60`) that refreshes the TTL while the lock is held; TTL is now a crash-backstop only and cannot expire under a live execute. Fencing token (compare-and-del Lua script) also prevents a stale first-finally from stealing a usurper's lock.** | High |
| 14 | Stream-bus handler failure leaves message pending → duplicate side effects | support-services | `roboco/events/stream_bus.py:338` | ACK only when all handlers succeed; `recover_pending` re-runs idle≥60s messages. Non-idempotent notification handlers can double-fire after a crash/restart. **FIXED post-snapshot: `e4ed970f` added per-`(event.id, handler)` SET-NX idempotency guard (`_run_handler_guarded`): successful handlers set a Redis key that blocks replay; failed handlers clear the key so replay re-runs them. Also added a dead-letter stream for undecodable poison pills and a periodic `_reclaim_loop` (every 60s).** | High |
| 15 | `resolve_for_agent` silently downgrades to Anthropic | support-services | `roboco/services/llm.py:500` | Decrypt failure / unreachable LOCAL / missing assignment all return the legacy Anthropic route instead of raising — a misconfigured Grok/Ollama fleet spawns against Anthropic with only a log warning. | High |
| 16 | LLM model rename breaks cached ollama deployments | deployment-tooling | `docker-compose.yaml:86` | `ollama-init` verify now greps for `glm-5.3` exactly. A NAS volume with only the old `glm-5:cloud` cached (no network) hits FATAL exit and blocks boot until the new model is pulled. | High |
| 17 | Gateway-health over-reap of live containers | orchestrator | `roboco/runtime/engines/dispatch_breaker.py:854` | `_maybe_recover_broken_gateway` kills a live container past `gateway_health_grace_seconds`; a flaky false-broken probe streak could kill a healthy agent mid-long-edit. | Medium-High |
| 18 | Readopt liveness false-positive | orchestrator | `roboco/runtime/engines/reconcile.py:499` | `_readopt_running_agents` registers ACTIVE for any running `roboco-agent-{slug}` container at startup, including a zombie from a prior orchestrator that already released the claim — blocks re-spawn until the stale container is noticed. | Medium |
| 19 | Stalled-claim reaper live-skip blind spot | orchestrator | `roboco/runtime/engines/dispatch_breaker.py:917` | `_should_skip_live_reap` spares any live container that is neither grok-wedged nor gateway-broken; a Claude agent alive but stuck in a non-verb loop keeps its claim forever. | Medium |
| 20 | DB purpose-dedup gated to ack-required types only; `_persist_and_deliver` skips it entirely | messaging-notification | `roboco/services/notification.py:521`, `notification_delivery.py:1934` | Task-handoff notifications (blocker/escalation/ceo-rejection) are not DB-deduped past the 60s Redis window — a retried `i_am_blocked`/`escalate` beyond 60s re-creates an unacked duplicate (the inbox-inflation + i_am_idle soft-block the DB dedup was added to prevent). | Medium |
| 21 | ~~`acknowledge` publishes `NOTIFICATION_ACKED` directly, not via the transactional outbox~~ **STALE CLAIM** (see `docs/map/notification.md`: `acknowledge` defers the event through `defer_bus_publish`, the after_commit outbox `deliver` uses) | messaging-notification | `roboco/services/notification_delivery.py:857` | Same phantom-event class F107 fixed for `deliver`, left unfixed for the ACK path — a rollback after a successful ACK publish emits a phantom ACK. | Medium |
| 22 | A2A legacy notification suppressed by new loop-prone re-fire guard | a2a-audit-journal-permissions | `roboco/services/a2a.py:640` | Since `3aff6e04`, `send_a2a_notification` runs the 60s Redis guard for loop-prone types before creating the notification. A legitimate A2A peer notification re-sent within 60s (real state change, not a respawn loop) can be silently dropped. | Medium |
| 23 | `sync_branch` has no source-status gate — callable on terminal/paused/blocked tasks | foundation-lifecycle | `roboco/foundation/policy/lifecycle.py:1085` | `sync_branch` composes=() and is not in the special-case list, so `can_invoke_intent` only checks role + OWNERSHIP. A dev who owns a COMPLETED/CANCELLED task passes the spec gate; the rebase runs against a finished task's branch. | Medium |
| 24 | `_curate_playbook` explicit `session.commit` before index | gateway-support | `roboco/services/gateway/content_actions.py:1194` | If the caller's session is in `PendingRollbackError` (prior mid-verb failure poisoned it), this commit raises and the whole curation verb 500s instead of a clean envelope. | Medium |
| 25 | Coverage omit list hides orchestrator/git/workspace regressions from the 80% gate | tests | `pyproject.toml:259` | `[tool.coverage.run].omit` excludes `orchestrator.py`, `git.py`, `workspace.py`, `mcp/*`, `agents/*`. A regression in the respawn-tracker upsert, pr_merge cross-repo scoping, or worktree routing will NOT fail `make quality`'s `--cov-fail-under=80`. | Medium |

> Additional notable Medium risks not deduplicated into the table above (see per-slice sections): `archive_playbook` behavior change (gateway-support); notify to prompter/secretary now refused (gateway-support); blocked task now blocks new claims (gateway-support); `apply_escalation` bypasses validator (task-service); branchless `ceo_reject` uses `admin_set_status` (task-service); `revision_count` bump is in audit helper only (task-service); `cancel` cascade swallows role violations (task-service); `_merge_with_retry` 405 → `MergeConflictError` (worksession-git); `close_pull_request` deletes branch on close by default (worksession-git); F123 worktree merge-sync runs in clone root not worktree (worksession-git); 1-cell map silently drops `product_id` (intake-secretary); malformed `project_id` in multi-cell map silently collapses shape (intake-secretary); collision-surface declaration is prompt-only not gate-enforced (prompts-roles-taxonomy); `submit_root` branch-keyed-vs-task_type-keyed prompt assertion (prompts-roles-taxonomy); `resolve_task_project_slug` cell_projects branch `AttributeError` (pr-gate-review); breaker substitution masks fixable rejection (mcp-servers); 404 synthesis assumes every route returns 200 (mcp-servers); intake composer SSE stuck (panel); panel token on live-chat bridges (panel); `/ws/system` ungated while siblings require panel token (api-core-websocket); cross-repo PR collision via `/api/work-sessions/{id}/pr/merge` (api-routes-schemas); orchestrator CEO gate vs release CEO gate divergence (api-routes-schemas); Grok directory mount widens RO exposure (runtime-providers); 6h `expires_at` default can burn the single-use refresh_token (runtime-providers); worktree `.venv` symlink self-heal depends on a later ensure (workspace); `ensure_worktree` reuses existing branch ref without validating base (workspace); `commit_and_push` RuntimeError unhandled by execute (release-manager); Redis outage fully blocks release approval (release-manager); `TranscriptionService` sync callbacks stall flush (support-services); `get_ready_buffers` unbounded growth (support-services); pitch partial-failure orphans GitHub repos (product-strategy-research-pitch); self-heal CEO notification spam (engines); ci_watch multi-workflow monorepo under-count (engines).

#### Drift from CLAUDE.md (consolidated)

| Slice | Drift |
|-------|-------|
| foundation-lifecycle | `BLOCKED -> AWAITING_CEO_APPROVAL` via `escalate_to_ceo` is in the spec but missing from the doc's Role-Based Transitions table. Per-role verb table omits `i_am_idle` (stated only in prose). Doc undersells the enforcement shim (it owns `GitContext`/`validate_git_requirements`/SLA tables, not a pure view). |
| foundation-batch-sequencing | Doc omits the undeclared-surface same-assignee lane fallback (`a957e4fa`), the cell-map branchless shape, `is_valid_batch_shape`, `main_pm_cannot_own_code`, and edge kinds 2–4. |
| foundation-policy-misc | Doc does not mention `VERB_RETRY_LIMITS` / per-verb circuit breaker / `pm_respawn_max_tracing_resets`. Agent learnings role-exclusion lives in `notification_delivery`, not `journaling.py`. |
| foundation-conventions-identity | None material (additive `role_for_slug_or_none` helper). |
| models | `Role`/`Team` are aliases to `foundation.identity` (base.py:21–24), not defined in `base.py` — CLAUDE.md's "Role/Team in agent.py+base.py" is slightly stale. `Task` carries `cell_projects`/`batch_id`/`intends_to_touch`/`adds_migration`/`touches_shared` (task.py:171,217–228) that the "Data Models" prose omits (but the MegaTask section covers). No `AuditEvent` class (it's `AuditEventType`) and no `A2AEnvelope` in models (gateway `Envelope` lives in `services/gateway/`). |
| db-migrations | Doc says "52 migrations 001..052" — correct, but does not mention the two chained 026 files. No factual drift. |
| task-service | None material. |
| worksession-git | Doc undersell: commit header format and gateway merge-path description are documentation-undersell, not behavioral mismatch. |
| workspace | Doc's "fresh claim `git reset --hard`" narrative diverges from the post-F123 worktree model (by design) — doc drift to reconcile. |
| choreographer | Verb table omits `sync_branch` from the developer list (added since baseline). Otherwise matches. |
| pr-gate-review | None material. |
| gateway-support | Auditor surface doc under-states `notify_list`/`notify_get` (additive, consistent with footnote). PM coordinator-skip lives in Choreographer not `claim_guards.py`. |
| orchestrator | None material (well-instrumented). |
| runtime-providers | `ClaudeCodeProvider` is dead reference code; its "default" label in CLAUDE.md is misleading. |
| engines-heal-ciwatch-depupdate | Minor framing: engines consume telemetry via `MultiProjectCITelemetrySource`, not `GitService` directly. Engine does not enforce `awaiting_ceo_approval` itself. |
| release-manager | None material. |
| org-memory-playbooks | None material. |
| notification | None material. |
| a2a-audit-journal-permissions | Doc lists `PermissionsService` (plural); actual class is `PermissionService` (singular). Legacy A2A-protocol path (`create_a2a_notification` / `TASK_ASSIGNED` re-spawn) undocumented. `AuditService.has_recent_tracing_gap` undocumented. |
| conventions-service-validator | None material (all doc claims match code). |
| intake-secretary | None material. |
| product-strategy-research-pitch | None material (slice unchanged). |
| metrics-observability | None material (slice unchanged). |
| support-services | None material (slice unchanged). |
| mcp-servers | Doc's server table is stale: lists 3 servers + omits many tools; intake/secretary/search are agent-facing MCP servers not listed. |
| api-core-websocket | No direct CLAUDE.md contradiction; the stale security docstring lives in `websocket.py` itself (describes old query-param model vs actual HMAC). |
| api-routes-schemas | None material; `post_pr_review` is additive, not contradictory. |
| panel | None material. |
| deployment-tooling | Doc omits panel/nginx from the compose services table; reverses panel/orchestrator build order in prose; `roboco-bootstrap = roboco.bootstrap:cli` console script points at a non-existent symbol; **Configuration section still documents `ROBOCO_LOCAL_LLM_MODEL=glm-5:cloud` while code now defaults to `glm-5.3:cloud`**; documented image set incomplete (grok-prompter/secretary/pr-reviewer images unlisted). |
| tests | None material. |
| prompts-roles-taxonomy | Stale agent count in `base.md` (22 vs CLAUDE.md's 25). |

#### Assessment

The baseline→HEAD diff is unusual: only two commits appear on `master`'s first-parent line (`15effce0` and `3aff6e04`), but they bundle a **+36,653/-4,214 line, 577-file** change that squashes months of per-fix work — F0xx audit gaps, F123 per-task worktrees, sequencing S1/S2/S3, the pr_fail loop-closer, the model rename, the enum-gate fix, and the bash-guard `/app` venv protection. Read against the 2026-06-28 logic-gap audit (140 confirmed gaps, all resolved), the picture is not "the system is broken"; it is "a hardened system that absorbed a massive consolidation pass and, in doing so, opened several new seams."

**What clearly hardened.** The cross-repo PR-number collision that crashed `cell_pm_complete` is fixed with `project_id` scoping. The pr_fail re-submit loop is closed at three layers (head-sha capture, `submit_root`/`submit_up` unchanged-PR guards, a2a to owning PM). The single-active work-session defect is enforced both at the service layer and by migration 047's partial-unique index. The PM-respawn counter is now DB-durable (051) with an upsert race fix. The 60s Redis loop-prone re-fire guard and `VERB_RETRY_LIMITS` circuit breaker tame the notification/respawn storms. F123 per-task worktrees eliminated the coordinator-PM clone clobber and routed commit/conventions/rebase into the worktree. Sequencing S1/S2/S3 + the per-dev lane barrier (`82541077`) close the out-of-order-start wedge. The WS fan-out no longer back-pressures on a slow client, 422 logs no longer leak credentials, and the WS + HTTP panel-token gates close the operator-only invariant.

**What is genuinely new and wrong.** Five gaps appear that did not exist (or were not load-bearing) at the baseline. (1) `ceo_approve` is asymmetric with `complete()`: it removes the worktree but skips work-session close and the full completion hooks, so CEO-approved root tasks leave unclosed sessions and never get code-changes/decision RAG indexing — a reporting and corpus drift. (2) `fail_qa` routing still depends on the unreliable `original_developer` marker with a work-session fallback that has no guarantee a dev session exists; the 2026-06-27 dev-loop it was meant to close can still recur on a parent-only edit. (3) The `do`/`a2a` any-role token gate means the HMAC check never asserts the role matches the verb's role family — privilege escape is one missed service-scope check away. (4) The 422 response body still echoes `git_token`/`api_key` back to the client (`_scrub_secrets` only scrubs the log) — a real secret-leak surface. (5) Gateway-health recovery, while closing a real blind spot, can over-reap a live healthy container on a flaky false-broken probe streak — killing an agent mid-long-edit. None of these are crash bugs on the happy path; all are correctness/privilege/integrity drift that the happy path never exercises.

**Standing landmines the diff did not touch but the diff's blast radius now amplifies.** The enum-parity gate can false-green on an empty/mismatched `roboco` DB; `sa.Enum(create_type=False)` in 001 is a latent no-op on clean re-apply; missing pgvector aborts `init_db`; `has_cell_projects` is a sharp footgun for any new `is_branchless_coordination` call site; the release Redis mutex TTL (50min) is shorter than worst-case execute (~85min), re-opening the `rm -rf`-clone race it was added to prevent; the coverage omit list excludes the very modules that changed most (orchestrator/git/workspace), so a green `make quality` does not mean those hot paths are covered. The single-commit bundling of nearly every panel logic fix means a partial revert can drop several independent fixes at once.

**Verdict.** The system is **not at its prime, but it is not broken either — it is hardened-but-drifting.** The 159-commit-equivalent gap-fill closed more real race conditions and cross-repo collisions than any prior wave, and the core delivery flow (claim → plan → start → submit → QA → PR-gate → PM/CEO review → complete) is structurally sound and well-instrumented. But the consolidation pass introduced a small set of new integrity seams — the `ceo_approve` completion asymmetry, the `fail_qa` routing fragility, the any-role token gate, the 422 secret echo, and the gateway-health over-reap — that are worth fixing before the next deploy, and the standing landmines (enum-parity, pgvector, mutex TTL, coverage omissions) are worth arming against. The CEO's suspicion that recent changes "may have broken the system" is, on the evidence, **partially warranted at the edges and not warranted at the core**: no meltdown-class regression is present, but five correctness/privilege gaps and several standing landmines mean a deploy without addressing them carries real (if non-fatal) risk.

---

<!-- VERIFIED_RISKS: appended by a later step -->

### Appendix: Git Log fd10cc862..HEAD

`git log --oneline fd10cc862c2020b3f639cdb686d427b0198a2441..HEAD` (run in the repository on 2026-06-29):

```
3aff6e04 Chore: Close gaps (#285)
15effce0 Chore: 141 Gaps fill-in (#283)
```

Two commits on `master`'s first-parent line, bundling a **577-file, +36,653/-4,214** diff (`git diff --stat fd10cc862..HEAD`). The substance of the gap-fill is the per-fix commits squashed into `15effce0` (ancestors: `e202ce39`, `250be5c2`, `a957e4fa`, `82541077`, `cf7603f3`, `e52fd05d`, `919aa7e2`, `12621a36`, `9927d248`, `c03e76c4`, `2f322286`, `c34e978f`, `3a4a3fe5`, `53d60da3`, and the F123/F-fix wave). The `feature/metrics-granularity` branch is **NOT deployed**.

> Post-snapshot updates (since 2026-06-29, branch `chore/logical-gaps-element-sweep-fixes` + merged via PR #286 `536bbb64`): logical-gap sweep fixed ceo_approve work-session closure (Risk #1), verb_runner trailing-None side-effect guard (Risk #7), release-mutex heartbeat (Risk #13), and stream-bus idempotency guard + dead-letter (Risk #14). Two new migrations landed: `053_playbook_archived_attr` and `054_a2a_message_skill` (total now 001→054; ORM table count increased from 37 to 38). Chat-subsystem commits (`76ce53e3` MESSAGE_SENT wired end-to-end in websocket_bridge, `0065ecbb` session task_links, `2da72f3f` closed-session/reply_to guard, `77958c1e` read IDOR fixes, `5cb4e85f` secretary SSE hardening, `a1127daf` session-task endpoint fix) landed after the snapshot. A2A-routes hardening: `5bec3ec5` stamped authenticated caller slug as responder (spoof fix) and added PM-only gate on cancel_task. Hotfixes: `cfe725da` worktree clone-root recovery, `00513399` push_branch named-branch, `9faf2763`/`7be10057` VIRTUAL_ENV agent-image strip. Key sweep commits: `ec2e49af` pr_review_claim active_claimant_id, `d8a5bb48` a2a hierarchy gate + skill persist, `e4ed970f` stream-bus (see Risk #14), `0e7674af` verb_runner (see Risk #7), `05616607`+`2759edf7` release executor (see Risk #13), `ef33d56c` lifecycle-enforcement validators, `16b71be8` lifecycle 6-gap fix, `f90565ea` pr_gate MegaTask-root classification, `b49337e7` route-layer force/privileged-field gates, `115061f3` notification_delivery over-fetch fix.

---
### Delta 2026-07-02 — hotfix batch (PR #293 `0f1ed3cc` + local `8e5f84c4`)

Nine live-run fixes, all merged to `master` the same day (commits `81f448bb`, `011158db`, `9d10217c`, `7dff3237`, `1cf24ff1`, `569a6157`, `c1acbd5b`, `298751e6`, `53bb0420`, `4de81d92`, `a260f903`, `caecb816`, `fe9e5589`, `8e5f84c4`):

1. **Delegate MCP tool carries the collision surface** (`81f448bb`, `7dff3237`) — `roboco/mcp/flow_server.py` `delegate` gains `intends_to_touch` / `adds_migration` / `touches_shared` / `depends_on` and forwards them. The `TASK_AT_DELEGATE` gate (`roboco/foundation/policy/task_completeness.py`) required `intends_to_touch` on code delegations but the tool could not send it — fleet-wide `incomplete_input` delegation wall. Parity test locks plan-gate fields ⊆ tool params (`tests/unit/mcp/test_flow_server_delegate_surface_parity.py`).
2. **Assembly-integrity guard accepts squash-merged children** (`011158db`) — `roboco/services/git.py` (~L4035): when `git cherry` reports a child unmerged, a parent-branch commit carrying the child's `[taskid8]` prefix now proves it landed (squash = N patches → one commit, new patch-id). Markerless children stay flagged.
3. **Diff head prefers origin when local is behind** (`9d10217c`) — `roboco/services/git.py` `_resolve_head_ref` (~L4359): both refs exist + local strictly behind (`merge-base --is-ancestor`) → resolve `origin/<branch>`; local-ahead/diverged keep priority. Kills the stale-evidence false `pr_fail` on assembled PRs that advanced on origin.
4. **`GET /api/tasks/summary` + bounded list routes** (`1cf24ff1`, `c1acbd5b`, `298751e6`) — `roboco/api/routes/tasks.py` wires the previously-dead `TaskSummaryResponse` (`roboco/api/schemas/tasks.py`, now + `completed_at` / `board_review_complete`) into a trimmed route (default limit 500, cap 1000); eleven unbounded task list routes gain `limit` params; the `/tasks` status-only branch honors its limit.
5. **Panel request-flood/fat-payload fixes** (`569a6157`, `298751e6`) — `prefetch={false}` on all Links (sidebar, cards, queues), task list + CEO queue consume `/api/tasks/summary` (`panel/src/lib/api/tasks.ts` `TaskSummary`), logo/icon PNGs slimmed (446KB → 7KB), ReactQueryDevtools dev-only (`panel/src/components/providers.tsx`).
6. **Spawn manifest `workspace_path` follows the task's project** (`53bb0420`) — `roboco/runtime/orchestrator.py` `_build_manifest_for_agent(workspace_path=)` + new `_resolve_workspace_cwd`: manifest and container `-w` share one resolver (was hardcoded to the roboco-project workspace for every spawn).
7. **Respawn breaker catches status ping-pong** (`4de81d92`) — `roboco/foundation/policy/agent_loop.py` `pm_respawn_max_revisit_resets: int = 2` + orchestrator `_respawn_status_change_resets`: a never-seen status fully resets strikes; a REVISITED status gets a bounded reset budget (mirrors tracing_resets), after which strikes accrue. `seen_statuses` is in-memory only (rebuilds post-restart, can only under-gate).
8. **Unassigned-QA dispatch no longer pre-claims** (`a260f903`, `caecb816`) — orchestrator (~L10968): the transitioning pre-claim moved `awaiting_qa` → `claimed` before the agent existed, stranding `claim_review`/`pass_review` (both demand `awaiting_qa`). Now matches `_spawn_assigned_qa` / external-PR dispatch: spawn unclaimed, agent self-claims via `claim_review`.
9. **Team-match enforcement armed** (`fe9e5589`, `8e5f84c4`) — `roboco/foundation/policy/lifecycle.py`: `resume`/`unblock`/`activate` flip `needs_team_match=True`; new `_ORG_WIDE_ROLES` exemption (main_pm, CEO, PO, head_marketing, auditor, pr_reviewer) in `_check_team_match(…, role)`. `8e5f84c4` threads `agent_team` through all 27 gateway `Context` construction sites (`choreographer/_impl.py`, `doc.py`, `pr_gate.py`, `pr_review.py`, `qa.py`) so the gate actually receives the team — it had sat in its permissive fallback since shipping.

Slices touched: worksession-git (2, 3), choreographer/gateway-support (9), orchestrator (6, 7, 8), foundation-lifecycle (7, 9), mcp-servers (1), api-routes-schemas (4), panel (5). `docs/map/_complete_map.md` is the pre-delta concatenation — not regenerated.

---
### Delta 2026-07-02 (evening) — leak-fix batch (branch `fix/leak-fix-batch`)

The remaining live-run leak fixes from the S6/fb836f80 postmortems, TDD'd per item:

1. **Spawner attribution** — `roboco/runtime/orchestrator.py` `spawn_agent(spawned_by=)` → `_launch_spawn(spawned_by=)` stamps the dispatching loop's name into `agent.spawned` / `agent.spawn_failed` audit details (`"unspecified"` when absent). All ~29 call sites pass their loop name (orchestrator dispatchers by method name; `bootstrap`, `api.orchestrator.spawn`, `event.auditor_spawn`); `OrchestratorAccessProtocol.spawn_agent` (roboco/models/events.py) gains the kwarg. A whole-package AST sweep test (`tests/unit/runtime/test_spawn_attribution.py`) fails any future caller omitting it. `pyproject.toml` adds PLR0913 to the orchestrator's per-file ignores (spawn contract > 5 params, bundle refused). NOTE found in passing: `_safe_spawn` + `gateway_pre_spawn_check` (orchestrator ~L741/L1932, the trigger_filter dispatch-time cooldown) have NEVER had a caller — flagged to CEO, untouched under the freeze.
2. **Admin-complete merge-or-refuse** — `roboco/api/routes/tasks.py` `_apply_forced_status_override`: `status=completed` without `force` on a task whose work session records `pr_status == "open"` now refuses naming PR number/URL + the stranding consequence (checked before the generic hatch text). New `TaskService.open_pr_ref` (`roboco/services/task.py`, beside `_assert_pr_merged_for_complete`) is the lookup.
3. **Panel CEO-approve** — `panel/src/components/tasks/task-detail/task-header.tsx` `AWAITING_CEO_APPROVAL`: primary action now `ceo-approve` (CeoApproveDialog → `POST /ceo-approve`, notes ≥ 20); `approve-and-merge` only when `task.pr_number` (umbrellas 400'd `NO_PR`).
4. **Live-Redis test leakage killed** — `tests/unit/services/test_self_heal_originate_db.py` wrote `self_heal:notified:*` (2h TTL) and `tests/unit/gateway/test_i_am_blocked_rate_limited.py` wrote a NO-TTL `roboco:rate_limit:anthropic:state` "rate_limited" blob into live localhost Redis on every run (verified live, keys scrubbed). Both now patch `cfg.redis_host/redis_port` (redis_url is computed) to an unreachable port; engines' fail-open paths keep assertions intact. The one-off `test_self_heal_engine` full-run failure was NOT reproduced (adversarial orders, 5× dir loops, green full gate) — this leakage class + the 2026-07-02 corrupted-venv day remain the suspects.
5. **Phase1 smoke mock team** — `tests/integration/test_foundation_phase1_smoke.py` parent mock gains `team="backend"`; the armed team-match gate (delta above) rejected the auto-generated MagicMock team before the asserted `incomplete_input` (the sweep fixed 13 files; this integration file was outside the gateway/foundation/runtime subsets it ran).

6. **Gate green again: ten xenon C-ranks + global test-Redis isolation** — master CI red at the smoke test meant no gate (CI or local) had reached xenon since the team-match sweep; its inline `agent_team=…` kwarg had pushed 9 verb bodies to C(11-12) unseen (`_impl.py` i_am_done/resume/unclaim/submit_up/submit_root/complete/escalate_up/escalate_to_ceo, `qa.py` fail_review). New shared `actor_context_fields()` (`choreographer/_protocol.py`) computes `(actor_slug, agent_team)` once per verb; the admin-complete open-PR check is extracted to `_refuse_unforced_complete_with_open_pr` (routes/tasks.py). `tests/conftest.py` gains the autouse `_no_live_redis` fixture (root fix for the live-Redis leak class; notif_dedup purpose-dedupe keys were a third, self-expiring family).

7. **Dead spawn-cooldown path deleted (CEO-ratified)** — `_safe_spawn` + `gateway_pre_spawn_check` (orchestrator) + `roboco/services/gateway/trigger_filter.py` + both test files + the dead `spawn_cooldown_seconds`/`role_spawn_rate_per_minute` settings. Never called in repo history; superseded by provider parking (in `spawn_agent`), claim guards/reaper, the respawn circuit breaker, and the notification-spawn cooldown. Rule 4 (per-task cooldown) would have queue-stalled every stage handoff if wired. `GatewayTriggerTable` kept inert (drop = future migration decision).

Slices touched: orchestrator (1, 7), api-routes-schemas + taskservice (2), panel (3), choreographer/gateway-support (6, 7), tests (4, 5, 6, 7). `docs/map/_complete_map.md` still not regenerated.

---
### Delta 2026-07-02 (late) — CEO-ratified follow-ups (branch `fix/leak-fix-batch`)

1. **Dead spawn-cooldown path deleted** (see item 7 above; commit `78fa0f6f`).
2. **uv serialization in the Makefile** (`cf5043fe`) — `export UV_NO_SYNC := 1` + `sync` prerequisite on quality/quality-fast/gate: gate recipes never implicitly re-sync the venv (the recurring rich/pip/bandit corruption came from two uv writers racing one `.venv`).
3. **`_api_base()` in git.py** (`13b9c5d4`) — 15 hardcoded `https://api.github.com` PR/merge/branch sites now honor `settings.github_api_base_url` like the CI/open-PR sites already did (GHE/test override fix; enables the smoke harness's fake GitHub).
4. **CI split** (`db6b3088`) — ci.yml (backend `quality` only; FILE name kept — self-heal/ci-watch/release default to it), `panel-ci.yml` (panel job, `panel/**` paths), `e2e-smoke.yml` (new job). Panel-only reds now land on Panel CI, unwatched by the ci.yml-pinned engines.
5. **e2e smoke harness** — `tests/e2e_smoke/{conftest,harness,test_dev_lifecycle}.py` + `make e2e-smoke` (env-gated `ROBOCO_E2E_SMOKE=1`, skipped in the default suite). Harness: real routers/middleware on uvicorn over the ephemeral test DB (`settings.database_*` patched + `_DbHolder` reset), bare origin at `<tmp>/github.com/e2e-smoke/proj.git` (satisfies `_parse_git_url`, clones tokenless), fake GitHub REST router doing real squash-merges via an admin clone, `ScriptedAgent` reloading the real MCP modules per role. Scenario 1 (leaf dev arc → awaiting_pm_review) GREEN in ~5s. Learned seams scripted: post-claim tracing gap keeps the claim; note scopes are decision/learning/note/reflect/struggle (no 'progress'); i_am_done demands during-work+handoff+reflect+per-AC artifacts; pass_review demands learning note + ac_verdicts; A2A resolves roles from the STATIC agents_config registry (seed canonical slugs: be-dev-1/be-qa/be-doc/be-pm/main-pm).

Slices touched: worksession-git (3), orchestrator (1), deployment-tooling (2, 4), tests (5).

---
### Delta 2026-07-02 (night) — wave 1 begins (branch `feat/wave-1`, post-v0.16.0)

1. **PR-gate turn cut** — `orchestrator._try_auto_submit` (+ `_AUTO_SUBMIT_VERB_BY_ROLE`), hooked in `_maybe_spawn_pm_closure` after the all-descendants-terminal check: POSTs the real `submit_up`/`submit_root` through the internal API as the owning PM (X-Agent-ID = task.assigned_to, fallback static `AGENT_UUIDS`); any refusal falls back to the classic PM closure spawn. Flag `ROBOCO_PR_GATE_AUTO_SUBMIT_ENABLED` (config, default on); audit `task.auto_submitted`. Tests: `tests/unit/runtime/test_pr_gate_auto_submit.py` (7) + e2e scenario 2b.
2. **e2e scenarios 2/2b** — `tests/e2e_smoke/arcs.py` (canonical-company seeding — A2A resolves roles from static agents_config so slugs must match; `seed_hierarchy` with task-short-id branch chains; dev/qa/doc/reviewer arcs; `dispatcher_assign` mirroring `_dispatch_pm_review_work`'s claim-for-PM lane since `pr_pass` clears ownership BY DESIGN) + `test_pm_merge_chain.py`. Seams scripted: commit-subject validator ≥20 chars; reviewer learning-note gate before pr_pass; child PR bases on the parent's branch via ancestor resolution.

3. **Trace timestamps** — `content_notes.apply_structured_note` stamps `written_at` into each stored section; panel `tab-notes.tsx` renders it (`FIELD_TO_SECTION` mirror map).
4. **Task search** — `TaskService.search_tasks` (ILIKE title/description + id-prefix) behind `GET /tasks/summary?q=`; panel debounces into the fetch, client title-filter removed. PLR0913 added to the routes per-file ignore (route signatures ARE the HTTP contract).
5. **Secretary task writes** — `control_task` action `edit` (allowlist title/description/acceptance_criteria/priority, `_EDITABLE_TASK_FIELDS`) + `GET /secretary/tasks?q=` name→id resolver (Secretary/CEO). PM-side expansion deliberately deferred (CEO: "PMs not that much").

Slices touched: orchestrator (1), tests (1, 2), taskservice + api-routes-schemas (3, 4, 5), panel (3, 4), secretary (5).

---
### Delta 2026-07-02 (late night) — e2e scenario 3 (branch `feat/wave-1`)

`tests/e2e_smoke/test_root_ceo_chain.py`: 3a pr_fail→needs_revision→`i_will_plan` re-entry (route demands approach≥150 + sub_tasks even on re-claim — pydantic fires before the gateway short-circuit)→real fix commit (`origin_commit` helper)→resubmit→pass→merge; 3b submit_root→gate→complete-escalates→REAL `approve-and-merge` (tasks router now mounted in the harness app; CEO row seeded)→hello.txt on origin master. Seed corrections that ARE the documentation: delivery roots are team=main_pm + planning-typed (backend-team roots get closure-routed to the cell PM; code-typed roots hit the main_pm+code impossibility guard). Fake GitHub `get_pr` now recomputes head.sha live (the unchanged-PR gate reads it via the REST API, not local refs). Latent fix en route: dep-update probe env scrub (VIRTUAL_ENV). uv-rot root cause: shared ~/.cache/uv with long-lived uvx MCP servers — per-repo UV_CACHE_DIR pinned.

---
### Delta 2026-07-03 — wave 2 begins (branch `feat/wave-2`)

1. **Five dead comms panel components deleted** (−422 lines; audit-verified zero consumers; MessageComposer/MessageTypeBadge stay).
2. **e2e scenario 4 (MegaTask umbrella)** — `tests/e2e_smoke/test_megatask_umbrella.py` + arcs helpers (`wire_dependency` via the real sequencing edge, `seed_cell_and_dev`, `set_branch_name`). Proves: sequencing hold (`unmet_dependency` on RS2's i_will_plan), serial root merges to master, umbrella branchless close via ceo-approve (never approve-and-merge).
3. **PRODUCT FIX: batch root-subtask completion wall** — `_main_pm_complete_guard` (_impl.py ~6708) + `escalate_to_ceo` (task.py ~5321) refused ALL parented tasks; both now consult `is_batch_root_subtask`. Live root-subtasks previously closed only via CEO god-mode. Regression tests in test_choreographer_pm.py + test_task_service_transitions.py. First product bug found BY the harness (subagent-built, Sonnet 5, reviewed).

---
### Delta 2026-07-03 (2) — A2A live view (branch `feat/wave-2b`, SDD/Sonnet 5, reviewed)

Backend: `EventType.A2A_MESSAGE_SENT` published from `A2AService.send` (excerpt-capped payload), `websocket_bridge` forwarder → `/ws/system` `a2a.message` frames; admin REST (`/a2a/chat/admin/conversations{,/{id}/messages}`, CEO-only) + CEO reply route via the publish-bearing `send` path. `agents_config.can_a2a_direct`: `from ceo` → allowed to anyone (the one asymmetry); `to ceo` stays blocked at creation. **Reply budget** (`_enforce_ceo_reply_budget` in `send_chat_message`): agent msgs ≥ CEO msgs in the conversation ⇒ reject; three-layer enforcement (matrix block → reply-channel resolve → budget). Panel: `/a2a` page + components, `use-a2a-live` hook over `useWebSocket("/system")`, composer gated on task-linked conversations. v1 seams flagged: legacy raw REST chat sends don't publish the live event; agent→CEO reply lookup matches topic-less conversations only; deduped re-send re-publishes an identical frame (idempotent consumer).

---
### Delta 2026-07-03 (3) — prompter memory (branch `feat/wave-2b`, SDD/Sonnet 5, reviewed)

`TaskService.list_recent_for_project` (recency = coalesce(completed,updated,created)); pure digest builders in `prompter.py` (15 lines/project, 70-char titles, 4000-char total cap); orchestrator `_resolve_history_digest_ambient` (+`_resolve_intake_ambient` merge, best-effort/non-blocking) injected at `_spawn_intake_container` → `_generate_composed_prompt(ambient=…)`; `GET /prompter/live/{id}/search-tasks` (session-liveness = trust boundary, q 2–200, limit ≤10); `query_past_tasks`/`format_search_results` shared by the grok MCP tool AND the Claude SDK in-process tool (full parity, one implementation). FLAGGED pre-existing gap (untouched): `_resolve_conventions_ambient` doesn't cover the MegaTask `project_ids` scope — conventions ambient absent on MegaTask intakes.

---
### Delta 2026-07-03 (4) — switchboard + task access (branch `feat/wave-2c`, SDD/Sonnet 5, reviewed)

1. **Switchboard**: `A2A_ALLOWED_PAIRS` (agents_config, import-time; 70 pairs: 15×3 cells, 6 pm-chain, 3 board, 16 cross), `A2AService.list_admin_pairs` (bulk tuple_ IN join, latest conversation per pair), `GET /a2a/chat/admin/pairs` (CEO-gated); panel `a2a-switchboard{,-utils,-pair-card}` (pure lighting utils, rAF/CSS 45s fade, no timers), /a2a defaults to switchboard w/ v1-list toggle. FLAGGED pre-existing: `agent-utils.ts` static maps miss the per-cell pr-reviewer slugs.
2. **Task access**: Secretary `_EDITABLE_TASK_FIELDS` full content surface + enum coercion + claim-aware reassign (`reassign_active_claim` when claimed/in_progress); `read_task` full detail (progress bounded 50). PM lighter: `_pm_editor_scope`/`_enforce_pm_lighter_fields` in routes/tasks.py — closed the pre-existing PM-unrestricted-admin hole (cross-team cell PM hard-403; content allowlist; zero status via PATCH). submit_directive tool docs never mentioned `edit` (fixed — undiscoverable).

---
### Delta 2026-07-03 (5) — wave 3 → v0.17.0 (branch `feat/wave-3`, SDD/Sonnet 5, reviewed)

Six subsystems, all default-off + additive:
1. **Sandboxed dev DB/Redis/Mongo** (`ROBOCO_SANDBOX_DB_ENABLED`, migration 057 `projects.sandbox_services`): `SandboxProvisioner` (`roboco/runtime/sandbox.py`) `docker run`s throwaway `postgres:16-alpine`/`redis:8-alpine`/`mongo:8` sibling containers per spawn (random creds, tmpfs, labeled), injecting `ROBOCO_TEST_DB_*`/`ROBOCO_TEST_REDIS_*`/`ROBOCO_TEST_MONGO_*` in place of the prod-creds gate-env. The service set is a pluggable engine registry (`roboco/models/sandbox.py`: `SandboxEngine` ABC + `_PostgresEngine`/`_RedisEngine`/`_MongoEngine`, `SANDBOX_ENGINES` / `VALID_SANDBOX_SERVICES` derived from it); adding an engine is one class + one registry line — no provisioner or env-emitter branch. Container-tracked lifetime + orphan janitor (grace-windowed). REVIEW FIX: pre-spawn stale-clear must not tear down the just-provisioned sandbox (`teardown_sandbox=False`) + provision pre-clears stale + janitor grace; `_ensure_image` inspects+pulls (300s) before `docker run` so a NAS cold pull isn't killed at the 20s run deadline.
2. **DB network isolation** (`ROBOCO_DB_NETWORK_ISOLATED`): second `roboco_data` compose bridge = postgres+redis only, orchestrator multi-homed; agents can't reach prod DB. Suppresses `_append_gate_env` prod-creds injection. In BOTH build+registry composes (topology-coupled).
3. **Mobile UI** (panel): `useIsMobile` (useSyncExternalStore, hydration-safe), `ResponsiveTable` table→card, snap `TabsList`, bottom tab bar, Comms/A2A single-pane drill-down below lg, vh→dvh. REVIEW FIX: `justify-center-safe` (overflow clip), memoized matchMedia subscribe.
4. **Cloud auth** (`ROBOCO_CLOUD_AUTH_ENABLED`, migration 058 `users`): FastAPI Users, single seeded user, cookie sliding 30-day session (pwd-fingerprint JWT), `get_agent_context` dual-path (deps.py). Off = byte-identical. `proxy.ts` (Next 16). REVIEW FIX: on-mode rejects EVERY non-CEO role without a token (not just ceo — closed the PM/board :8000 spoof).
5. **X engine** (`ROBOCO_X_ENGINE_ENABLED`, migration 059 Fernet `x_credentials` + `x_seen_mentions`): release-post + mention-reply drafts held for per-post CEO approval; local-model draft, hand-rolled OAuth1 signer, `XPostService.approve` sole `post_tweet` caller. REVIEW FIX: closed a double-post race (in-lock re-read + commit-before-release).
6. **Board roadmap engine** (`ROBOCO_ROADMAP_ENGINE_ENABLED`, marker-backed, no migration): weekly one-shot PO-solo explore → `propose_roadmap` verb (PO-only, 3-7 items) → CEO per-item approve→BACKLOG. `_dispatch_roadmap_exploration` bypasses the two-reviewer board path. REVIEW: builder found+fixed a SQLAlchemy dirty-check bug (deepcopy marker before mutate); I whitelisted `create_task_from_draft` source ({prompter, roadmap}).

Compose: every optional feature armed `:-true` in the NAS composes, OFF in registry; two opt-in exceptions (`CLOUD_AUTH`, `ROUTING_STRICT`) default off (change auth/failure behavior, not capability).

---
### Delta 2026-07-04 — v0.18.0

Three workstreams plus a migration-chain catch-up (all reviewed; all default-off except the doc-only design bar):

1. **Fable mode** (`ROBOCO_FABLE_MODE_ENABLED`, default off) — opus-fable-playbook adoption on the existing model tiers (no new tier; Fable 5 is not a model option). Two levers: **doctrine** — `fable_doctrine_layer()` (`roboco/agents/factories/_base.py:203`) injects the vendored behavioral doctrine (`agents/prompts/doctrine/fable.md`) into `compose_prompt`'s layer tuple right after `base.md`. **Hooks** — `AgentOrchestrator._fable_hook_groups()` (`orchestrator.py:1414`) appends 5 vendored scripts (`docker/scripts/fable-{stop-gate,bash-discipline,honesty-nudge,prompt-nudge,precompact}-hook.sh`) AFTER RoboCo's own per-event hooks in the Claude settings.json; the grok path installs only the honesty-nudge (`write_grok_fable_hooks`/`fable_honesty_nudge_hook_config`, `roboco/llm/providers/grok_cli_config.py:308-345`) since a grok `PreToolUse`/`Stop` hook deny cancels the whole run (verified live) while `PostToolUse` never denies. Off = byte-for-byte unchanged spawn path.
2. **X feature-spotlight** (`ROBOCO_X_FEATURE_SPOTLIGHT_ENABLED`, default off, sub-switch of `x_engine_enabled`) — a third `XEngine` origination path alongside release posts and mention replies. `Orchestrator._x_feature_spotlight_loop` opens one held exploration task for the Head of Marketing every `x_feature_spotlight_interval_seconds` (default 3d); the HoM investigates and calls the new `propose_feature_spotlight` do-verb (HoM-only, `ContentActions.propose_feature_spotlight`, `content_actions.py:1238`, role gate `_FEATURE_SPOTLIGHT_ROLES`), which materializes a held draft (`XEngine.materialize_feature_spotlight`) into the same X Post Queue as release/reply drafts. Migration 061 adds `x_seen_features` (dedup ledger) + `company_goals.brand_voice` (feeds `_voice_guide` for all three draft kinds). **Gap found (static read, not live-verified):** `propose_feature_spotlight` has no wrapper function or `_TOOLS` entry in `roboco/mcp/do_server.py` (unlike `propose_roadmap`, which has both) — `_register_tools()` only registers the intersection of the manifest's granted verbs and `_TOOLS`, so a spawned Head-of-Marketing agent cannot see this tool via MCP today despite the role-config grant.
3. **FE/UX-UI design bar** (doc-only, no flag) — a `## Design bar` section added to `agents/prompts/teams/frontend.md` and `agents/prompts/teams/ux_ui.md` (distilled from `Leonxlnx/taste-skill`, MIT: three tuning dials — DESIGN_VARIANCE/MOTION_INTENSITY/VISUAL_DENSITY — plus typography/spacing/motion rules and an "AI tells to avoid" list), plus a pointer section in `agents/prompts/roles/developer.md` scoping it to frontend/ux_ui tasks only (backend unaffected).
4. **Migration-chain catch-up** — `060_drop_messaging` (the comms-teardown migration: drops `messages`/`session_tasks`/`sessions`/`groups`/`channels` + 4 enum types + `journal_entries.session_id`; A2A is now the sole directed-message channel) had already landed on master but was never appended to this doc. Chain head is now **061** (this doc previously said 059); `roboco/db/tables.py` now carries **38** ORM table classes (verified via direct count), up from the 37 this doc's Purpose section stated (that figure predates 055-061 and was never recomputed).

Slices touched: orchestrator (1, 2), runtime-providers (1), prompts-roles-taxonomy (1, 3), deployment-tooling (1), gateway-support + mcp-servers (2), product-strategy-research-pitch (2, already current from an earlier pass), db-migrations (2, 4).
