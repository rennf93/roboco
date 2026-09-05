# Release Certificate Endpoint

**Date:** 2026-08-31 **Task:** 63375b3c **PR:** #969 **Files:** `roboco/api/routes/releases.py`, `roboco/services/release_certificate.py`, `roboco/api/schemas/release.py`, `roboco/api/app.py`

**Update (2026-09-05, PR #1022 pr_gate bounce):** `conventions_clean`, the
release-window task set, `ceo_approved_at`, and `qa_passed` all measured the
wrong thing — see "Semantics" and "Where each piece comes from" below for the
corrected derivations. Files touched: `roboco/services/release_certificate.py`,
`roboco/api/routes/release.py`, `roboco/api/schemas/release.py`,
`roboco/foundation/policy/content/markers.py`.

## What

CEO-only `GET /api/releases/{version}/certificate` packages one published release's full gate chain into a single exportable artifact — the external-facing proof a release was governed end-to-end. It is the backend half of a two-cell feature: the panel's "Download certificate" action consumes the response schema (`ReleaseCertificateResponse`) verbatim, so that schema is a **cross-cell contract** — additive changes only.

The route is thin and read-only: `_require_ceo(agent)` gate → one `ReleaseCertificateService.build_certificate(version)` call → 404 on an unpublished version → `ReleaseCertificateResponse(**asdict(certificate))`. Every DB read lives in the assembly service, not the route.

The endpoint is registered in `roboco/api/app.py` via `_mount_release_routers`, alongside the existing *singular* `/api/release/proposal*` router (`roboco/api/routes/release.py`). The plural `/api/releases/...` router is deliberately a separate file — the certificate is version-keyed, the proposal surface is a live gate.

## Semantics

- **Version lookup:** the version path param is normalized (trimmed, leading `v` dropped — a git tag and a CHANGELOG reference resolve the same) and matched against the **stored readiness report** of every COMPLETED `source=release_manager` proposal task (`TaskService.list_completed_release_proposals`, ordered by completion). A *published* release means that proposal task is COMPLETED (publish happens in the gated `ReleaseExecutor`; the proposal completing is the durable record). No matching COMPLETED proposal → **404** with `No published release for version ...`. The `v` prefix is tolerated (`v1.2.3` == `1.2.3`).
- **CEO-only via reuse:** the gate is `_require_ceo(agent)` from `roboco/api/utils/release.py` — the same helper the proposal routes use. Non-CEO callers are refused before any lookup.
- **Read-only:** no approval, publish, or state change passes through here.

## The response schema (cross-cell contract for fe-pm)

`roboco/api/schemas/release.py` — additive-only changes.

```python
class ReleaseCertificateResponse(BaseModel):
    version: str  # normalized (v-prefix dropped)
    generated_at: datetime  # UTC, at request time
    ci_verdict: str  # "green" | "red" | "unknown" (readiness report gate_state)
    conventions_clean: bool  # see derivation below
    ceo_approved_at: datetime | None  # CEO's approval-dispatch timestamp, or None
    changelog_excerpt: str  # readiness report drafted_changelog
    task_states: list[ReleaseCertificateTaskState]
    findings_summary: ReleaseCertificateFindingsSummary


class ReleaseCertificateTaskState(BaseModel):
    task_id: str
    title: str
    status: str
    criteria_total: int  # the task's acceptance-criterion count
    criteria_verified: int  # QA '[AC]' verified-stamp count in qa_notes
    qa_passed: bool | None  # None = no AC (no QA required), else verified>=total


class ReleaseCertificateSeverityCounts(BaseModel):
    blocker: int = 0
    major: int = 0
    minor: int = 0
    nit: int = 0


class ReleaseCertificateFindingsSummary(BaseModel):
    open: ReleaseCertificateSeverityCounts  # unaddressed findings
    closed: ReleaseCertificateSeverityCounts  # status 'addressed' or 'verified'
    waived: ReleaseCertificateSeverityCounts  # Auditor-waived minor/nit findings
```

## Where each piece comes from (source map)

| Certificate field | Source |
|---|---|
| `ci_verdict`, `changelog_excerpt` | The proposal's stored readiness report (`markers.get_release_report` → `report_from_dict`, `roboco/services/release_readiness.py`): `gate_state` / `drafted_changelog`. |
| `conventions_clean` | `ReleaseCertificateService._conventions_clean`: True unless any task in the release window has an unresolved **`level="block"`** row in `project_convention_findings` — the durable record of the REAL conventions validator's own output (`GitService.conventions_check_for_task`, git.py:6416), persisted per task at `i_am_done` time by `ConventionsService.record_findings`. Read from the persisted table rather than re-running the validator live: a merged task's branch is deleted post-merge, so a live re-check would fail-closed on every historical task with nothing left to diff. (Previously this reused `release_readiness.py`'s commit-classification gaps — an unrelated signal about whether commit subjects could be auto-classified, not architectural conventions at all.) |
| `ceo_approved_at` | `markers.get_release_approved_at(target)` — the timestamp the CEO's `POST /api/release/proposal/approve` route stamps on the proposal task the moment it dispatches the background executor (`roboco/api/routes/release.py`), parsed back to a `datetime`. `None` when the marker is absent (a release published before this fix shipped). (Previously this read the proposal's `completed_at`, which is when the ~40min background `ReleaseExecutor` finished *publishing* — long after the CEO actually clicked approve.) |
| `task_states[]` | One entry per task in the release task set (below); per-AC counts parsed from each task's `qa_notes` `[AC] ` stamp lines (the deterministic rendering QA's `pass_review` produces); `qa_passed` is `None` for a zero-criteria task (never went through QA), else `criteria_verified >= criteria_total`. |
| `findings_summary` | The task-review-findings ledger (`ReviewFindingsRepository.list_for_task`) aggregated across the release task set, bucketed open / closed (`addressed`+`verified`) / waived, by severity (`blocker`/`major`/`minor`/`nit`, unknown severities ignored). |

## Release task-set derivation (documented heuristic)

There is **no schema link** between a release and its tasks — the release proposal task is the only durable release record. The task set is therefore derived: delivery tasks with status `COMPLETED` inside the window between the previous **same-project** publication's `completed_at` and this proposal's own `completed_at` (exclusive on the previous end, inclusive on this end), ordered by completion. Key properties:

- **Project-scoped:** `TaskTable.project_id == proposal.project_id` — the readiness report assesses that project's changes, so another project's task completing inside the window must not leak into this certificate. Null-project proposals aren't producible today; if one ever appears the filter degrades to unfiltered rather than guessing a scope.
- **First release** of a project takes everything completed before its own completion.
- **Held/coordination artifacts excluded, delivery roots included:** the proposal itself, X posts, video drafts, and board-program exploration cycles are filtered out via `task_type != ADMINISTRATIVE` and `source NOT IN LEAD_TIME_EXCLUDED_SOURCES` (mirroring `TaskService.get_delivery_stats_30d`'s real-delivery-work filter) — **not** by allow-listing `HUMAN_AUTHORED_SOURCES` ("manual"/"prompter"). The prior allow-list silently dropped every engine-originated delivery root (self_heal/ci_watch/dep_update/docs_sync/roadmap/pest_control, task.py:666-788) while still including their subtasks: a delegated subtask's own `source` column is always "manual" (`create_subtask` never inherits the parent's source), so only the ROOT of an engine-originated tree carried the non-allow-listed source value.

Changing this heuristic changes what `task_states`/`findings_summary` cover; treat any change as a contract-adjacent decision worth a QA pass and a journal decision entry.

## Tests

- `tests/integration/test_release_routes.py` — happy path (full gate chain, including an engine-originated delivery root and all three `qa_passed` states), conventions-dirty/-clean cases keyed off real persisted findings, `ceo_approved_at` present/absent, 404 for an unpublished version, non-CEO denial, cross-project task exclusion. DB-backed; needs Postgres.
- `tests/unit/services/test_release_certificate.py` — version normalization, `[AC]`-stamp counting, severity bucketing, per-task pass state (including the zero-AC `None` case), window/same-project scoping.

## Risks and follow-ups

- Release membership stays heuristic (completion window + same-project scoping) — a future `release_id` column on tasks would make membership schema-linked.
- The response schema is consumed by the frontend cell starting from this PR; extend only additively (new optional fields), never by reshaping existing fields.
- `conventions_clean` is True on zero recorded `project_convention_findings` rows for the release-window tasks — that means "no recorded block-level violation", not "assessed clean". A task never checked (conventions enforcement is a default-off feature flag) reads the same as a task checked and found clean; there is no separate "unassessed" state on the wire today.
- `qa_passed` widened from `bool` to `bool | None` in this bounce fix (PR #1022) — this is a **nullability widening, not an additive change**, despite the doc's own additive-only rule above: an existing frontend consumer that reads `qa_passed` as a non-nullable boolean now receives `null` for the zero-AC case and must be updated in this same PR (`panel/src/lib/api/release.ts`) rather than treated as safe-by-default.

## Related

- `docs/backend/api/x-post-response-schemas.md` — the sibling CEO-facing response-schema doc pattern
- `docs/rag/architecture/review-findings.md` — the findings ledger the `findings_summary` aggregates (§ "Aggregated release-wide: the release certificate")
- `roboco/services/release_readiness.py` — the readiness report the certificate builds on