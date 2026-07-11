# QA Review Workflow

## Preconditions

- Task is in `awaiting_qa` status
- The developer's PR is open (the choreographer opened it during their `open_pr(task_id)` call)
- You are not the original developer of the task (self-review guard)

## Steps

```python
# 1. Pick up an awaiting-QA task
give_me_work()

# 2. Claim it for review (auto-checks-out the dev's branch in your
#    workspace; auto-records original_developer for the self-review
#    guard at pass/fail time)
claim_review(task_id="<task>")

# 3. Inspect the diff (project_slug is optional — omit it and your
#    own project is used)
roboco_git_diff()
roboco_git_log(branch="<dev's branch>")

# 4. Run the relevant suite
# Backend: uv run pytest && uv run ruff check . && uv run mypy roboco/
# Frontend: pnpm test && pnpm lint && pnpm typecheck

# 5. Capture evidence (survives compaction; PMs can audit later)
note(text="Verified AC #1 (429 on 101st req), #2 (TTL match), #3 "
          "(boundary tests). pytest 1635 passed; ruff clean; mypy clean.",
     scope="evidence",
     task_id="<task>")
```

There is no `roboco_task_claim / _start / _qa_pass / _qa_fail` and no `roboco_git_checkout`. The verbs above (`claim_review`, `pass`, `fail`) are the actual surface; branch checkout is a side-effect of `claim_review`.

You always claim the review yourself — the dispatcher spawns you against an `awaiting_qa` task without pre-claiming it. `claim_review` records your claim but keeps the status at `awaiting_qa` (there is no `claimed` detour), so `pass`/`fail` find the status they demand.

## Review Checklist

Before deciding:

- [ ] Read the dev's notes and journal entries on the task
- [ ] Walk every acceptance criterion against the diff
- [ ] Tests pass on the dev's branch
- [ ] Lint / typecheck clean
- [ ] No layer-separation regressions (routes/ vs services/ etc.)
- [ ] No silenced rules (`# noqa`, `# type: ignore`, `# pragma: no cover`)
- [ ] Code matches project standards in CLAUDE.md

## Passing QA

```python
pass(
    task_id="<task>",
    notes=(
        "All 3 acceptance criteria verified against the diff. "
        "pytest 1635 passed; ruff and mypy clean. "
        "PR #123."
    ),
)
```

Result:

- Task advances to `awaiting_documentation`
- Documenter and the original dev work in parallel from here
- The PR stays open; it will be merged later by the Cell PM via `complete(task_id, ...)`

## Failing QA

```python
fail(
    task_id="<task>",
    findings=[
        {
            "file": "roboco/api/routes/rate_limit.py",
            "line": 88,
            "severity": "blocker",
            "expected": "429 on the 101st request in the window",
            "actual": "the 100th request also returns 429 — boundary off-by-one",
            "fix": "use > not >= when comparing against the window limit",
        },
        {
            "severity": "major",
            "criterion": "AC #3 — Redis-down failover path",
            "expected": "a test covering the Redis-down failover path",
            "actual": "no such test exists in this diff",
        },
    ],
)
```

Each finding is validated, persisted onto the task's append-only revision-findings ledger (`origin=qa`, `round=revision_count+1`), and rendered into `qa_notes` as `[F-xxxxxxxx] file:line (severity) — expected → actual → fix`. The old `issues=[...]` (plain strings) form still works this release but is deprecated — each becomes a file-less `severity=major` finding. A soft nudge fires above 5 findings in one call, a hard reject above 10.

Result:

- Task returns to `needs_revision`
- Re-assigned to the original developer (recorded at submit-for-qa time)
- Developer receives a notification, and the open findings arrive inline via `evidence()`'s `revision_findings` and the respawn prompt — see `docs/rag/architecture/review-findings.md`

## Re-reviewing a bounced task (round ≥2)

If the task has failed before, `claim_review` returns `prior_findings` — the FULL ledger, every round, newest first — alongside the usual PR diff. Check each prior finding against the current diff one at a time before deciding: a finding still unaddressed is a fail, not a pass with a note. Passing (`pass`) bulk-verifies every `addressed` QA-origin finding in the same transaction — that verification IS the confirmation the fix landed.

## Reflect (recommended)

After pass or fail, journal the review for future QA agents to learn from:

```python
note(
    text=(
        "Reviewed task <id>. Pattern: rate-limiter boundary tests "
        "should always assert the off-by-one — caught it in this "
        "review and last week's. Worth a regression checklist item."
    ),
    scope="reflect",
    task_id="<task>",
)
```

## Self-Review Prevention

The system blocks QA from reviewing their own dev work. The original developer is recorded in `quick_context` at submit-for-qa time. If `qa_agent_id == original_developer_id`, **all** QA actions on the task return `not_authorized`:

- `claim_review` — FORBIDDEN
- `pass` — FORBIDDEN (defence-in-depth even if claim somehow succeeded)
- `fail` — FORBIDDEN (same)

Enforced at the gateway layer in `roboco/services/gateway/choreographer/_impl.py`.
