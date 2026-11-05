# Root-cause review: out-of-scope docs files on cell branches, and the Python gate over docs

Task 3ceca974 (research, no source changes). Findings are from reading the code on the current branch; no runtime reproduction.

## Summary

- `intends_to_touch` is **not enforced at claim or commit time**. It is declared at delegate time, used for sibling sequencing and collision display, and compared against real files only for an advisory "undeclared" drift flag shown to QA/PR-gate reviewers. Nothing refuses or unstages a file outside it.
- `commit` stages with `git add -A` whenever `files` is omitted, so anything in the worktree (docs a dev regenerated or touched incidentally) rides the branch.
- `ruff` does not lint or format `.md` files at all. The docs-related red gates come from a different tool (`scripts/reflow_md.py --check`, only in `make quality`) and from the whole-tree `ruff ... .` invocation, which has no docs-aware scoping and would hit any `.py` placed under a docs surface. The "ruff-format violation in a release-certificate doc" is therefore most likely either a Python snippet/script file in the doc change or a misattributed reflow failure; this needs confirming against that task's gate log.

## (a) Why out-of-scope files land on cell branches

### Commit path
- `roboco/services/gateway/content_actions.py` `ContentActions.commit` (content_actions.py:1216) forwards `files` to `GitService.commit_for_task` (git.py:1185), which calls `create_commit`.
- `roboco/services/git.py`: `GitService.create_commit` (def :830; `git add -A` at :853) and `GitService.commit` (def :6689; `git add -A` at :6734, which mirrors create_commit's staging) do `git add <file>` per file when `files` is given, else `git add -A`. There is no check of the staged set against the task's declared scope, and no filter on paths.
- `_commit_git_error_envelope` (content_actions.py:1288) only handles "files matched no modified paths".

### Scope field is advisory only
- Declared: `intends_to_touch` in `roboco/api/schemas/v1/flow.py`, `roboco/db/tables.py`, `roboco/foundation/policy/sequencing/models.py` (collision surface for `SequencingService.analyze`).
- Required at delegate for CODE subtasks only: `TASK_AT_DELEGATE` in `roboco/foundation/policy/task_completeness.py` (~:170) with `FieldRule.NON_EMPTY_LIST` — a completeness gate, not a file gate. Documentation/research tasks are exempt, so docs tasks never carry a scope at all.
- Consumed by: `roboco/services/sequencing.py` (dependency edges), `roboco/services/gateway/choreographer/collision.py` (`build_collision_context`, `_drift`, the `undeclared` list), and `_touch_globs` in `choreographer/_impl.py` (:148; used at :1456 and :3640 only as best-effort state for decision pilots).
- Drift is computed only as display: `collision.py:142-144` sets `entry["undeclared"]`; callers are `choreographer/qa.py:385`, `pr_gate.py:1236`, `_impl.py:1214`; surfaced by `roboco/api/routes/tasks.py:1014`. It never fails `i_am_done`, `pass_review` or `pr_pass`.
- Other gates are placement gates (conventions validator), not scope gates: they judge where a definition lives, not whether the file belongs to the task.

### Consequences
Anything a dev's tool run or editor touches under `docs/` (generated artifacts, a certificate doc, reflowed neighbours) is staged by `add -A`, pushed, and only a human/QA reviewer notices. Reverting then costs a full revision cycle (the $246 PR #950 case).

## (b) Why the Python gate runs over docs surfaces

- `Makefile` `quality` (ruff at :301-304, reflow check at :306), `quality-fast` (:358-359) and `gate` (:369-370; `quality-fast` is the first block, `gate` the second) all run `uv run ruff format --check .` and `uv run ruff check .` against the repo root. They are not path-scoped to `roboco/ tests/` like mypy/xenon/vulture are.
- `pyproject.toml` `[tool.ruff]` (table at :130; `src` at :132, `exclude` at :138; the other `exclude` lists at :473, :569, :610, :624, :631 belong to other tools): `src = ["src"]` (a stale path; the package is `roboco/`), `exclude = ["vulture_whitelist.py", ".venv", "alembic", ".decomp", ".worktrees"]` (`make lint` at Makefile:187-188 also runs ruff over `.`). There is no exclude for `docs/`, `panel/`, `motion/`, or generated trees, so every `.py` under any of them is linted and format-checked. `[tool.ruff.lint] select` is broad (E,W,F,I,B,C4,UP,ARG,SIM,TCH,PTH,PL,RUF). `per-file-ignores` only cover `roboco/` paths.
- Ruff itself only discovers `.py/.pyi/.ipynb`, so pure markdown cannot fail ruff. Markdown has its own gate: `scripts/reflow_md.py --check` (Makefile:306, `make quality` only), which scans every `*.md` under the root except `SKIP_DIRS`, `docs/internal/`, `agents/prompts/_generated/` and `docs/SPEC_*` (`EXCLUDE_PREFIXES`/`EXCLUDE_GLOBS`). A hard-wrapped doc (the openrouter-routing-ui.md reflow, $90) fails here, and `gate`/`quality-fast` do not run it, so the failure surfaces only at the full gate/CI after submission.
- `i_am_done` runs the project `quality_command` (`make gate`), which carries ruff over the whole tree but not the reflow check: docs problems are discovered late, a pattern that converts into rework rounds.

## Recommended fixes

### Leaf: gate scoping (Makefile + pyproject; be-dev-1)
1. Scope ruff to Python source roots: `ruff format --check roboco tests scripts` and `ruff check roboco tests scripts` (introduce a `PY_PATHS` Makefile variable used by `quality`, `quality-fast`, `gate`, `lint`), or add `docs`, `panel`, `motion` to `[tool.ruff] exclude` plus `extend-exclude` for generated trees. Fix `src = ["src"]` to `["."]`/`["roboco"]`.
2. Add `reflow-check` to `gate` and `quality-fast` (it is cheap and pure Python) so wrapped markdown is caught at the desk, matching what `quality` enforces. Keep `EXCLUDE_PREFIXES` as is.
3. Test: a unit test that runs `ruff check` over a temp `docs/x.py` with a lint error and asserts the scoped targets ignore it; keep `make gate` green.
4. Verify the actual root cause of the $119 release-certificate failure from that task's gate log before finalising (file type and which tool failed).

### Leaf: commit/claim scoping (gateway; be-dev-2)
1. In `GitService.create_commit` and the second staging site (`git.py:853` and `git.py:6734`), replace the `add -A` default with a scope-aware stage: compute the candidate set (`git status --porcelain`), compare against the task's `intends_to_touch` globs via the existing glob matcher in `collision.py` (`_globs_overlap`/`_drift` — extract a shared pure helper into `roboco/foundation/policy/sequencing/`), stage in-scope files, and return the out-of-scope paths in the `commit` envelope (`remediate`: pass `files=` explicitly or declare scope).
2. Start in warn mode (envelope field + journal), then promote to refuse for tasks with a non-empty `intends_to_touch`; tasks with no declared scope (docs/research) keep today's behaviour. Always allow task-agnostic exemptions (e.g. `.roboco/conventions.yml`).
3. Add a hard gate at `i_am_done`/`submit_up`: files from `list_changed_files` not matched by declared globs produce a convention-style finding (block for code tasks), reusing `collision._drift`, so scope drift is caught even if staged by other paths.
4. Require or default a scope for DOC tasks at delegate (`TASK_AT_DELEGATE`'s `when` currently covers `task_type == code` only), e.g. the doc paths the PM names.
5. Keep placement per `.roboco/conventions.yml`: the matcher in `foundation/policy` (pure), enforcement in `services/gateway/choreographer`/`GitService`, routes untouched. Tests: unit tests for the helper, plus a commit-adapter test staging an out-of-scope docs file.
