# Project Dialog Fields Reference

**Status:** Complete as of Task f1957610 (2026-07-23)

This document maps which project configuration fields are exposed in each dialog for future reference. The backend ProjectCreateRequest and ProjectUpdateRequest schemas are the source of truth; frontend types mirror them exactly.

## Create Project Dialog

Fields exposed in `panel/src/components/projects/create-project-dialog.tsx`:

| Field | Type | Optional | Notes |
|-------|------|----------|-------|
| `name` | string | No | Display name, can be changed post-creation |
| `slug` | string | No | Immutable identifier, derives workspace paths |
| `git_url` | string | No | HTTPS URL for git clone and auth |
| `git_provider` | enum | Yes | Auto-detect (github/gitea/gitlab), or explicit for self-hosted |
| `github_installation_id` | number | Yes | Set via GitHub App picker; cleared on URL edits |
| `git_token` | string | Yes | Encrypted at rest; optional at creation, updatable later |
| `assigned_cell` | enum | No | Backend-enforced team ownership (BACKEND/FRONTEND/UX_UI) |
| `default_branch` | string | Yes | Head branch for PRs when no environment ladder exists |
| `environments` | ladder | Yes | Optional ordered environment rungs (head→prod) |
| `test_command` | string | Yes | Reference only; not wired to automated gates |
| `lint_command` | string | Yes | Runs in dev pre-submit (unless quality_command replaces both) |
| `format_command` | string | Yes | Reference only; excluded from pre-submit for safety |
| `typecheck_command` | string | Yes | Runs in dev pre-submit (unless quality_command replaces both) |
| `build_command` | string | Yes | Reference only; test/build left to CI |
| `quality_command` | string | Yes | When set, replaces lint + typecheck in pre-submit gate |
| `codegen_command` | string | Yes | Regenerates checked-in artifacts before push |

**Note:** autonomous maintenance features (ci_watch, video_engine, dependency updates, sandbox services, budget) are configured **after** creation in the Edit Project dialog.

## Edit Project Dialog

All create fields above, **plus:**

| Field | Type | Optional | Notes |
|-------|------|----------|-------|
| `video_engine_enabled` | boolean | Yes | Opt-in per-project video rendering |
| `monthly_budget_usd` | number | Yes | Client-side validation: gt=0, rejects 0 and negatives |
| `sandbox_extensions` | object | Yes | Per-service module extensions (e.g., postgis for postgres) |
| `protected_branches` | array | Yes | Union with hardcoded {master, main}; exact match, case-sensitive |

## Symmetry Notes

- **Asymmetric by design:** create-project-dialog focuses on git setup; edit-project-dialog adds maintenance/autonomy toggles.
- **Type mirrors:** `ProjectCreate` and `ProjectUpdate` in `panel/src/types/index.ts` match the backend schemas exactly.
- **Field validation:** monthly_budget_usd client-side gate mirrors backend Field(gt=0).
- **No deferrals:** all fields mentioned in task f1957610 are present; no partial/deferred implementations exist.

## Future Changes

When adding a new project field to the backend ProjectCreateRequest or ProjectUpdateRequest:

1. Update the backend schema (`roboco/api/schemas/project.py`)
2. Update both frontend types (`panel/src/types/index.ts`)
3. Add to **both** dialogs (unless explicitly create-only or edit-only by design)
4. Mirror help text and validation between dialogs where fields overlap
5. Update this audit table
