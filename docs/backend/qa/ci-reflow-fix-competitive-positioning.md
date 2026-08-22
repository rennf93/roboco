# CI Fix: Reflow-Check on Competitive Positioning Doc

**PR #784 / PR #791 — Resolved**

## Root Cause

The Python quality gate's `reflow-check` step was failing on `docs/backend/analysis/port-ai-builder-competitive-positioning.md`. The doc contained hard-wrapped markdown prose (manual line breaks within paragraphs), which the reflow-check rejects — it requires one line per paragraph so that diffs stay clean and the prose reflows naturally in any viewer width.

The failure was initially suspected to be CI staleness (a prior `sync_branch` rebase was claimed to have resolved it), but `sync_branch` returned `superseded` — the branch was already in sync. The real cause was the hard-wrapped lines in the doc itself, not a stale CI state.

## Solution Applied

Reflowed the hard-wrapped prose to one line per paragraph throughout `docs/backend/analysis/port-ai-builder-competitive-positioning.md`. This is a formatting-only change: no content, wording, or citations were modified. The verified wording at line 11 (`require_ceo_role()`) and line 37 (`ROBOCO_CONVENTIONS_ENABLED`) is intact.

All other quality-gate steps were already clean: ruff format, ruff check, mypy, xenon, vulture, bandit, deptry, alembic, lint-imports, compose-sync, pip-audit.

## Impact

- **Scope:** Documentation formatting only (one markdown file)
- **Risk:** None — reflow only changes line wrapping, not content
- **Behavior:** No change to any code, tests, or doc content
- **Verification:** Local `make quality` run confirmed full pass after reflow

## Pattern

The reflow-check enforces one-line-per-paragraph in markdown prose files under `docs/`. When authoring or editing markdown docs, write each paragraph as a single long line — let the viewer handle wrapping. Do not insert manual line breaks within a paragraph.

```markdown
# Correct — one line per paragraph
This is a paragraph. It can be very long, and that is fine. The reflow-check
wants each paragraph on a single line so diffs are clean and the prose
reflows naturally in any viewer width.

# Wrong — hard-wrapped prose (reflow-check fails)
This is a paragraph. It is manually wrapped
at a fixed column width, which the reflow-check
rejects because it creates noisy diffs.
```

If the reflow-check fails on a doc you edited, run `make quality` locally — the failing file and the reflow expectation will be in the output. Join the hard-wrapped lines and re-run the gate.