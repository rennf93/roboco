# Revision Findings Ledger

Structured, persistent code-level feedback for every QA/PR-gate/PM/CEO bounce back to `needs_revision` — the replacement for a prose `issues` list that got flattened into free text and forgotten by the next round. If a task ever comes back to you as `needs_revision`, this is the mechanism that carries WHAT was wrong, not just THAT something was wrong. Always on — there is no feature flag.

## The four producers

| Verb | Caller | Origin | Note it renders into |
|---|---|---|---|
| `fail` (`fail_review`) | QA | `qa` | `qa_notes` |
| `pr_fail` | PR reviewer (in-path gate) | `pr_gate` | `pr_reviewer_notes` |
| `request_changes` | Cell PM / Main PM | `pm` | `pm_notes` |
| `ceo_reject` | CEO (panel-only, not a gateway verb) | `ceo` | — (a single finding derived from your rejection reason) |

Each takes `findings: list[dict]` — a list of structured findings. The legacy `issues: list[str]` (plain strings) still works this release as a deprecated shim: each string becomes a file-less `severity=major` finding. Sending both `findings` and `issues` in the same call merges them rather than dropping one.

## The `Finding` shape

```python
{
    "file": "roboco/api/routes/rate_limit.py",   # optional; repo-relative, no ".."
    "line": 88,                                   # optional; >= 1
    "severity": "blocker",                        # required: blocker | major | minor | nit
    "criterion": "<acceptance-criterion id or exact text>",  # optional
    "expected": "429 on the 101st request",       # required, <=300 chars
    "actual": "the 100th request also 429s",      # required, <=300 chars
    "fix": "use > not >= on the window limit",    # optional, <=500 chars — describe the change, never a literal patch
    "evidence": "<failing test output / CI lines / diff hunk>",  # optional, <=2000 chars
}
```

- `severity`: `blocker` (must fix before merge/pass) → `major` (significant defect) → `minor` (small defect, fix advised) → `nit` (cosmetic).
- `criterion`, if supplied, must match one of the task's acceptance-criterion ids or exact text — a criterion that matches neither is rejected outright, so a typo doesn't silently detach the finding from what it's actually about.
- **Count guard**: a soft nudge appears (non-blocking) above 5 findings in one call; more than 10 is a hard reject — split across calls or prioritize the blocking ones first. An oversized findings list is as unreviewable as an oversized task.

## What happens when you file one

Every finding is validated, then inserted as one append-only row on the task's `task_review_findings` ledger (`origin`, `round` = the revision count this bounce belongs to, `status=open`), then rendered into a deterministic line — `[F-xxxxxxxx] file:line (severity) — expected → actual → fix` — that becomes both the structured note's `summary` (so `qa_notes`/`pr_reviewer_notes`/`pm_notes` show it directly) and the A2A message body. Nothing here is a snapshot that a later round can overwrite — every round's findings stay on the ledger alongside every earlier round's.

## Resolving findings (the bounced side)

`i_am_done`, `submit_up`, and `submit_root` all accept `resolved_findings`:

```python
i_am_done(
    task_id="<task>",
    notes="...",
    resolved_findings=[
        {"finding_id": "a1b2c3d4", "commit": "<sha>", "note": "fixed the off-by-one"},
    ],
)
```

`finding_id` is the 8-char id shown in the `[F-xxxxxxxx]` rendering (an unambiguous longer prefix, or the full id, also matches). Every entry on the ledger still `open` for this task must be named or the call is refused, listing exactly which ids are still open — you don't have to guess or re-derive them. This is a gate, not a suggestion: an unresolved finding blocks resubmission.

## Where findings arrive

- **`evidence(task_id)`** carries `revision_findings` — the OPEN findings on the task, for any role.
- **`claim_review`** (QA) and **`claim_gate_review`** (PR reviewer) additionally carry `prior_findings` — the FULL ledger (every round, every status), so a round-2+ reviewer checks each prior finding against the current diff instead of re-deriving what was already found.
- Your **respawn prompt**, if the task bounced, renders the open findings inline (id, file:line, expected → actual → fix) — you don't have to call anything extra to see them.
- A PM re-spawned onto a bounced root sees a "bounced" block in its triage prompt with the same rendering.
- The A2A message a producer sends alongside the bounce carries the identical rendering.
- The panel's task-detail **Findings tab** shows the full ledger per round with status badges; the task header shows a `bounced xN` chip.

## Verification (the reviewer's side)

When the SAME origin's review passes on a later round, every `addressed` finding of that origin is bulk-promoted to `verified` in the same transaction — `pass` (QA) verifies `qa`-origin findings, `pr_pass` verifies `pr_gate`-origin, `complete` (PM) verifies `pm`-origin. `ceo_approve` does the same for `ceo`-origin findings, best-effort. A finding can also be `waived` (the repository supports it) but no verb currently calls that path — an unaddressed finding cannot yet be dismissed without actually resolving it.

## `ceo_reject` specifically

The CEO acts through the panel, not a gateway verb — there is no agent-facing `ceo_reject` call. But the CEO's rejection reason is no longer just a status flip: it becomes one `origin=ceo`, `severity=blocker` finding on the ledger (`expected="CEO sign-off on this task"`, `actual=<your reason>`), visible in the Findings tab and delivered to whoever reworks it exactly like a QA or PR-gate bounce. An empty or placeholder reason ("", "wip", "n/a") is rejected cleanly rather than causing a server error.

## Don't confuse this with `convention_findings`

`convention_findings` (surfaced in QA's `claim_review` evidence when the architectural-conventions standard is enabled) is a completely different concept: diff-time lint findings from the architecture validator (misplaced definitions, lint suppressions), keyed to a different table (`project_convention_findings`), replaced per-task rather than append-only. A findings-driven `claim_review` evidence payload can carry BOTH `convention_findings` and `revision_findings`/`prior_findings` at once — don't conflate the two words.

## See also

- `docs/rag/roles/qa.md` — `fail`/`pass` in practice
- `docs/rag/roles/pr-reviewer.md` — `pr_fail`/`pr_pass` in practice
- `docs/rag/roles/cell-pm.md` / `docs/rag/roles/main-pm.md` — `request_changes` in practice
- `docs/rag/roles/developer.md` — resolving a bounce with `resolved_findings`
- `docs/rag/roles/ceo.md` — `ceo_reject`
- `docs/rag/lifecycle/intent-verbs.md` — the canonical verb reference
- `docs/rag/standards/conventions.md` — the unrelated `convention_findings` concept
- `docs/map/review-findings.md` — the implementation map (code-facing, not agent-facing)
