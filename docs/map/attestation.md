# RoboCo Map — `attestation` slice

## Purpose

A single, auditable per-task verification attestation — proof of how a task was verified, assembled purely from data other parts of the lifecycle already persist (`task_review_findings`, the `qa_notes` per-AC verification stamps, `work_sessions`, `audit_log`, `project_convention_findings`). No new capture point: this slice is read-only. It exists so a task's acceptance-criteria coverage, revision-findings ledger, CI verdict, conventions findings, and reviewer/custody chain can be reconstructed outside the live panel — e.g. for an external audit or a Markdown report.

This is **leaf 1 of 2**. Leaf 2 (a route + Markdown render + tests) will consume `assemble_task_attestation`'s return value directly, so its shape — plain frozen dataclasses, not the mirrored Pydantic schemas — is the real contract for that follow-on work.

## Files

| Path | Role | LOC |
|---|---|---|
| `roboco/services/attestation.py` | The assembler: `assemble_task_attestation` + its frozen dataclasses (`TaskAttestation` and eight component dataclasses) | 384 |
| `roboco/api/schemas/attestation.py` | Mirrored Pydantic request/response schemas — `TaskAttestationResponse` + component response models, plus `TaskAttestationQuery` | 136 |

## Key Symbols

| Name | Kind | File:Line | Responsibility |
|---|---|---|---|
| `assemble_task_attestation` | async function | `roboco/services/attestation.py:348` | Entry point. Takes a `session`, a loaded `TaskTable`, and optional `project_slug`/`git_service`; returns a fully populated `TaskAttestation`. |
| `TaskAttestation` | frozen dataclass | `roboco/services/attestation.py:150` | The full attestation: task identity/branch/PR refs, `commits`, `work_sessions`, `acceptance_criteria`, `findings_by_round`, `ci`, `conventions_findings`, `reviewer_chain`, `generated_at`. |
| `_attested_criteria` | function | `roboco/services/attestation.py:188` | Matches each task AC against the `qa_notes` `[AC] ... — verified: ...` stamps **by id OR text** (mirrors `findings_lib.unmatched_criteria`'s match semantics) — see Gotchas below for why this matters. |
| `_findings_by_round` | async function | `roboco/services/attestation.py:214` | Groups `ReviewFindingsRepository.list_for_task` rows into `FindingsRound` tuples, sorted by round. |
| `_conventions_findings` | async function | `roboco/services/attestation.py:242` | Reads `ProjectConventionFindingTable` rows for the task, newest first. |
| `_reviewer_chain` | async function | `roboco/services/attestation.py:263` | Reconstructs the custody chain from `audit_log`: one entry per generic `task.<to_status>` row, skipping the rejector-attributed duplicate rows (`task.qa_fail`/`task.pr_fail`/`task.request_changes`/`task.ceo_reject`) so each real transition appears exactly once. |
| `_work_sessions` | async function | `roboco/services/attestation.py:299` | Reads every `WorkSessionTable` row bound to the task — the commit/branch/PR refs an outside auditor can check against real git history. |
| `_ci_verdict` | async function | `roboco/services/attestation.py:326` | Calls a duck-typed `git_service.get_pr_ci_status`; degrades to `not_available`/`error` rather than raising when no PR/git_service is available or the call fails. |
| `TaskAttestationResponse` | Pydantic model | `roboco/api/schemas/attestation.py:97` | Field-for-field mirror of `TaskAttestation`, for the (future) route to serialize. |
| `TaskAttestationQuery` | Pydantic model | `roboco/api/schemas/attestation.py:129` | Query params for the (future) route: `task_id` + `format` (`json`\|`markdown`, default `json`). |

## Data Flow

`assemble_task_attestation(session, task, project_slug=..., git_service=...)` fans out to five independent read helpers (`_work_sessions`, `_attested_criteria` — sync, over the already-loaded `task` — `_findings_by_round`, `_conventions_findings`, `_reviewer_chain`) plus `_ci_verdict`, and assembles their results into one `TaskAttestation`. Every field traces back to a table another part of the lifecycle already writes:

- `acceptance_criteria` ← `task.acceptance_criteria`/`acceptance_criteria_ids` cross-referenced against `task.qa_notes`'s rendered `[AC] ...` stamps (written by `pass_review`'s `criteria_verified` — see `docs/map/review-findings.md`'s sibling "Delegation detail-fidelity" note in CLAUDE.md).
- `findings_by_round` ← `task_review_findings` via `ReviewFindingsRepository` (see `docs/map/review-findings.md`).
- `ci` ← a live `GitService.get_pr_ci_status` call, not a stored column — best-effort, never raises.
- `conventions_findings` ← `project_convention_findings` (see `docs/map/conventions-service-validator.md`).
- `reviewer_chain` ← `audit_log`'s `task.<status>` transition rows.
- `work_sessions` ← `work_sessions`, including each session's `commits`/`pr_number`/`pr_url` for external auditability.

## Dependencies

- `roboco.db.tables` — `AgentTable`, `AuditLogTable`, `ProjectConventionFindingTable`, `TaskTable`, `WorkSessionTable`
- `roboco.services.repositories.review_findings` — `ReviewFindingsRepository`
- `roboco.utils.converters` — `require_uuid`
- Consumed by (future): leaf 2's route, which converts each `roboco.services.attestation` dataclass into its `roboco.api.schemas.attestation` twin

## Entry Points

None yet — this slice ships only the service + schemas. Leaf 2 wires the actual route (per the task description: task id + optional `format` query param) that calls `assemble_task_attestation` and renders JSON or Markdown.

## Config Flags

None — no new flag; sourced entirely from existing data.

## Gotchas

- **AC stamp matching must check id OR text, not text alone.** `pass_review`'s `criteria_verified` entries can key an AC's stamp by either its stable id or its exact text (`findings_lib.unmatched_criteria` already relies on this dual match). The first cut of `_attested_criteria` matched by text only, so an id-keyed stamp was silently reported as `verified=False` with no evidence — QA caught this in round 1 (finding `F-fd9db8fb`) and it was fixed in the revision commit (`b7d2b09a`). Any future caller re-deriving AC-verification matching elsewhere in the codebase should mirror this same id-or-text semantics rather than re-deriving its own.
- **The service returns frozen dataclasses, not the Pydantic response models.** This is deliberate layering: `roboco/services/*` must never import `roboco/api/schemas/*` at runtime (see `roboco/services/git.py`'s `TYPE_CHECKING`-only imports for the precedent this follows). Leaf 2's route is responsible for converting each dataclass into its schema twin — don't have the service construct Pydantic models directly.
- **`ci` is a live call, not a stored field** — it re-queries `GitService.get_pr_ci_status` on every assembly. A deleted branch or unreachable forge API degrades to `CiVerdict(state="not_available")`/`"error"`, never an exception.

## Related

- `docs/map/review-findings.md` — `task_review_findings`, `ReviewFindingsRepository`, the `[AC] ... — verified: ...` qa_notes rendering this slice reads back out
- `docs/map/conventions-service-validator.md` — `project_convention_findings`, the source for `conventions_findings`
- `docs/map/worksession-git.md` — `WorkSessionTable`, `GitService.get_pr_ci_status`
