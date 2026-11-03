# Markdown Reflow Quality Gate

## Overview

The markdown reflow quality gate ensures that all documentation prose follows a strict one-sentence-per-line formatting standard, avoiding hard-wrapped text that breaks mid-clause across multiple lines. This improves diff readability, version control hygiene, and makes prose edits easier to track.

## What is Hard-Wrapping?

**Hard-wrapped** prose is text that is broken across multiple lines at arbitrary column boundaries for display purposes, typically to keep lines under 80-100 characters:

```markdown
This is a paragraph that has been
hard-wrapped mid-sentence across two
separate lines for no good reason.
```

The reflow quality gate **rejects** this pattern. Instead, prose should follow the **one-logical-unit-per-line** rule:

```markdown
This is a paragraph that has been hard-wrapped mid-sentence across two separate lines for no good reason.
```

The reflowed version is a single line, making the change history (git diff) cleaner and more readable.

## The Reflow Check Script

RoboCo includes a deterministic script, `scripts/reflow_md.py`, that detects hard-wrapped prose in markdown files:

```bash
python3 scripts/reflow_md.py --check
```

**Exit codes:**
- `0`: All markdown prose in scope is reflowed (one logical unit per line)
- `1`: Hard-wrapped prose detected; the output names affected files

**Scope:** The check runs on `.md` files in tracked directories (`docs/`, `README.md`), excluding code blocks, tables, and YAML frontmatter. Files under `EXCLUDE_PREFIXES` (and the excluded globs) are skipped in every mode. The script treats the current working directory as the repo root, so run it from the repo root.

### Full-repo vs changed-only mode

| Command | Evaluates | Used by |
|---------|-----------|---------|
| `scripts/reflow_md.py --check` | Every in-scope markdown file in the repo | `make reflow-check` |
| `scripts/reflow_md.py --check --changed [--base REF]` | Only markdown the branch changed vs its base | `make quality` |

`--changed` exists so hard-wrapped prose already on the base branch cannot fail a clean PR that never touched that file. It selects files from the merge-base diff (`git diff --name-only --diff-filter=ACMR`, so added, copied, modified, and renamed files) plus staged, unstaged, and untracked markdown. The same in-scope filter and the same reflow engine are applied to that smaller set; nothing is reimplemented.

The base ref is `--base REF` when given. Otherwise, in a CI pull request, it is `origin/$GITHUB_BASE_REF`. Otherwise the first ref that resolves from `origin/slave`, `slave`, `origin/master`, `origin/main`, `master`, `main` is used, so a slave-based fleet PR is never charged for markdown slave changed since master. If no base resolves (for example a shallow CI checkout with no base ref), the script prints `warning: no base ref resolved; checking every markdown file` and falls back to the full scan, so it never passes silently.

A file you edit is checked in full: touching a hard-wrapped file means you must reflow it (`make reflow-docs`).

### Automatic reflow at commit time

Agents no longer need to run `make reflow-docs` by hand: the gateway `commit` path reflows changed markdown before it stages anything, so hard-wrapped prose never reaches the merge gate. The hook was placed in commit rather than `open_pr` so every commit is already clean and no fix-up commit is needed when the PR opens.

**Where it runs.** `GitService.commit` and `GitService.create_commit` (`roboco/services/git.py`) call `reflow_worktree_markdown` (`roboco/services/md_reflow.py`) right before `git add`. That helper lists the worktree's modified and untracked markdown (`git ls-files -m -o --exclude-standard -- '*.md'`). When the caller passed an explicit `files` list, only the listed paths are considered.

**What it reuses.** `roboco/services/md_reflow.py` loads `scripts/reflow_md.py` by path and calls its own `reflow()` and `in_scope()`, so the commit hook and `--check` cannot drift apart. Nothing is reimplemented; the engine stays pure text. File access goes only through `workspace_git.read_workspace_file` / `write_workspace_file`, and a markdown file whose real path leaves the clone (a planted symlink) is skipped, never written.

**Behavior guarantees.**
- Only changed markdown is touched; unchanged files and files outside `in_scope()` (including `EXCLUDE_PREFIXES` such as `docs/internal`) are left alone.
- A file whose reflowed text would not keep the same token sequence as the original is skipped, the same token-invariant safety the script applies.
- It is idempotent: a second run rewrites nothing.
- It is a no-op, not an error, when no markdown changed or the engine script is absent.
- It is best-effort: any failure is logged as `markdown reflow skipped` and the commit still proceeds.

**Packaging.** `docker/orchestrator.Dockerfile` copies `scripts/reflow_md.py` into the image (`/app/scripts`), because the hook runs inside the orchestrator and needs the engine at runtime.

**Limits.** The hook considers only worktree-modified or untracked files, so markdown that was already committed in an earlier step is not revisited. `make quality` (`--check --changed`) remains the authoritative gate.

Tests: `tests/unit/services/test_md_reflow.py` covers reflowing a changed hard-wrapped file (and that the check then passes), idempotency, the no-op case, ignoring unlisted and out-of-scope files, and the git hook's file filtering and never-raises behavior.

## Quality Gate Integration

The reflow check is **wired into `make quality`**, the Python test/lint/type-check gate that all merged PRs must pass:

```bash
make quality
```

This runs (among other checks):
1. `uv run ruff format --check .` — code formatting
2. `uv run ruff check .` — linting
3. `python3 scripts/reflow_md.py --check --changed` — markdown reflow, branch-changed files only
4. `uv run mypy roboco/ tests/` — type checking
5. `uv run pytest` — unit tests (DB-dependent, skipped in sandboxes)

A failure in *any* stage blocks the merge.

## Reflowed Files (Verification Task: 9c7bc11a)

The following three documentation files were reflowed to pass the quality gate:

| File | Commit | Task |
|------|--------|------|
| `docs/backend/api/video-engine-endpoints.md` | `18bc3247` | `99c3ed9c` |
| `docs/backend/migrations/video-project-scoping.md` | `18bc3247` | `99c3ed9c` |
| `docs/ux_ui/design/01-video-request-composition-controls.md` | `5435a2da` | `ccfe2015` |

Each file was reflowed by dedicated subtasks to eliminate all hard-wrapped prose. The diffs for these files are **whitespace-only** — no content, headings, code blocks, or tables were altered, only line breaks repositioned to match the one-logical-unit-per-line standard.

## Regression Test

To ensure the reflow-check wiring does not regress (e.g., if a future Makefile edit accidentally removes the check), a regression test was added:

**File:** `tests/unit/scripts/test_reflow_md.py`

**Tests:**
1. `test_quality_target_wires_in_reflow_check` — Verifies that `make quality` explicitly invokes `scripts/reflow_md.py --check`.
2. `test_check_passes_on_repo_as_committed` — Confirms the full-repo check exits 0 on HEAD.
3. `test_check_fails_on_a_hard_wrapped_file` — Verifies the check correctly rejects hard-wrapped prose.
4. `test_changed_mode_ignores_untouched_base_debt` — A hard-wrapped file untouched by the branch passes under `--changed`, while the full-repo check still fails on it.
5. `test_changed_mode_fails_on_changed_wrapped_file` — A new hard-wrapped file fails and is named in the output; untouched debt is not.
6. `test_changed_mode_fails_on_edited_tracked_file` — Editing a tracked file that is still wrapped fails.
7. `test_changed_mode_respects_exclude_prefixes` — Changed files under `EXCLUDE_PREFIXES` stay exempt.
8. `test_reflow_is_idempotent` — Reflowing already-reflowed text is a no-op.

Running `uv run pytest tests/unit/scripts/test_reflow_md.py` ensures the wiring remains intact.

## How to Verify

To verify that all three files are reflowed correctly:

```bash
# Run the reflow check on the entire repo
python3 scripts/reflow_md.py --check

# Expected output:
# OK: no hard-wrapped markdown prose in scope.
```

To check only what your branch changed (what `make quality` runs):

```bash
python3 scripts/reflow_md.py --check --changed
python3 scripts/reflow_md.py --check --changed --base origin/master
```

To see which files would be reflowed by the script (without modifying them), run it with no flags (dry run):

```bash
python3 scripts/reflow_md.py
```

To auto-reflow every in-scope file in place (`make reflow-docs`):

```bash
python3 scripts/reflow_md.py --apply
```

## Next Steps

- When editing `docs/backend/api/video-engine-endpoints.md`, `docs/backend/migrations/video-project-scoping.md`, or `docs/ux_ui/design/01-video-request-composition-controls.md`, ensure prose continues to follow the one-logical-unit-per-line standard.
- Before committing, run `python3 scripts/reflow_md.py --check --changed` to catch hard-wrapped prose in your own changes early; use `make reflow-check` for the full-repo audit.
- If a new documentation file is added, it will automatically be checked by the quality gate on the next PR.

## References

- Regression test: `tests/unit/scripts/test_reflow_md.py`
- Reflow script: `scripts/reflow_md.py`
- Quality gate: `Makefile` (see `quality` target)
