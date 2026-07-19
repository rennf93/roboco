# Possibilities Matrix (work-already-done fast path)

A fast path on `i_am_done`: when a dev's work already looks done, the choreographer submits straight to `awaiting_qa` in one call instead of walking the standard verify/journal turn. Gated by `ROBOCO_POSSIBILITIES_MATRIX_ENABLED` (default off — panel-toggleable, Settings → Feature Flags). Fully inert when off: `i_am_done` behaves exactly as it always has.

## You don't opt in — it's transparent

There is no separate verb and no argument that turns this on. A developer always just calls:

```python
i_am_done(task_id="<task>", notes="...", resolved_findings=None)
```

`_maybe_i_am_done_fast_path` runs on **every** `i_am_done` call when the flag is armed, checks whether the task already looks done, and silently takes the fast path when it does. Nothing about how you call `i_am_done` changes — you never need to know whether the fast path fired.

The orchestrator's dev spawn prompt sometimes steers a freshly spawned dev straight to a `WORK_ALREADY_DONE` state (when the flag is on and the task already has an open PR + commits at spawn time) that tells you to call `i_am_done` directly rather than re-deriving what's already done — this is a turn-saving nudge for that specific spawn-timing case, not a requirement. A mid-session dev who never saw that prompt still gets the fast path the moment it calls plain `i_am_done`.

## What "already looks done" means

`_work_appears_done` — all of the following, checked against the live task:

- Status is `claimed`, `in_progress`, or `verifying`
- At least one commit exists
- A PR is open (`pr_created` or `pr_number` set)
- Every acceptance criterion is addressed (each AC has a recorded artifact reference)
- No open finding remains on the revision-findings ledger for this task

Ownership (`assigned_to == you`) is checked separately before the fast path is even considered.

## What still runs — nothing is skipped that matters

The fast path is not a bypass of the non-negotiable guards, only of the standard multi-turn derivation:

1. Substantive-notes check
2. Video render-preview check, on a `source=video` task (`Requirement.RENDER_VERIFIED`)
3. `resolved_findings` applied, if given
4. Commits/PR field gates (`NO_COMMITS` / `NO_PR`)
5. Branch pushed
6. Not behind base
7. Architectural-conventions gate, if enabled
8. Every open finding re-checked — `FINDINGS_ADDRESSED` still blocks a resubmit that left one unnamed
9. The quality verdict (below)

What IS skipped versus the standard path: the retroactive rich-plan derivation, and the journal progress/reflect tracing gates.

## The quality verdict — CI trusted, local gate as fallback

`_fast_path_quality_verdict` resolves the assembled PR's own CI status the same way `pr_pass` does:

- **CI green** → trusted outright; no local gate runs at all.
- **CI red** → the fast path refuses outright: "fast path refused — PR CI is failing; QA reviews working code, not a red build." Fix CI (or route through the standard path) — the fast path will not ship a known-red build to QA.
- **No CI signal at all** (not configured, pending, or unresolvable) → falls back to the local `make quality`-style gate (plus the toolchain-match guard, when `ROBOCO_TOOLCHAIN_MATCH_ENABLED` is also armed).

## See also

- `docs/rag/roles/developer.md` — the fast path from the dev's seat
- `docs/rag/roles/pr-reviewer.md` / `docs/rag/architecture/review-findings.md` — the same CI-green trust `pr_pass` applies
- `CLAUDE.md` "Possibilities matrix" — the canonical feature summary
