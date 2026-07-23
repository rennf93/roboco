# db-migrations slice

## Purpose
The DB layer is async SQLAlchemy 2.0 over PostgreSQL+asyncpg, with pgvector for the in-house RAG engine. Schema evolution is owned by an Alembic chain (001→086) that runs on every boot via `init_db()`; `Base.metadata.create_all` is no longer the source of truth — migration 017 reconciled the drift the other way. The ORM tables live in one fat module `roboco/db/tables.py` (~2.5k lines, 38+ tables — not recomputed for this delta, several 077-086 migrations add columns to existing tables rather than new ones).

## Files

| Path | Role |
|------|------|
| `roboco/db/__init__.py` | Re-exports `Base`, session helpers, `bootstrap_database`, table classes. |
| `roboco/db/base.py` | `Base` (DeclarativeBase + naming convention), `get_engine`, `get_session_factory`, `get_db` (FastAPI dep), `get_db_context`, `run_migrations`, `init_db`, `_db_has_*` probes. Stamps a pre-Alembic DB at 001 then upgrades head. |
| `roboco/db/tables.py` | All 37 ORM table classes (single module). |
| `roboco/db/seed.py` | `bootstrap_database()` — runs `init_db` then seeds agents. |
| `alembic/env.py` | Async Alembic env; imports `roboco.db.tables` to register metadata, overrides `sqlalchemy.url` from settings, `compare_type` + `compare_server_default` on. |
| `alembic.ini` | Standard config; `script_location=alembic`, `prepend_sys_path=.`, no URL (set in env.py). |
| `alembic/versions/` | 86 migration files 001..086 (two share number 026 — chained, not a collision). |

## Key Symbols

| Name | Kind | File:Line | Responsibility |
|------|------|-----------|----------------|
| `Base` | class | db/base.py:38 | DeclarativeBase + MetaData naming convention. |
| `get_engine` | fn | db/base.py:46 | Lazy singleton async engine (pool_pre_ping). |
| `get_db` | fn | db/base.py:70 | FastAPI async session dependency. |
| `get_db_context` | fn | db/base.py | Out-of-request async session context. |
| `init_db` | fn | db/base.py:180 | Boot entry: stamp pre-Alembic DB at 001 then `run_migrations`. |
| `run_migrations` | fn | db/base.py:141 | Runs `alembic upgrade head` via `command.upgrade` in a thread. |
| `bootstrap_database` | fn | db/seed.py:282 | `init_db` + seed default agents. |
| `TaskTable` | class | tables.py:157 | Core task entity (largest table, drives lifecycle). |
| `WorkSessionTable` | class | tables.py:798 | Per-claim session; single-active enforced by 047 partial-unique index. |
| `AgentTable` | class | tables.py:95 | Agent identity, role, team, model provider assignment. |
| `ProjectTable` | class | tables.py:475 | Git repo config + CI/watch/dep-update/quality_command/`sandbox_services` (057) cols. |
| `AuditLogTable` | class | tables.py:1940 | Transition journey; `details` JSONB (010); composite query index (045). |
| `AgentSpawnSessionTable` | class | tables.py:2170 | Per-spawn token totals; feeds usage dashboard. |
| `ProjectConventionsCacheTable` | class | tables.py:2442 | Effective conventions map per (project, HEAD sha). |
| `PlaybookTable` | class | tables.py:721 | Curated procedures (draft→approved→indexed). |
| `RespawnTrackerTable` | class | tables.py:1902 | Durable PM-respawn circuit breaker mirror. |
| `TaskCellProjectTable` | class | tables.py:632 | Per-cell project map for a MegaTask root-subtask (052). |
| `WaitingRecordTable` | class | tables.py:1872 | Persisted dispatcher waiting records (restore at start). |
| `IndexedDocumentTable` | class | tables.py:1651 | RAG corpus docs (added to chain by 017). |
| `UserTable` | class | tables.py:2603 | Cloud-auth (FastAPI Users) single seeded CEO login row (058). |
| `XCredentialsTable` | class | tables.py:2650 | Singleton Fernet-encrypted OAuth 1.0a secrets for the X engine (059). |
| `XSeenMentionTable` | class | tables.py:2675 | X mentions-poll dedup ledger, keyed by mention id (059). |
| `XSeenFeatureTable` | class | tables.py:2264 | X feature-spotlight dedup ledger, keyed by feature slug (061). |
| `run_async_migrations` | fn | env.py | Async online migration runner (NullPool). |

## Migration Chain

| Num | File | What it adds/changes |
|-----|------|---------------------|
| 001 | 001_initial_schema.py | All initial tables (agents, tasks, work_sessions, notifications, journals, audit_log, a2a_*); also originally created channels/sessions/messages, later dropped by the comms-teardown migration. |
| 002 | 002_persistence_tables.py | Persistence tables + `NotificationType.APPROVAL`. |
| 003 | 003_blocker_resolver_type.py | `tasks.blocker_resolver_type` + `blockerresolvertype` enum. |
| 004 | 004_provider_routing.py | `provider_configs` + `model_assignments`; `modelprovider`/`assignmentscope` enums (create_type=False). |
| 005 | 005_blocker_raised_by.py | `tasks.blocker_raised_by`. |
| 006 | 006_gateway_columns.py | Gateway cols: claimant lock, heartbeat, pre-block snapshot, AC status, qa evidence flag. |
| 007 | 007_gateway_triggers_table.py | `gateway_triggers` (dispatcher decision log). |
| 008 | 008_align_skills.py | No-op (skills alignment done statically). |
| 009 | 009_enum_reconcile.py | Reconcile every postgres enum with ORM StrEnum (lowercase) + new members. |
| 010 | 010_audit_log_details_jsonb.py | `audit_log.details` JSON→JSONB. |
| 011 | 011_drop_quarantined_state.py | Drop `quarantined` from taskstatus enum (phantom state, audit D15). |
| 012 | 012_align_agentrole_foundation.py | Add agentrole/team enum values foundation declares. |
| 013 | 013_drop_role_enum.py | Drop stray `role` enum (smoke run 2). |
| 014 | 014_drop_pm_approvals.py | Drop unused `tasks.pm_approvals`. |
| 015 | 015_drop_task_execution_outputs.py | Drop unused `execution_log`/`outputs`. |
| 016 | 016_add_products_and_task_product_id.py | `products` + `product_projects`; `tasks.product_id` (team enum create_type=False). |
| 017 | 017_reconcile_orm_schema_drift.py | Add ORM tables/columns the chain never had (e.g. `indexed_documents`). |
| 018 | 018_task_project_id_nullable.py | `tasks.project_id` nullable (board/fan-out tasks carry product_id). |
| 019 | 019_seed_default_providers.py | Idempotent seed of default model providers. |
| 020 | 020_backfill_enum_values.py | Backfill ORM enum values the chain never added. |
| 021 | 021_task_board_review_complete.py | `tasks.board_review_complete` (board-review handoff flag). |
| 022 | 022_default_branch_master.py | Flip `projects.default_branch` default `main`→`master`. |
| 023 | 023_prompter_tracking_columns.py | `tasks.source` + `confirmed_by_human` (prompter origin). |
| 024 | 024_add_prompter_tables.py | `prompter_sessions`, `prompter_messages`, `task_drafts`. |
| 025 | 025_agentrole_prompter.py | Add `prompter` to agentrole enum. |
| 026a | 026_completed_dependency_ids.py | `tasks.completed_dependency_ids`. |
| 026b | 026_token_usage_tables.py | `agent_spawn_sessions` + `token_usage_snapshots` (chained off 026a). |
| 027 | 027_system_settings.py | `system_settings` key-value store. |
| 028 | 028_seed_self_hosted_provider.py | Seed Self-Hosted (Ollama LOCAL) provider row. |
| 029 | 029_project_quality_command.py | `projects.quality_command` (fast pre-submit gate). |
| 030 | 030_rag_chunks_content_schema.py | Align RAG chunk tables with vector-store schema. |
| 031 | 031_rag_chunks_fulltext.py | tsvector + GIN index on every chunk table (hybrid retrieval). |
| 032 | 032_company_goals.py | `company_goals` singleton charter. |
| 033 | 033_pitches.py | `pitches` (Board proposals → auto-provision). |
| 034 | 034_agentrole_secretary.py | Add `secretary` to agentrole enum. |
| 035 | 035_secretary_directives.py | `secretary_directives` (command audit + gate queue). |
| 036 | 036_ac_ids_and_parent_refs.py | Per-criterion AC ids + child→parent AC linkage. |
| 037 | 037_agentrole_pr_reviewer.py | Add `pr_reviewer` to agentrole enum. |
| 038 | 038_modelprovider_grok.py | Add `grok` to modelprovider enum. |
| 039 | 039_seed_grok_provider.py | Seed Grok (xAI) provider row. |
| 040 | 040_awaiting_pr_review.py | Add `awaiting_pr_review` to taskstatus enum (PR-review gate). |
| 041 | 041_structured_content_columns.py | `pr_reviewer_notes`, machine-marker split, structured content cols. |
| 042 | 042_worksession_toolchain.py | `work_sessions` toolchain matching cols. |
| 043 | 043_conventions_cache.py | `project_conventions_cache`. |
| 044 | 044_convention_findings.py | `project_convention_findings` (violations feed). |
| 045 | 045_observability_rework.py | `tasks.revision_count` + audit_log composite query index. |
| 046 | 046_batch_intake.py | `tasks.batch_id` + collision descriptors (intends_to_touch, adds_migration, touches_shared). |
| 047 | 047_ws_single_active.py | Partial-unique index: one ACTIVE work_session per task. |
| 048 | 048_ci_watch_project_cols.py | Per-project CI-watch opt-in cols. |
| 049 | 049_dep_update_project_cols.py | Per-project dep-update bot opt-in cols. |
| 050 | 050_playbooks.py | `playbooks` table (curated procedures). |
| 051 | 051_respawn_tracker.py | `respawn_tracker` (durable PM-respawn counter). |
| 052 | 052_task_cell_projects.py | `task_cell_projects` (per-cell project map for MegaTask root-subtask; reuses team enum create_type=False). |
| 053 | 053_playbook_archived_attr.py | `playbooks.archived_by` (UUID) + `playbooks.archived_at` (DateTime) — distinct retirement attribution; keeps `approved_by`/`approved_at` as approval-only provenance. |
| 054 | 054_a2a_message_skill.py | `a2a_messages.skill` (String 100, nullable) — persists the capability a directed A2A message concerns; was silently dropped on send. |
| 055 | 055_spawn_session_turns_tool_calls.py | `agent_spawn_sessions.turns` + `.tool_calls` (BigInteger, DEFAULT 0) — per-stint LLM iterations + tool invocations for the granular per-member performance metrics. |
| 056 | 056_member_perf_daily.py | `member_performance_daily` — one row per (date, member_kind, agent_slug) scorecard rollup (incl. CEO as `member_kind='ceo'`). |
| 057 | 057_project_sandbox_services.py | `projects.sandbox_services` (ARRAY(String), nullable) — per-project opt-in for the sandboxed per-agent-spawn engine provisioner (postgres / redis / mongo via the `SANDBOX_ENGINES` registry). |
| 058 | 058_cloud_auth_users.py | `users` table (FastAPI Users schema) — the single seeded CEO login for cloud auth (`ROBOCO_CLOUD_AUTH_ENABLED`, default off). |
| 059 | 059_x_credentials.py | `x_credentials` (singleton Fernet-encrypted OAuth 1.0a secrets) + `x_seen_mentions` (mentions-poll dedup ledger) — the X (Twitter) engine (`ROBOCO_X_ENGINE_ENABLED`, default off). |
| 060 | 060_drop_messaging.py | Drops the channels/groups/sessions/session_tasks/messages subsystem (comms teardown — A2A is now the sole directed-message channel): `journal_entries.session_id` column, the 5 tables, and 4 enum types (`messagetype`/`sessionstatus`/`sessionscope`/`channeltype`); one-way (`downgrade()` raises `NotImplementedError`). |
| 061 | 061_x_feature_spotlight.py | `x_seen_features` (feature-spotlight dedup ledger, keyed by feature slug) + `company_goals.brand_voice` (Text, CEO-authored brand-voice sample, feeds `_voice_guide`) — X feature-spotlight (`ROBOCO_X_FEATURE_SPOTLIGHT_ENABLED`, default off, sub-switch of `x_engine_enabled`). |
| 062-072 | *(not yet reflected in this table — pre-existing gap, out of scope for this pass)* | Vault V1/V2, revision-findings ledger, sandbox extensions, and other slices landed migrations in this range; see `alembic/versions/` directly until this table is backfilled. |
| 073 | 073_project_environments.py | `projects.environments` (nullable JSONB) — the per-project ordered environment ladder (`list[{name, branch}]`, index 0 = head rung, index -1 = prod rung) that replaces `default_branch` as the source of truth for a project's PR target and release target. Additive: a null value falls back to a degenerate single-branch ladder synthesized from `default_branch` at read time (`roboco/models/env_branches.py`), so existing projects are unaffected until the CEO declares a real ladder. |
| 074 | 074_telegram_credentials.py | `telegram_credentials` (singleton Fernet-encrypted `bot_token_encrypted` + `chat_id_encrypted`, mirrors `x_credentials`) — the Telegram notifications bridge (`ROBOCO_TELEGRAM_ENABLED`, default off). |
| 075 | 075_company_goals_company_name.py | `company_goals.company_name` (Text, `server_default=""`) — CEO-authored product/company name, mirroring `brand_voice`. Feeds `CompanyGoalsService.resolve_product_name` (project name → this field → the "RoboCo" literal fallback), which `XEngine`/`VideoEngine` both call so release posts/videos stop hardcoding "RoboCo". Additive and inert until the CEO sets it in the Business → Goals editor. |
| 076 | 076_project_git_provider.py | `projects.git_provider` (nullable `String(16)`, not a pg enum — validated at the service layer by `roboco.foundation.policy.forge.validate_project_forge`) — Phase 0 of the forge-providers spec (GitHub + Gitea + GitLab). Null = auto-detect from the `git_url` host (github.com → github; anything else is a registration-time rejection unless the operator sets this column explicitly — the GitHub Enterprise / self-hosted escape hatch). Additive: every existing project keeps resolving to GitHub behavior until GitLab/Gitea providers are set. See `docs/map/worksession-git.md`. |
| 077 | 077_github_app.py | `github_app_credentials` (singleton Fernet-encrypted App id + private key) + `projects.github_installation_id` (BigInteger, nullable) — a project can bind to a GitHub App installation instead of a bare PAT, with PAT fallback on any mint failure. See `docs/map/worksession-git.md`. |
| 078 | 078_project_codegen_command.py | `projects.codegen_command` (String(500), nullable) — per-project command run in the task's worktree right before every push, auto-committing any codegen drift into the same push (RoboCo itself sets `make codegen`). See `docs/map/worksession-git.md`. |
| 079 | 079_notification_backoff.py | `notifications.reescalation_count` / `.last_reescalated_at` / `.reescalation_delivered_count` — the per-notification exponential re-escalation backoff schedule, replacing the prior every-sweep-tick-forever re-escalation. See `docs/map/notification.md`. |
| 080 | 080_task_project_budgets.py | `tasks.budget_usd` (Float, nullable) + `projects.monthly_budget_usd` (Float, nullable) — per-task and per-project cost budgets (`ROBOCO_TASK_BUDGETS_ENABLED`, default off). See `docs/map/orchestrator.md` / `docs/map/gateway-support.md`. |
| 081 | 081_doctrine_version.py | `agent_spawn_sessions.doctrine_version` (String(32), nullable) — stamped at spawn-session finalize from the composed prompt layers, so a golden-task eval cohort's model+doctrine combination (e.g. Fable-mode on vs. off) is durably identifiable after the fact. See `docs/map/tests.md`. |
| 082 | 082_routing_presets.py | `routing_presets` (id, name, `payload` JSONB, timestamps) — named, full-snapshot save/restore of the routing state (mode + every assignment row, AGENT_SLUG pins included). See `docs/map/support-services.md`. |
| 083 | 083_seed_openai_provider.py | Seeds the OpenAI (Codex CLI) provider row — `ModelProvider.OPENAI`. See `docs/map/runtime-providers.md`. |
| 084 | 084_modelprovider_gemini.py | Adds `gemini` to the `modelprovider` postgres enum. |
| 085 | 085_seed_gemini_provider.py | Seeds the Gemini CLI provider row — `ModelProvider.GEMINI`. |
| 086 | 086_enable_gemini_provider.py | `UPDATE provider_configs SET enabled = true` for the Gemini row 085 seeded `enabled=false` — Grok gets force-enabled via its `apply_mode="grok"` write path, but `apply_mode` grew no `"gemini"` case until this same change, so without this migration a Mix-mode assignment to a Gemini model would resolve against a permanently-disabled row and silently fall back to Anthropic (wired end-to-end everywhere except reachable). Codex (083) sidesteps this by seeding `enabled=true` directly. See `docs/map/runtime-providers.md`. |

## Data Flow
On boot, `init_db()` probes for application tables and `alembic_version`; if a pre-Alembic DB exists it stamps it at revision 001, then always runs `run_migrations()` → `alembic upgrade head` (in a thread via `asyncio.to_thread`). `env.py` imports `roboco.db.tables` so `Base.metadata` is fully populated, overrides `sqlalchemy.url` from `settings.database_url`, and runs online with an async NullPool engine. `compare_type` + `compare_server_default` are on so autogenerate drift is detectable. `tables.py` classes are the ORM mapping the migrations build; the domain layer reads them through `roboco/models/` dataclasses, not the tables directly.

## Mermaid

```mermaid
graph LR
  001-->002-->003-->004-->005-->006-->007-->008-->009-->010
  010-->011-->012-->013-->014-->015-->016-->017-->018-->019
  019-->020-->021-->022-->023-->024-->025-->026a-->026b-->027
  027-->028-->029-->030-->031-->032-->033-->034-->035-->036
  036-->037-->038-->039-->040-->041-->042-->043-->044-->045
  045-->046-->047-->048-->049-->050-->051-->052-->053-->054
  054-->055-->056-->057-->058-->059-->060-->061
```

## Logical Tree

```
Migration chain 001..059
├── Initial schema
│   └── 001 initial schema (agents, tasks, work_sessions, notifications, journals, audit_log, a2a_*; also originally channels/sessions/messages, later dropped by the comms-teardown migration)
├── Persistence
│   └── 002 persistence tables + NotificationType.APPROVAL
├── Blocker metadata
│   ├── 003 blocker_resolver_type + blockerresolvertype enum
│   └── 005 blocker_raised_by
├── Provider routing & model assignments
│   ├── 004 provider_configs + model_assignments (modelprovider/assignmentscope enums)
│   ├── 019 seed default model providers
│   ├── 028 seed Self-Hosted (Ollama LOCAL) provider
│   ├── 038 add grok to modelprovider enum
│   └── 039 seed Grok (xAI) provider
├── Gateway
│   ├── 006 gateway columns (claimant lock, heartbeat, pre-block snapshot, AC status, qa evidence)
│   └── 007 gateway_triggers (dispatcher decision log)
├── Enum reconcile / widening
│   ├── 009 reconcile postgres enums with ORM StrEnum
│   ├── 011 drop quarantined from taskstatus enum
│   ├── 012 align agentrole/team enums with foundation
│   ├── 013 drop stray role enum
│   ├── 020 backfill ORM enum values
│   ├── 025 add prompter to agentrole enum
│   ├── 034 add secretary to agentrole enum
│   └── 037 add pr_reviewer to agentrole enum
├── Audit log
│   ├── 010 audit_log.details JSON→JSONB
│   └── 045 tasks.revision_count + audit_log composite query index
├── Cleanup / drops
│   ├── 014 drop unused tasks.pm_approvals
│   ├── 015 drop unused execution_log/outputs
│   └── 008 no-op (skills alignment done statically)
├── Products
│   ├── 016 products + product_projects; tasks.product_id (team enum create_type=False)
│   └── 018 tasks.project_id nullable
├── ORM drift reconcile
│   └── 017 add ORM tables/columns the chain never had (indexed_documents)
├── Board review
│   └── 021 tasks.board_review_complete
├── Project defaults
│   └── 022 flip projects.default_branch default main→master
├── Prompter tracking
│   ├── 023 tasks.source + confirmed_by_human
│   └── 024 prompter_sessions, prompter_messages, task_drafts
├── Dependency / token usage
│   ├── 026a tasks.completed_dependency_ids
│   └── 026b agent_spawn_sessions + token_usage_snapshots (chained off 026a)
├── System settings
│   └── 027 system_settings key-value store
├── Project quality
│   └── 029 projects.quality_command (fast pre-submit gate)
├── RAG
│   ├── 030 align RAG chunk tables with vector-store schema
│   └── 031 tsvector + GIN index on chunk tables (hybrid retrieval)
├── Strategy / provisioning
│   ├── 032 company_goals singleton charter
│   └── 033 pitches (Board proposals → auto-provision)
├── Secretary
│   └── 035 secretary_directives (command audit + gate queue)
├── Acceptance criteria
│   └── 036 per-criterion AC ids + child→parent AC linkage
├── PR review
│   ├── 040 add awaiting_pr_review to taskstatus enum
│   └── 041 pr_reviewer_notes, machine-marker split, structured content cols
├── Worksession toolchain
│   └── 042 work_sessions toolchain matching cols
├── Conventions standard
│   ├── 043 project_conventions_cache
│   └── 044 project_convention_findings (violations feed)
├── MegaTask / batch intake
│   ├── 046 tasks.batch_id + collision descriptors
│   └── 052 task_cell_projects (per-cell project map for MegaTask root-subtask)
├── WorkSession single-active
│   └── 047 partial-unique index: one ACTIVE work_session per task
├── Autonomous maintenance
│   ├── 048 per-project CI-watch opt-in cols
│   └── 049 per-project dep-update bot opt-in cols
├── Organizational memory
│   ├── 050 playbooks table (curated procedures)
│   └── 053 playbooks.archived_by + archived_at (distinct retirement attribution from approval)
├── Orchestrator runtime durability
│   └── 051 respawn_tracker (durable PM-respawn counter)
├── A2A messaging
│   └── 054 a2a_messages.skill (nullable; persists directed-A2A capability context)
├── Per-member performance metrics
│   ├── 055 agent_spawn_sessions.turns + .tool_calls (DEFAULT 0)
│   └── 056 member_performance_daily (per date/member_kind/agent_slug rollup)
├── Sandboxed dev DB/Redis
│   └── 057 projects.sandbox_services (per-project opt-in array)
├── Cloud auth
│   └── 058 users (FastAPI Users; single seeded CEO login)
├── X (Twitter) engine
│   ├── 059 x_credentials (singleton encrypted OAuth 1.0a) + x_seen_mentions (dedup ledger)
│   └── 061 x_seen_features (feature-spotlight dedup ledger) + company_goals.brand_voice
└── Comms teardown
    └── 060 drop channels/groups/sessions/session_tasks/messages + journal_entries.session_id (A2A is now the sole directed-message channel; one-way, no downgrade)
```

## Dependencies
- PostgreSQL 15+ (NULLS NOT DISTINCT) — actually pgvector image on PG 16.
- `pgvector` extension for RAG cosine similarity (`chunks_*` tables, `indexed_documents`).
- `asyncpg` driver; SQLAlchemy 2.0 async.
- Alembic; migrations run on every orchestrator boot.

## Entry Points
- `init_db()` / `run_migrations()` in `roboco/db/base.py` — boot-time `alembic upgrade head`.
- `bootstrap_database()` in `roboco/db/seed.py` — init + seed.
- `alembic upgrade head` (manual, in orchestrator container).
- `conftest` (tests) — ephemeral DB per test; runs migrations or `create_all` depending on PG availability.

## Config Flags
- `ROBOCO_DATABASE_*` (host/port/user/password/name) — `settings.database_url`.
- `ROBOCO_DATABASE_ECHO`, pool size/timeout/recycle.
- No DB-specific feature flag; migrations always run. Feature flags (`ROBOCO_CONVENTIONS_ENABLED`, `ROBOCO_CI_WATCH_ENABLED`, `ROBOCO_DEP_UPDATE_ENABLED`, `ROBOCO_RELEASE_MANAGER_ENABLED`, `ROBOCO_ORG_MEMORY_ENABLED`) gate *use* of tables the migrations already added.

## Gotchas
- **`sa.Enum(create_type=False)` is silently ignored** — the flag only works on `postgresql.ENUM`, not generic `sa.Enum`. Using `sa.Enum` re-emits CREATE TYPE and fails with "type already exists". 001/016/052 carry the live gotcha comment; 004/016 use the correct `postgresql.ENUM(create_type=False)`. 001 itself uses `sa.Enum(..., create_type=False)` in spots — latent on a clean re-apply.
- **Enum-parity gate can false-green** — `make quality` runs `scripts/verify_postgres_enums.py` only against a migrated DB; an empty `roboco` DB (conftest ephemeral) or `|| echo` masking hides drift. Fixed in 957fb522 but the gate is only as good as the DB it points at.
- **016 latent** — the `team` enum member list under create_type=False is the *original* set, not the later-widened set; inert but misleading.
- **Two files numbered 026** — not a collision: `026_token_usage_tables` chains off `026_completed_dependency_ids`. Renaming is risky (breaks down_revision refs).
- **052 reuses the `team` enum** with `create_type=False` correctly — no new enum added; safe.
- **017 reconciled drift the other way** — added ORM tables the chain had missed; `create_all` is no longer authoritative.

## Drift from CLAUDE.md
- CLAUDE.md says "52 migrations 001..052" — now stale; chain is 001..059 (59 files). Does not mention the two 026 files (chained, not a conflict).
- CLAUDE.md cites migrations 043/046/047/048/049/050/051 by number in feature sections — all present and consistent.
- No factual drift found in the DB layer description.

## Changes Since Baseline
`git log fd10cc862c2020b3f639cdb686d427b0198a2441..HEAD -- alembic/ roboco/db/`:
- `15effce0` Chore: 141 Gaps fill-in (#283) — adds migration 052 (`task_cell_projects`) + `TaskCellProjectTable`; logic-touching.

(Only one commit in range touches these paths.)

> Post-snapshot updates (since 2026-06-29): `536bbb64` (Chore/all/logical gaps sweep #286) — adds migration 053 (`playbooks.archived_by`/`archived_at`), two new columns on `PlaybookTable`; `d8a5bb48` ([chore] a2a hierarchy gate + skill persist) — adds migration 054 (`a2a_messages.skill`), one new column on `A2AMessageTable`, wired through `send_chat_message` and the A2AChatMessage model.
>
> Delta 2026-07-03 (v0.17.0, 5 features): `055_spawn_session_turns_tool_calls` (`agent_spawn_sessions.turns`/`.tool_calls`) + `056_member_perf_daily` (`member_performance_daily`) predate this wave but were never appended to this doc; `057_project_sandbox_services` adds `projects.sandbox_services` (sandboxed dev DB/Redis/Mongo, `ROBOCO_SANDBOX_DB_ENABLED`); `058_cloud_auth_users` adds `users` (`UserTable`, cloud auth, `ROBOCO_CLOUD_AUTH_ENABLED`); `059_x_credentials` adds `x_credentials` (`XCredentialsTable`) + `x_seen_mentions` (`XSeenMentionTable`) (X engine, `ROBOCO_X_ENGINE_ENABLED`). Chain head is now 059. Mongo rides existing 057 (no new migration) — it's just another entry in the `SANDBOX_ENGINES` registry.
>
> Delta 2026-07-04 (v0.18.0): `060_drop_messaging` (the comms-teardown migration — drops `messages`/`session_tasks`/`sessions`/`groups`/`channels` + 4 enum types + `journal_entries.session_id`; A2A is now the sole directed-message channel; one-way, `downgrade()` raises `NotImplementedError`) had already landed on master but was never appended to this doc; `061_x_feature_spotlight` adds `x_seen_features` (`XSeenFeatureTable`) + `company_goals.brand_voice` (X feature-spotlight, `ROBOCO_X_FEATURE_SPOTLIGHT_ENABLED`, sub-switch of `x_engine_enabled`). Chain head is now 061. ORM table count is now 38 (verified via `grep -c '^class .*Table' roboco/db/tables.py`), up from this doc's previously-stated 37 (that figure predates 055-061 and was never recomputed).
>
> Delta 2026-07-18/19: `075_company_goals_company_name` adds `company_goals.company_name` (X/video product-branding fallback) and `076_project_git_provider` adds `projects.git_provider` (forge-providers Phase 0 — GitHub/Gitea/GitLab). Chain head is now 076 (062-072 remain the pre-existing table gap noted above — this delta only closes 073-076).
>
> Delta 2026-07-21/23 (10 migrations, 077-086): `077_github_app` (GitHub App credentials + `projects.github_installation_id`), `078_project_codegen_command` (`projects.codegen_command`, auto-regenerate-before-push), `079_notification_backoff` (re-escalation columns replacing the every-tick-forever sweep), `080_task_project_budgets` (`tasks.budget_usd` + `projects.monthly_budget_usd`), `081_doctrine_version` (`agent_spawn_sessions.doctrine_version`, the golden-task eval cohort stamp), `082_routing_presets` (named full-snapshot routing presets), `083_seed_openai_provider` + `084_modelprovider_gemini` + `085_seed_gemini_provider` + `086_enable_gemini_provider` (Codex + Gemini CLI provider rows — see `docs/map/runtime-providers.md`). Chain head is now 086.

## Regression Risks

| Title | File:Line | Claim | Severity |
|-------|-----------|-------|----------|
| sa.Enum create_type silently dropped | alembic/versions/001_initial_schema.py:127,312 | 001 uses `sa.Enum(..., create_type=False)` which is a no-op on the generic Enum; a fresh re-apply on a clean DB can double-emit CREATE TYPE. | High |
| Enum-parity gate false-green | Makefile:540 + scripts/verify_postgres_enums.py | Gate skips on no-migrated-DB; an empty/mismatched `roboco` DB hides postgres-enum drift until a smoke run. | High |
| 016 team enum stale member list | alembic/versions/016_add_products_and_task_product_id.py:38 | `postgresql.ENUM(create_type=False)` member list is frozen at the original set; inert but masks later widening. | Medium |
| Missing pgvector extension blocks RAG | roboco/db/tables.py (chunks_*/indexed_documents) | Migrations assume pgvector installed; on a plain PG the vector columns fail and init_db aborts. | High |
| Two 026 files — rename hazard | alembic/versions/026_*.py | Renaming either 026 file breaks `down_revision` chain; autogenerate may mis-order. | Medium |
| 047 partial-unique index assumes single-active | alembic/versions/047_ws_single_active.py | A duplicate ACTIVE session raises on the partial-unique index; service-layer guard must run first or claim crashes. | Medium |
| 052 reuses team enum — order-dependent | alembic/versions/052_task_cell_projects.py:44 | Depends on `team` enum already existing (from 001/016); a partial chain replay to 052 without 016 would fail. | Low |
| Single-head violation on re-apply | alembic/versions/017_reconcile_orm_schema_drift.py | 017 adds tables/columns that `create_all` had created; on a DB built by `create_all` then stamped, 017 may double-create. | Medium |
| Migration 060 is a one-way removal with no downgrade | alembic/versions/060_drop_messaging.py:57-61 | `downgrade()` raises `NotImplementedError` — recreating channels/groups/sessions/session_tasks/messages + 4 enum types would need the full original schema. Any rollback plan for a bad deploy past 060 must restore from a pre-060 DB backup, not `alembic downgrade`. | Low |

## Health
The chain is linear and complete (001→076), with `init_db` running `upgrade head` on every boot so deployed schemas stay current. The two structural risks are the `sa.Enum(create_type=False)` no-op in 001 (latent on clean re-applies) and the enum-parity gate's dependence on a populated migrated DB. New migrations consistently use the `postgresql.ENUM(create_type=False)` pattern and `ALTER TYPE ... ADD VALUE IF NOT EXISTS` for enum widening, so recent additions are safe.