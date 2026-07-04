# db-migrations slice

## Purpose
The DB layer is async SQLAlchemy 2.0 over PostgreSQL+asyncpg, with pgvector for the in-house RAG engine. Schema evolution is owned by an Alembic chain (001→059) that runs on every boot via `init_db()`; `Base.metadata.create_all` is no longer the source of truth — migration 017 reconciled the drift the other way. The ORM tables live in one fat module `roboco/db/tables.py` (~2.5k lines, 37 tables).

## Files

| Path | Role |
|------|------|
| `roboco/db/__init__.py` | Re-exports `Base`, session helpers, `bootstrap_database`, table classes. |
| `roboco/db/base.py` | `Base` (DeclarativeBase + naming convention), `get_engine`, `get_session_factory`, `get_db` (FastAPI dep), `get_db_context`, `run_migrations`, `init_db`, `_db_has_*` probes. Stamps a pre-Alembic DB at 001 then upgrades head. |
| `roboco/db/tables.py` | All 37 ORM table classes (single module). |
| `roboco/db/seed.py` | `bootstrap_database()` — runs `init_db` then seeds agents. |
| `alembic/env.py` | Async Alembic env; imports `roboco.db.tables` to register metadata, overrides `sqlalchemy.url` from settings, `compare_type` + `compare_server_default` on. |
| `alembic.ini` | Standard config; `script_location=alembic`, `prepend_sys_path=.`, no URL (set in env.py). |
| `alembic/versions/` | 59 migration files 001..059 (two share number 026 — chained, not a collision). |

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
| 057 | 057_project_sandbox_services.py | `projects.sandbox_services` (ARRAY(String), nullable) — per-project opt-in for the sandboxed per-agent-spawn Postgres/Redis provisioner. |
| 058 | 058_cloud_auth_users.py | `users` table (FastAPI Users schema) — the single seeded CEO login for cloud auth (`ROBOCO_CLOUD_AUTH_ENABLED`, default off). |
| 059 | 059_x_credentials.py | `x_credentials` (singleton Fernet-encrypted OAuth 1.0a secrets) + `x_seen_mentions` (mentions-poll dedup ledger) — the X (Twitter) engine (`ROBOCO_X_ENGINE_ENABLED`, default off). |

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
  054-->055-->056-->057-->058-->059
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
└── X (Twitter) engine
    └── 059 x_credentials (singleton encrypted OAuth 1.0a) + x_seen_mentions (dedup ledger)
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
> Delta 2026-07-03 (v0.17.0, 5 features): `055_spawn_session_turns_tool_calls` (`agent_spawn_sessions.turns`/`.tool_calls`) + `056_member_perf_daily` (`member_performance_daily`) predate this wave but were never appended to this doc; `057_project_sandbox_services` adds `projects.sandbox_services` (sandboxed dev DB/Redis, `ROBOCO_SANDBOX_DB_ENABLED`); `058_cloud_auth_users` adds `users` (`UserTable`, cloud auth, `ROBOCO_CLOUD_AUTH_ENABLED`); `059_x_credentials` adds `x_credentials` (`XCredentialsTable`) + `x_seen_mentions` (`XSeenMentionTable`) (X engine, `ROBOCO_X_ENGINE_ENABLED`). Chain head is now 059.

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

## Health
The chain is linear and complete (001→059), with `init_db` running `upgrade head` on every boot so deployed schemas stay current. The two structural risks are the `sa.Enum(create_type=False)` no-op in 001 (latent on clean re-applies) and the enum-parity gate's dependence on a populated migrated DB. New migrations consistently use the `postgresql.ENUM(create_type=False)` pattern and `ALTER TYPE ... ADD VALUE IF NOT EXISTS` for enum widening, so recent additions are safe.