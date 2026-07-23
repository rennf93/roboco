# Panel Forms ↔ Backend Data-Consistency Audit

**This is a living reference artifact.** Any PR that changes a backend request schema touched by a form listed below (`ProjectCreateRequest`/`ProjectUpdateRequest` in `roboco/api/schemas/project.py`, `TaskCreate` in `roboco/models/task.py`, the route-level `TaskUpdate` in `roboco/api/schemas/tasks.py`, or a writable settings key in `roboco/services/settings.py`) must update the matching row in this table in the same PR. A stale row here is worse than no row — it tells the next developer a mismatch was checked when it wasn't.

Audited 2026-07-23 directly against the current codebase (v0.26.0) on branch `feature/frontend/73275ff0--170c9578--515697f4` — every verdict below was checked by reading the live dialog component next to the live backend schema, not assumed from the intake's original description. Several fields the intake named as "missing" turned out to already be shipped (by unrelated feature work — the env-branches ladder, forge providers, and task/project cost budgets initiatives all landed after this audit's premise was written); this table reflects what is actually true today.

## Verdict legend

| Verdict | Meaning |
|---|---|
| `ok` | Frontend field/control matches the backend schema and its constraints. |
| `missing` | The backend schema accepts this field but no form control exists for it. |
| `stale` | A label, placeholder, help text, or default no longer matches actual behavior. |
| `validation-mismatch` | The form validates (or fails to validate) differently than the backend will. |
| `n/a` | Deliberately absent by design (system-managed field, role-gated note field, or lifecycle-governed elsewhere). |

---

## Settings page (Stream1-C — this task)

Form: `panel/src/app/(dashboard)/settings/page.tsx`. Backend: `roboco/services/settings.py` (`SettingsService`, `_VALIDATORS`) plus `panel/src/store/ui-store.ts` (`useUIStore`, client-only, zustand `persist`).

| Field | Verdict | Notes |
|---|---|---|
| Enable Notifications | `ok` (fixed) | Was already migrated to client-only `useUIStore` persistence (localStorage, immediate-apply, no Save button) by a prior fix — see "History" below. This task adds the one piece that was still missing: a confirmation toast on every change. |
| Sound Alerts | `ok` (fixed) | Same as above; also client-disabled while Notifications is off. |
| Auto Refresh | `ok` (fixed) | Same as above. |
| Refresh Interval | `ok` (fixed) | Same as above; toast now names the new interval. |
| Theme | `n/a` | Client-only (`next-themes`), never sent to a server. |
| Collapsed Sidebar | `n/a` | Client-only (`useUIStore`), never sent to a server. |
| Retention window (days) | `ok` | `TranscriptRetentionCard` uses the server-persisted `settingsApi` pattern — this is the one setting that genuinely round-trips to the backend. |
| Feature flag switches | `ok` | `FeatureFlagsCard` writes immediately per-flag with toast feedback. |

### History (why these four fields are client-only, not server-persisted)

An earlier fix discovered that `roboco/services/settings.py`'s `_VALIDATORS` allowlist never contained `notifications_enabled`/`sound_enabled`/`auto_refresh`/`refresh_interval` — every save 422'd, and nothing consumed the values anywhere (no auto-refresh timer, no notification toast, no sound). Rather than widening the backend allowlist, that fix moved the four prefs into `useUIStore` (client-only, same idiom as theme/sidebar) and **deliberately left the backend allowlist untouched** ("the backend allowlist stays strict and untouched" — see the CHANGELOG entry "Settings preferences persist as real client prefs instead of 422-ing as theater"). It also built `AutoRefreshDriver` (ticks the page-refresh registry when Auto Refresh is on) and `NotificationAlerts` (toasts + optional chime on new WS notifications), so the prefs are now both persisted and actually consumed. This task's own acceptance criterion ("read/write through settingsApi") predates that fix and is superseded by it; adding the four keys back to `_VALIDATORS` would be a regression against a documented, deliberate decision, so this task does not touch `roboco/services/settings.py`.

---

## Project dialogs (Stream1-A)

Forms: `panel/src/components/projects/create-project-dialog.tsx` (backend: `ProjectCreateRequest`) and `edit-project-dialog.tsx` (backend: `ProjectUpdateRequest`), both in `roboco/api/schemas/project.py`.

Every field the intake's Stream1-A unit named as missing — `codegen_command`, `git_provider`, `github_installation_id`, `environments`, `monthly_budget_usd`, `sandbox_extensions` — is now present in both the backend schema and the corresponding dialog, shipped by unrelated feature work (the env-branches ladder, forge-provider, and sandbox-extensions initiatives) that landed after Stream1-A was scoped. Stream1-A's field-sync objective is already fully met.

| Field | Create dialog | Edit dialog | Verdict | Notes |
|---|---|---|---|---|
| `name` | ok | ok | `ok` | |
| `slug` | ok (create-only, immutable) | read-only display | `ok` (fixed help text) | Help text said "lowercase, hyphens only" but the backend pattern (`^[a-z0-9-]+$`) also allows digits — fixed to "lowercase letters, numbers, hyphens". |
| `git_url` | ok | ok | `ok` | |
| `git_provider` | ok (Forge select) | ok (Forge select) | `ok` | |
| `github_installation_id` | ok (Select-repo picker) | ok (Select-repo picker + Unbind) | `ok` | |
| `default_branch` | ok | ok | `ok` | |
| `protected_branches` | `n/a` (create has no such field on the backend either) | ok (chip editor) | `ok` | `ProjectCreateRequest.protected_branches` exists but defaults to `[default_branch]`; the create dialog correctly leaves it for the edit dialog post-creation. |
| `environments` | ok (`EnvironmentLadderEditor`) | ok (`EnvironmentLadderEditor`) | `ok` | |
| `git_token` | ok | ok (set/replace/clear flow) | `ok` | |
| `test_command` / `lint_command` / `format_command` / `typecheck_command` / `build_command` / `quality_command` / `codegen_command` | ok (create lacks `codegen_command` — see note) | ok | `ok` | `codegen_command` is edit-only in the UI even though `ProjectCreateRequest` accepts it — matches the create dialog's own comment that autonomous/advanced config is deliberately deferred to "Edit Project" post-creation. |
| `is_active` | `n/a` (create has no such field) | ok | `ok` | |
| `ci_watch_enabled` / `ci_watch_workflow` | `n/a` (create has no such field) | ok | `ok` | |
| `video_engine_enabled` | `n/a` (create has no such field) | ok | `ok` | |
| `dep_update_command` / `dep_update_paths` | `n/a` (create has no such field) | ok | `ok` | |
| `monthly_budget_usd` | `n/a` (`ProjectCreateRequest` has no such field — update-only) | ok | `ok` | |
| `sandbox_services` / `sandbox_extensions` | `n/a` (create has no such field) | ok | `ok` | |

---

## Task dialogs (Stream1-B)

Forms: `panel/src/components/tasks/create-task-dialog.tsx` (backend: `TaskCreate` in `roboco/models/task.py`) and `edit-task-dialog.tsx` (backend: the route-level `TaskUpdate` in `roboco/api/schemas/tasks.py` — a richer schema than the internal `roboco/models/task.py TaskUpdate`; the API route imports the former).

`acceptance_criteria` editing on the edit dialog — the one gap the intake explicitly named for this unit — is already shipped. The remaining gaps below were found during this audit sweep, not named by the intake.

| Field | Create dialog | Edit dialog | Verdict | Notes |
|---|---|---|---|---|
| `title` | ok | ok | `ok` | |
| `description` | ok (Markdown editor, 20-char min) | ok | `ok` | |
| `acceptance_criteria` | ok (`AcceptanceCriteriaEditor`) | ok (`AcceptanceCriteriaEditor`) | `ok` | |
| `team` | ok | ok | `ok` | |
| `priority` | ok | ok | `ok` | |
| `sequence` | missing | missing | `missing` | `TaskCreate.sequence` (create) and the route-level `TaskUpdate.sequence` (edit) both accept sibling ordering; neither dialog exposes a control for it. |
| `status` | ok (Pending/Backlog at creation) | `n/a` | `n/a` | Post-creation status changes are lifecycle transitions (claim/complete/etc.) gated through role-specific verbs, not a free-text edit field. |
| `estimated_complexity` | ok | ok | `ok` | |
| `nature` | ok | ok | `ok` | |
| `task_type` | ok (behind "Advanced Options") | ok (behind "Advanced Options") | `ok` | |
| `target_date` | missing | ok | `missing` | `TaskCreate.target_date` accepts it; only the edit dialog exposes a control. |
| `budget_usd` | `n/a` (`TaskCreate` has no such field — update-only) | ok | `ok` | Mirrors the project dialog's `monthly_budget_usd` shape (create has no field, edit does, both intentional per the backend schema). |
| `project_id` / `product_id` | ok (behind "Advanced Options") | ok (`project_id` only; locked once `branch_name` is set) | `ok` | `product_id` is create-only by design — a fan-out task's per-cell routing is fixed at creation and `TaskUpdate` carries no `product_id`. |
| `parent_task_id` | ok (behind "Advanced Options") | missing | `missing` | The route-level `TaskUpdate.parent_task_id` accepts re-parenting after creation; the edit dialog has no control for it. |
| `dependency_ids` | ok (`DependencySelector`) | missing | `missing` | The route-level `TaskUpdate.dependency_ids` accepts it; the edit dialog has no equivalent control even though `Task.dependency_ids` is already in the type. |
| `blocker_ids` | `n/a` | `n/a` | `n/a` | Present on the route-level `TaskUpdate` schema but every non-test producer of blockers found in `roboco/services/` sets it programmatically (dependency/lifecycle bookkeeping); no evidence this is meant as a manually-edited dialog field, so it is left as a system-managed field pending explicit product direction otherwise. |
| `assigned_to` | ok (behind "Advanced Options") | ok (behind "Advanced Options") | `ok` | |
| `dev_notes` / `qa_notes` / `auditor_notes` / `pr_reviewer_notes` / `doc_notes` / `quick_context` | `n/a` | `n/a` | `n/a` | Role-authored structured note fields written through each role's own gateway verbs, not general-purpose manual edit fields. |
| `plan` / `progress_updates` | `n/a` | `n/a` | `n/a` | Agent execution-tracking artifacts, not manual dialog fields. |
| `branch_name` / `pr_number` / `pr_url` / `docs_complete` / `pr_created` | `n/a` | `n/a` | `n/a` | System-managed git/work-session tracking fields, not user-editable. |
