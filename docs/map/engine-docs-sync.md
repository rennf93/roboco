# RoboCo Slice Map — `engine-docs-sync`

Slice key: `engine-docs-sync`. Repo root: `/Users/renzof/Documents/GitHub/ZZZ/roboco-master/roboco`. Scope: `roboco/services/docs_sync_engine.py`, the docs-sync touch points in `roboco/services/release_proposal.py` and `roboco/services/task.py`, and the release-version marker in `roboco/foundation/policy/content/markers.py`.

## Purpose

A default-off, release-triggered task-origination engine that keeps the public docs at docs.roboco.tech in sync with what actually ships. On a successful release publish, if `docs_sync_enabled` is on and the `roboco-website` project is registered, the engine opens exactly one PENDING Main-PM planning task per release tag against `roboco-website`. The task brief carries the release's drafted CHANGELOG section plus a pointer to the divergence checklist surfaced by `ReleaseReadinessReport`. The engine never writes docs itself, never starts/approves/merges, and has no background loop — it is invoked synchronously from `ReleaseProposalService.approve()` after the release is already published. Like the other autonomy engines, it is conservative: gate on a default-off flag, dedupe per release version, bound concurrently-open and per-cycle originations, and flush-only (the caller owns the commit).

## Files

| Path | Role | LOC |
|---|---|---|
| `roboco/services/docs_sync_engine.py` | `DocsSyncEngine` and `get_docs_sync_engine` — release-triggered origination of one docs-update task per release tag | 188 |
| `roboco/services/release_proposal.py` | Publish-success seam: `ReleaseProposalService._draft_docs_update` hands the release report to `DocsSyncEngine` best-effort | 18 |
| `roboco/services/task.py` | `DOCS_SYNC_SOURCE` constant and `TaskService.list_open_docs_sync_tasks(version=None)` dedupe query | 31 |
| `roboco/foundation/policy/content/markers.py` | `DOCS_SYNC_RELEASE_VERSION` marker + `get/set_docs_sync_release_version` accessors | 17 |
| `roboco/config.py` | `docs_sync_enabled`, `docs_sync_max_open_tasks`, `docs_sync_max_per_cycle` settings | 25 |
| `tests/integration/services/test_docs_sync_engine.py` | Mocked integration tests for enabled / disabled / missing-project / dedupe / cap paths | 224 |
| `tests/unit/services/test_release_proposal_docs_sync_hook.py` | Unit test that `ReleaseProposalService._draft_docs_update` is called on publish success and swallows engine failures | — |

## Key Symbols

| Name | Kind | File:Line | Responsibility |
|---|---|---|---|
| `_DOCS_PROJECT_SLUG` | constant | `roboco/services/docs_sync_engine.py:50` | The registered project slug that hosts the public docs (`roboco-website`). Engine no-ops with a warning when this project is missing. |
| `_DIVERGENCE_CHECKLIST_POINTER` | constant | `roboco/services/docs_sync_engine.py:53` | Text pointer included in every originated task, telling the assignee to review the release-readiness `docs_drift` gaps. |
| `DocsSyncEngine` | class | `roboco/services/docs_sync_engine.py:61` | Release-triggered docs-update task origination service. |
| `DocsSyncEngine.__init__` | method | `roboco/services/docs_sync_engine.py:66` | Initializes the per-instance `_per_cycle_originated` counter. Reset per engine instance because `release_proposal.py` constructs a fresh engine per publish invocation. |
| `DocsSyncEngine.originate_docs_update` | method | `roboco/services/docs_sync_engine.py:72` | Public entry point: returns the created `TaskTable` or `None` when disabled / missing project / either cap reached / already open for this version. Flushes; caller commits. |
| `DocsSyncEngine._per_cycle_originated` | attribute | `roboco/services/docs_sync_engine.py:70` | Instance counter tracking how many docs-sync tasks this engine has originated. Guarded against `settings.docs_sync_max_per_cycle`. |
| `DocsSyncEngine._already_open_for_version` | method | `roboco/services/docs_sync_engine.py:132` | Dedupe check using `TaskService.list_open_docs_sync_tasks(version=...)` filtered by the `docs_sync_release_version` marker. |
| `DocsSyncEngine._open_task` | method | `roboco/services/docs_sync_engine.py:139` | Builds the `TaskCreateRequest` (Main-PM planning root, `confirmed_by_human=True`, source `docs_sync`) and stamps the release-version marker. |
| `get_docs_sync_engine` | function | `roboco/services/docs_sync_engine.py:185` | Factory constructing a `DocsSyncEngine` bound to a session. |
| `DOCS_SYNC_SOURCE` | constant | `roboco/services/task.py:600` | Source tag value `"docs_sync"` used for dedupe queries and task creation. |
| `list_open_docs_sync_tasks` | method | `roboco/services/task.py:1577` | Returns non-terminal `docs_sync` tasks, optionally scoped to one release version via JSONB marker filtering in SQL. |
| `get_docs_sync_release_version` / `set_docs_sync_release_version` | functions | `roboco/foundation/policy/content/markers.py:412,417` | Read/write the `docs_sync_release_version` marker used for per-release dedupe. |

## Data Flow

`ReleaseProposalService.approve()` runs `ReleaseExecutor.execute(report)` in a background task. When the executor returns `published` or `already_published`, `approve()` marks the proposal `COMPLETED`, flushes, and calls the best-effort post-publish hooks in sequence: `_draft_x_post(report)`, `_draft_video(report)`, `_draft_docs_update(report)`. Each catches `Exception` and logs a warning so a failure in any hook cannot roll back or otherwise affect the already-succeeded release.

`_draft_docs_update` imports `get_docs_sync_engine` locally and calls `originate_docs_update(version=report.proposed_version, changelog=report.drafted_changelog)`. Inside the engine:

1. **Flag gate**: returns `None` immediately if `settings.docs_sync_enabled` is `False`.
2. **Project resolution**: looks up `roboco-website` via `ProjectService.get_by_slug`. If missing, logs a warning and returns `None`.
3. **Rolling cap**: counts all non-terminal `docs_sync` tasks via `list_open_docs_sync_tasks()`; if the count is already at `docs_sync_max_open_tasks` (default 3), logs and returns `None`.
4. **Per-cycle cap**: checks an instance-level counter (`_per_cycle_originated`) against `docs_sync_max_per_cycle` (default 1). If the counter is already at the cap, logs and returns `None`. The counter resets per engine instance; `release_proposal.py` constructs a fresh instance per publish invocation.
5. **Per-release dedupe**: calls `list_open_docs_sync_tasks(version=version)` filtering on the `docs_sync_release_version` JSONB marker in SQL; if any row exists, logs and returns `None`.
6. **Originate**: creates a `TaskCreateRequest` with `source=DOCS_SYNC_SOURCE`, `team=Team.MAIN_PM`, `assigned_to=AGENTS["main-pm"].uuid`, `created_by=AGENTS["system"].uuid`, `task_type=PLANNING`, `nature=TECHNICAL`, `complexity=MEDIUM`, `status=PENDING`, `confirmed_by_human=True`, `project_id=roboco-website.id`. The description includes the release version, the drafted CHANGELOG section, the divergence-checklist pointer, and a note that the task is a Main-PM coordination root ready to decompose and delegate.
7. **Marker stamp**: calls `markers.set_docs_sync_release_version(task, version)` and flushes.

The created task then rides the normal delivery lifecycle: Main PM decomposes it into per-cell subtasks, a dev/documenter implements the docs update, QA verifies, the PR reviewer passes it, and the CEO merges.

## Mermaid

```mermaid
sequenceDiagram
    participant RP as ReleaseProposalService.approve
    participant RE as ReleaseExecutor
    participant DSE as DocsSyncEngine
    participant PS as ProjectService
    participant TS as TaskService
    participant DB as TaskTable

    RP->>RE: execute(report)
    RE-->>RP: status: published
    RP->>RP: task.status = COMPLETED; flush
    RP->>DSE: _draft_docs_update(report)
    DSE->>DSE: settings.docs_sync_enabled?
    alt disabled
        DSE-->>RP: None
    else enabled
        DSE->>PS: get_by_slug("roboco-website")
        alt project missing
            DSE->>DSE: logger.warning
            DSE-->>RP: None
        else project exists
            DSE->>TS: list_open_docs_sync_tasks()
            alt open_count >= max_open_tasks
                DSE-->>RP: None
            else under rolling cap
                DSE->>DSE: _per_cycle_originated >= max_per_cycle?
                alt per-cycle cap reached
                    DSE-->>RP: None
                else under per-cycle cap
                    DSE->>TS: list_open_docs_sync_tasks(version=report.proposed_version)
                    alt already open for version
                        DSE-->>RP: None
                    else new version
                        DSE->>TS: create(TaskCreateRequest, source=docs_sync)
                        TS->>DB: INSERT PENDING Main-PM planning task
                        DSE->>DSE: _per_cycle_originated += 1
                        DSE->>DB: set docs_sync_release_version marker
                        DSE-->>RP: created task
                    end
                end
            end
        end
    end
    RP->>RP: catch Exception: log warning
```

## Logical Tree

```
engine-docs-sync
  DocsSyncEngine (roboco/services/docs_sync_engine.py)
    _DOCS_PROJECT_SLUG = "roboco-website"
    _DIVERGENCE_CHECKLIST_POINTER
    originate_docs_update(version, changelog) -> TaskTable | None
      gate on docs_sync_enabled
      resolve roboco-website project
      check docs_sync_max_open_tasks rolling cap
      check docs_sync_max_per_cycle instance counter
      dedupe per version via list_open_docs_sync_tasks(version)
      _open_task(project_id, version, changelog)
      increment _per_cycle_originated
    _already_open_for_version(task_svc, version) -> bool
    _open_task(task_svc, project_id, version, changelog) -> TaskTable
      create TaskCreateRequest(source=docs_sync, team=MAIN_PM, ...)
      markers.set_docs_sync_release_version(task, version)
      session.flush()
    get_docs_sync_engine(session) -> DocsSyncEngine
  ReleaseProposalService seam (roboco/services/release_proposal.py)
    approve() -> on published/already_published: _draft_docs_update(report)
    _draft_docs_update(report) -> best-effort; catch Exception, log warning
  TaskService support (roboco/services/task.py)
    DOCS_SYNC_SOURCE = "docs_sync"
    list_open_docs_sync_tasks(version=None) -> [TaskTable]
      base filter: source == docs_sync AND status not in (COMPLETED, CANCELLED)
      optional version filter: orchestration_markers["docs_sync_release_version"].astext == version
  Markers (roboco/foundation/policy/content/markers.py)
    DOCS_SYNC_RELEASE_VERSION = "docs_sync_release_version"
    get_docs_sync_release_version(task) -> str | None
    set_docs_sync_release_version(task, version)
  Config (roboco/config.py)
    docs_sync_enabled (default False)
    docs_sync_max_open_tasks (default 3, ge=1)
    docs_sync_max_per_cycle (default 1, ge=1)
```

## Dependencies

- Internal: `roboco.config.settings`, `roboco.foundation.identity` (`AGENTS`), `roboco.foundation.policy.content.markers` (`DOCS_SYNC_RELEASE_VERSION`, `set_docs_sync_release_version`), `roboco.models.base` (`Complexity`, `TaskNature`, `TaskStatus`, `TaskType`, `Team`), `roboco.services.base.BaseService`, `roboco.services.project.get_project_service`, `roboco.services.task` (`DOCS_SYNC_SOURCE`, `TaskCreateRequest`, `TaskService`, `get_task_service`).
- External: `sqlalchemy.ext.asyncio.AsyncSession`.

## Entry Points

| Name | File | Trigger |
|---|---|---|
| `ReleaseProposalService.approve` | `roboco/services/release_proposal.py:82` | CEO panel `POST /api/release/proposal/approve`; runs executor, then calls `_draft_docs_update(report)` on publish success |
| `DocsSyncEngine.originate_docs_update` | `roboco/services/docs_sync_engine.py:72` | Called from `_draft_docs_update`; no background loop or public API route |

## Config Flags

- `ROBOCO_DOCS_SYNC_ENABLED` (`docs_sync_enabled`) — master switch; default `false`. When off the engine is never invoked.
- `ROBOCO_DOCS_SYNC_MAX_OPEN_TASKS` (`docs_sync_max_open_tasks`) — rolling cap on concurrently-open docs-sync tasks; default `3`.
- `ROBOCO_DOCS_SYNC_MAX_PER_CYCLE` (`docs_sync_max_per_cycle`) — max docs-sync tasks originated in one invocation; default `1`. Because a release publish is a single invocation, this bounds it to one task per publish event.

The flag is registered in `roboco/services/settings.py`'s `FEATURE_FLAGS` tuple, so it can be toggled from the panel's Settings → Feature Flags card without editing env.

## Gotchas

- **No background loop.** Unlike `SelfHealEngine`, `CiWatchEngine`, and `DepUpdateEngine`, `DocsSyncEngine` has no orchestrator loop. It only runs as a synchronous post-publish hook inside `ReleaseProposalService.approve()`.
- **Best-effort seam.** `_draft_docs_update` catches `Exception` broadly and logs a warning. An engine failure (e.g., DB rollback, unexpected `TaskService` error) must never affect the already-succeeded release publish or the proposal completion.
- **Requires `roboco-website` registration.** The engine logs a warning and returns `None` when the docs project is not registered. This is a deliberate operator step; the engine does not create the project itself.
- **Per-release dedupe is in SQL.** `list_open_docs_sync_tasks(version=...)` filters by the `docs_sync_release_version` JSONB marker in SQL so the database can use JSONB indexes and avoid hauling every open docs-sync row into Python.
- **Rolling cap counts all open docs-sync tasks, not per version.** If three docs-sync tasks are already open for older releases, a new release publish will not originate a fourth task until one of the older ones closes.
- **The originated task is `confirmed_by_human=True`.** It is ready to start immediately and appears in the Main PM's work queue; it is not held for CEO approval like a release proposal.
- **The docs update still goes through normal gates.** The engine only opens the coordination root; implementation, QA, PR review, and CEO merge happen through the standard lifecycle.

## Drift from CLAUDE.md

- CLAUDE.md does not mention a docs-sync engine; this is a new subsystem.

## Changes Since Baseline

| SHA | Subject | Impact |
|---|---|---|
| af2fb904 | `[687574d2] Add docs-sync engine and release-proposal publish seam` | Introduced `roboco/services/docs_sync_engine.py`, the `release_proposal.py` seam, `DOCS_SYNC_SOURCE`, `list_open_docs_sync_tasks`, the `docs_sync_release_version` marker, and `docs_sync_max_open_tasks` / `docs_sync_max_per_cycle` config caps. |
| db919882 | `[687574d2] Restore task.py safeguards deleted by docs-sync engine commit and filter docs_sync version in SQL` | Restored unrelated `task.py` safeguards accidentally deleted by the first commit and moved the version predicate in `list_open_docs_sync_tasks` into SQL against the JSONB marker. |
| d333dde7 | `[e6e23c1f] Enforce docs_sync_max_per_cycle cap in DocsSyncEngine` | Added a per-instance `_per_cycle_originated` counter and guard so `originate_docs_update` respects `docs_sync_max_per_cycle` in addition to the existing rolling cap. Added `test_per_cycle_cap_is_enforced`. |

## Regression Risks

| Title | File:Line | Claim | Severity |
|---|---|---|---|
| Missing `roboco-website` project silently skips docs updates | `roboco/services/docs_sync_engine.py:80` | By design the engine logs a warning and no-ops; operators must register the docs project before enabling the flag. | low |
| Broad exception catch in `_draft_docs_update` could mask persistent engine failures | `roboco/services/release_proposal.py:242` | Best-effort by design so publish success is never endangered, but a persistent failure would only surface as a log warning. | low |
| Cap/dedupe ordering: open_count is checked before per-version dedupe | `roboco/services/docs_sync_engine.py:97` | Correct: a duplicate for the same version is rejected after the cap check, so the cap is not consumed by duplicates. | low |
| Instance-level `_per_cycle_originated` counter persists across calls on a reused engine | `roboco/services/docs_sync_engine.py:66` | Safe today because `release_proposal.py` constructs a fresh `DocsSyncEngine` per publish invocation. A future refactor that reuses an instance must either create a fresh engine per publish or add an explicit reset. | low |

## Health

The engine is intentionally small and conservative: default-off, no background loop, bounded, deduped, and flush-only. It follows the same safety model as the other autonomy engines (never start/approve/merge/deploy) while staying out of the orchestrator's periodic loops entirely. The main dependency on operator action is registering `roboco-website` as a project before enabling the flag. Health is good.
