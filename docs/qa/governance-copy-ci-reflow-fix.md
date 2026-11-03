# Governance copy PR: CI red from README hard-wrapped prose

The governance copy PR (#1392, assembled as #1424) was blocked at the PR gate because the `ci` check was failing. The copy itself had already passed review, so the approved wording was not touched.

## Root cause

The README hero lines 5-7 and the `Human governance` section were written one sentence per line, in the CEO brand cadence. `make quality` runs `scripts/reflow_md.py --check`, which rejects hard-wrapped prose in `README.md` and `docs/`. The panel edits (`login/page.tsx`, `layout.tsx`) were not the cause: panel format check, lint and typecheck all pass.

## Fix

Ran `scripts/reflow_md.py --apply` (commit `52096ed3`). It joined those lines into single-line paragraphs. No words changed, and the README renders the same in markdown. Only `README.md` is in the diff.

## Caveats from the dev

- The CI log could not be read (no `gh` in the workspace), so the reflow cause is inferred from reproducing `make quality` locally. Confirm on the new head that `ci` is green.
- `pnpm test` showed 27 timeout failures (5000ms) in task-detail tests under load. They are unrelated to the copy and were not touched.
- `sync_branch` failed on a corrupt git object in the dev clone, so the branch was not rebased onto its base.

## Pattern

The one-idea-per-line brand cadence conflicts with the reflow gate. Write README and docs prose as one line per paragraph, or run `make reflow-docs` before committing. See `CONTRIBUTING.md` and `docs/rag/standards/markdown-reflow-quality-gate.md`.
