# Round-6 pr_gate fix: CI-red quality gate + stale dispatch-table line citations

Cell PR #934 (branch `feature/backend/5442e6a6--01860f88`, the shipped-work-digest helper + Board Program prompt injection) bounced round 6 of `pr_gate` review with two open findings — the digest diff itself had already reviewed clean against all five acceptance criteria. Both findings were fixed on the same branch by this task (commits `4eb9a80c`, `a3d9824f`) and land in assembled PR #1071.

## F-564e0c72 (nit): stale dispatch-table line citations

`docs/backend/services/shipped-work-digest.md`'s dispatch table cited line numbers 14325/14380/14562 against `orchestrator.py` for `_dispatch_roadmap_exploration`, `_dispatch_pest_control_exploration`, and `_dispatch_spackle_exploration`. Those numbers were stale: `orchestrator.py` is now 2169 lines, and the three methods actually live in `roboco/runtime/engines/dispatch_work.py` (verified at lines 261/318/501 on this branch).

**Fix:** dropped the "Line (dispatch)" column entirely and cite the three symbol names alone, matching how the digest doc already handles `_shipped_digest_block()` and `_SHIPPED_DIGEST_INSTRUCTION` (both of which genuinely still live in `orchestrator.py` and were left unchanged) — symbol citations survive a rebase, bare line numbers do not. Any future orchestrator/dispatch-module split won't re-break this table.

## F-bd351444 (blocker): CI red on the assembled PR's head commit

The task description's working theory was that the round-6 CI-red was the pre-existing `roboco/security.py` mypy/guard-core incompatibility (tracked separately in tasks `fbbe59bb`/`7777a962`). Direct review of `security.py` on this branch showed it already matches the newer guard-core API — that fix had already landed via merged siblings `1073f708`/`590d5d1b` (PR #972/#999, see `ci-mypy-fix-security-round3-revert.md`). The mypy errors observed locally against `security.py` were stale-venv noise (an installed `guard-core` 3.7.0 against a pyproject floor of `>=3.16.0` in a root-owned `.venv` that could not be `uv sync`ed).

The real cause was `scripts/reflow_md.py --check` — one step of `make quality` — flagging hard-wrapped prose in `docs/backend/qa/ci-mypy-fix-security-round3-revert.md`, an unrelated pre-existing QA doc from the round-3 revert.

**Fix:** `scripts/reflow_md.py --apply` against that doc — a mechanical, token-invariant reflow with no prose content change.

CodeQL, new this round (it appeared after the rebase onto the orchestrator/dispatch-module split), was reviewed directly rather than assumed to be a false positive; no real finding was present once the branch was current.

## What shipped

- `docs/backend/services/shipped-work-digest.md`: dispatch table now cites symbol names only (no line-number column).
- `docs/backend/qa/ci-mypy-fix-security-round3-revert.md`: reflow only, no content change.
- No digest behavior change — the shared helper, the `MegaphoneEngine` delegation, and the three Board Program prompt injections are untouched; the pre-existing regression tests for all of these still pass.

## Pattern

A gate-red finding's own suspected root cause (here, `security.py`'s guard-core mypy story) can already be resolved by a sibling task landed earlier in the same week — check the base branch and the file itself before re-deriving a fix. The actual quality-gate failure was a full step of `make quality` (`reflow_md.py --check`) that the finding's own narrative didn't name; running the complete local gate, not just the suspected sub-check, is what surfaced it.
