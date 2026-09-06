Slice key: `attestation` Repo root: `roboco` Scope: `roboco/services/attestation.py`, `roboco/api/schemas/attestation.py`, the `GET /api/tasks/{id}/attestation` route in `roboco/api/routes/tasks.py`

## Purpose

The per-task verification attestation: a single, auditable JSON/Markdown
snapshot proving how a task was verified — every acceptance criterion with
its verified stamp and evidence, the full revision-findings ledger grouped
by round, the CI verdict for the PR's head commit, architectural-conventions
findings, the status-transition custody chain, and the commit/branch/PR
refs a work session is bound to. This is an **export of what the lifecycle
already proves** — no new data capture, no new tables or columns; every
field is read from `task_review_findings`, the `qa_notes` `[AC]` stamps,
`work_sessions`, `audit_log`, and `project_convention_findings`.

Two sequenced leaves built this: `roboco/services/attestation.py` (the
assembly service + frozen dataclasses, task `8d79e63a`) landed first, then
`GET /api/tasks/{id}/attestation` (the thin route + Markdown render, task
`d971bd3c`) consumed its merged output directly. This is the **per-task**
sibling of the already-shipped **release-level** artifact
(`docs/map/release-manager.md`'s `GET /api/releases/{version}/certificate`,
task `cf266bc5`) — the CEO scope correction on the parent root explicitly
forbade rebuilding a release-level rollup here; that half stays owned by
the certificate endpoint.

## Files

| Path | Role | LOC |
|---|---|---|
| `roboco/services/attestation.py` | Assembly service: `assemble_task_attestation` reads the five source tables/stamps into a `TaskAttestation` frozen-dataclass tree; `render_attestation_markdown` renders that SAME object as a human-readable receipt. | 501 |
| `roboco/api/schemas/attestation.py` | Pydantic response schemas mirroring the service's dataclasses field-for-field; `attestation_to_response` converts one into the other. | 230 |
| `roboco/api/routes/tasks.py` (`get_task_attestation`, ~line 1003) | Thin `GET /{task_id}/attestation` handler: resolve task → resolve project/git_service if project-bound → `assemble_task_attestation` → branch on `format`. No model/helper defined in the route itself. | (slice within a shared file) |

## Key Symbols

| Name | Kind | File:Line | Responsibility |
|---|---|---|---|
| `TaskAttestation` | frozen dataclass | `attestation.py:151` | The full per-task attestation: identity/refs, `commits`, `work_sessions`, `acceptance_criteria`, `findings_by_round`, `ci`, `conventions_findings`, `reviewer_chain`, `generated_at`. The real contract — leaf 2's route/schemas/render all key off this shape. |
| `assemble_task_attestation` | coroutine | `attestation.py:465` | Builds one `TaskAttestation` from a `TaskTable` row plus `session` queries; `project_slug`/`git_service` are optional duck-typed params used only to fetch the live CI verdict — every other field is reachable from `task`/`session` alone. |
| `_attested_criteria` | function | `attestation.py:188` | Matches each AC against the `qa_notes` `[AC]` stamps by id OR text (mirrors `findings_lib.unmatched_criteria`) — `criteria_verified`'s `criterion` key may be either the AC's stable id or its exact text, so matching text alone would misreport an id-keyed verified criterion as unverified. |
| `_parse_ac_stamps` | function | `attestation.py:173` | Regex-parses `qa_notes`'s deterministic `"[AC] <criterion> — verified: <evidence>"` lines (`_AC_STAMP_RE`) back into a `{criterion: evidence}` dict — best-effort text parsing of an existing rendering, not a new capture point. |
| `_reviewer_chain` | coroutine | `attestation.py:263` | The custody chain from `audit_log`: one entry per generic `task.<to_status>` row, oldest first. Skips the rejector-attributed duplicate rows (`task.qa_fail`/`task.pr_fail`/`task.request_changes`/`task.ceo_reject`, emitted alongside the generic row by `TaskService._audit_events_for` for rework-metric attribution — see `docs/map/review-findings.md`) so each real transition appears exactly once. |
| `_ci_verdict` | coroutine | `attestation.py:326` | The PR head commit's CI state via `git_service.get_pr_ci_status` (duck-typed). Returns `not_available` when no PR exists yet or the caller supplied no `git_service`/`project_slug` — no live call is attempted in that case; returns `error` on an exception from the call itself. |
| `render_attestation_markdown` | function | `attestation.py:442` | Renders a human-readable Markdown receipt from an ALREADY-assembled `TaskAttestation` — never a second computation over raw tables, so the Markdown can never drift from the JSON the route also returns. Each section (header/AC/findings/CI/conventions/reviewer-chain/work-sessions) is a small pure helper kept under the branch budget. |
| `TaskAttestationResponse` | Pydantic model | `schemas/attestation.py:100` | Response schema mirroring `TaskAttestation` field-for-field. |
| `attestation_to_response` | function | `schemas/attestation.py:141` | Converts the assembler's frozen-dataclass tree into its Pydantic twin. Lives in `roboco.api.schemas`, not the route and not the service — services must never import schemas (conventions map layering rule). |
| `get_task_attestation` | async route | `roboco/api/routes/tasks.py:1004` | `GET /api/tasks/{id}/attestation`; 404 on an unknown task id; `format=json` (default) returns `TaskAttestationResponse`, `format=markdown` returns a `PlainTextResponse` of `render_attestation_markdown`'s output — both call sites pass the SAME `assemble_task_attestation` result, so JSON and Markdown can never diverge. |

## Response shape (cross-cell contract)

The frontend cell's task-detail download action consumes this response
verbatim (`docs/map/release-manager.md`'s certificate endpoint is the
sibling pattern for the CEO-facing release-level version) — treat field
names/structure as a contract; extend additively only, and check with
be-pm before renaming or reshaping.

```
GET /api/tasks/{task_id}/attestation?format=json|markdown
```

- `format=json` (default): `TaskAttestationResponse` — task identity/refs
  (`task_id`, `title`, `status`, `team`, `project_slug`, `branch_name`,
  `pr_number`, `pr_url`, `revision_count`), `commits`, `work_sessions[]`,
  `acceptance_criteria[]` (`{id, text, verified, evidence}`),
  `findings_by_round[]` (`{round, findings: [...]}`), `ci`
  (`{state, head_sha, failing_checks}`), `conventions_findings[]`,
  `reviewer_chain[]`, `generated_at`.
- `format=markdown`: a `text/markdown` rendering of the identical data —
  header block, `## Acceptance criteria` checklist (`[x]`/`[ ]` + evidence
  line), `## Findings ledger` grouped by round, `## CI verdict`,
  `## Conventions findings`, `## Reviewer chain`, `## Work sessions`. Every
  empty section renders an explicit placeholder (`_None recorded._` /
  `_No findings raised._`) rather than an empty heading.
- No auth restriction beyond a valid agent context (unlike the CEO-only
  release certificate) — usable for a completed OR in-flight task.

## Tests

- `tests/unit/services/test_attestation.py` — the Markdown render: header
  identity/refs, the AC checklist, every ledger finding status
  (open/addressed/verified/waived), the CI/conventions/reviewer-chain/
  work-session sections, and each section's empty-state placeholder.
- `tests/unit/api/schemas/test_attestation_schema.py` — the Pydantic
  response shape and its JSON serialization round-trip, against the same
  mixed-finding-states fixture.
- No HTTP-level integration test for the route itself exists yet: any test
  importing `roboco.api.routes` fails to collect in the shared dev sandbox
  due to a pre-existing, unrelated stale `guard` package version mismatch
  in `roboco/security.py` — reproduced on an untouched pre-existing
  integration test file, confirming it predates this feature.

## Gotchas

- **No new capture point, ever.** Every field this assembler returns is
  read from data another part of the lifecycle already persists
  (`task_review_findings`, the `qa_notes` stamps, `work_sessions`,
  `audit_log`, `project_convention_findings`). If a future change needs a
  field this assembler can't currently produce, the fix is exposing an
  existing table/column here — not adding a write path to this module.
- **JSON and Markdown must never diverge.** Both response branches in
  `get_task_attestation` call `assemble_task_attestation` exactly once and
  derive from that SAME `TaskAttestation` object — `render_attestation_markdown`
  takes the already-assembled object, never raw tables. A future change
  that adds a second `assemble_task_attestation` call (e.g. to avoid
  passing the object around) would reintroduce the divergence class this
  design exists to prevent.
- **`_attested_criteria` must match by id OR text, not text alone.**
  `pass_review`'s `criteria_verified` stamps a criterion into `qa_notes` by
  whichever key the caller supplied (a stable AC id or its exact text) —
  matching only by text silently misreports every id-keyed verified
  criterion as unverified. Mirrors `findings_lib.unmatched_criteria`'s same
  dual-key matching.
- **This is the per-task twin of the release certificate, not a
  replacement.** `docs/map/release-manager.md`'s `GET
  /api/releases/{version}/certificate` aggregates the SAME kind of data
  (CI, conventions, findings, AC verification) at release-window scope,
  CEO-only, for a published release. This endpoint is per-task, open to
  any authenticated agent, and works on in-flight tasks too. A future
  release-level rollup of per-task attestations is an explicit follow-up
  to the certificate endpoint (task `cf266bc5`), not this slice — do not
  duplicate it here.
- **Layering: services never import schemas.** `attestation_to_response`
  lives in `roboco/api/schemas/attestation.py`, not
  `roboco/services/attestation.py` — the service module only defines and
  returns plain frozen dataclasses so it stays schema-agnostic per the
  conventions map's layering rule.

## Related

- `docs/map/release-manager.md` — the release-level sibling artifact (`GET
  /api/releases/{version}/certificate`) this endpoint is explicitly scoped
  apart from
- `docs/map/review-findings.md` — the `task_review_findings` ledger
  `findings_by_round` reads, including the rejector-attributed duplicate
  audit rows `_reviewer_chain` filters out
- `docs/map/metrics-observability.md` — `MetricsService`'s own separate
  `reviewer_chain` field on `GET /dashboard/metrics/task/{id}` (a
  DIFFERENT reviewer_chain shape, sourced from `agent_spawn_sessions`
  rather than `audit_log` — do not conflate the two)
