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

**Scope:** The check runs on `.md` files in tracked directories (`docs/`, `README.md`), excluding code blocks, tables, and YAML frontmatter.

## Quality Gate Integration

The reflow check is **wired into `make quality`**, the Python test/lint/type-check gate that all merged PRs must pass:

```bash
make quality
```

This runs (among other checks):
1. `uv run ruff format --check .` — code formatting
2. `uv run ruff check .` — linting
3. `python3 scripts/reflow_md.py --check` — markdown reflow
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
2. `test_check_passes_on_repo_as_committed` — Confirms the check exits 0 on HEAD.
3. `test_check_fails_on_a_hard_wrapped_file` — Verifies the check correctly rejects hard-wrapped prose.

Running `uv run pytest tests/unit/scripts/test_reflow_md.py` ensures the wiring remains intact.

## How to Verify

To verify that all three files are reflowed correctly:

```bash
# Run the reflow check on the entire repo
python3 scripts/reflow_md.py --check

# Expected output:
# OK: no hard-wrapped markdown prose in scope.
```

To see which files would be reflowed by the script (without modifying them):

```bash
python3 scripts/reflow_md.py --diff
```

To auto-reflow a file in-place:

```bash
python3 scripts/reflow_md.py --fix <file>
```

## Next Steps

- When editing `docs/backend/api/video-engine-endpoints.md`, `docs/backend/migrations/video-project-scoping.md`, or `docs/ux_ui/design/01-video-request-composition-controls.md`, ensure prose continues to follow the one-logical-unit-per-line standard.
- Before committing, run `python3 scripts/reflow_md.py --check` to catch hard-wrapped prose early.
- If a new documentation file is added, it will automatically be checked by the quality gate on the next PR.

## References

- Regression test: `tests/unit/scripts/test_reflow_md.py`
- Reflow script: `scripts/reflow_md.py`
- Quality gate: `Makefile` (see `quality` target)
